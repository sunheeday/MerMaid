"""실험 7 — 문맥 몸통: 그 사람의 과거 (상황, 반응) 토큰을 읽는 공통 모델. 차별 파라미터 0개.

    python3 ml/experiments/context_body.py --base-dataset ml/out/runs/v002/dataset.npz --fleet ml/out/bulk_world/fleet.npz \
        --cache ml/out/bulk_3head_stk/response [--k 32] [--steps 10000] [--lr 1e-3]

구조
  1층: FT 베이스 몸통(ft_base_response_1500.pt) 고정 → 행 하나를 32차원 벡터로. 모든 행을 미리 계산.
  2층: 작은 트랜스포머(d=64, 2층). 토큰 = [질문 행 벡터] + 문맥 k개 (그때 행 벡터 32 + 장치 원핫 3 + 켰나 1). 출력 3개.
학습: 베이스 세계에서 에피소드 — 사람 p, 탑승 r의 질문 행, 문맥은 r보다 앞선 탑승의 토큰(양성 = 켠 순간, 음성 = 자격 있는데 안 켠 순간).
채점: 플릿 세계. 질문 = 각 행, 문맥 = 그 사람의 앞선 탑승에서 최근 k개. 미세조정 없음.
  표 1: 뒤 10탑승(기존 플릿 실험과 같은 시험 행) — FT 공통(무문맥) / 문맥 모델(문맥 0개) / 문맥 모델(최근 k개)
  표 2: 앞선 탑승 수별 ROC (0 / 1~3 / 4~9 / 10+) — 전체 플릿 행
"""
import argparse
import pathlib
import sys
import time

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import bulk_pretrain_3head as B  # noqa: E402
from fleet_adapt import ft_predict  # noqa: E402
HEADS = B.HEADS; NH = 3


def embed_rows(torch, m, Z, P):
    m.eval(); out = []
    with torch.no_grad():
        for i in range(0, len(Z), 8192):
            cls, _ = m(torch.from_numpy(Z[i:i + 8192]), torch.from_numpy(P[i:i + 8192])); out.append(cls.numpy())
    return np.concatenate(out).astype(np.float32)


def embed_raw(torch, m, X, mu, sd, chunk=50_000):
    """원본 피처 → 32차원. 표준화를 덩어리로 해서 큰 임시 배열을 만들지 않는다."""
    out = np.zeros((len(X), 32), np.float32)
    for i in range(0, len(X), chunk):
        xi = X[i:i + chunk]; out[i:i + chunk] = embed_rows(torch, m, B.standardize(xi, mu, sd), np.isfinite(xi))
    return out


def build_tokens(Y, W, t, ride_id, person_id, ride_order, rng, neg_per_ride=2):
    """사람별 문맥 토큰 후보: (탑승 순서, 행 번호, 장치, 라벨). 양성 = 켠 순간(마지막 Y=1 행), 음성 = W>0 & Y=0 에서 표본."""
    tok = {}
    for r in np.unique(ride_id):
        idx = np.flatnonzero(ride_id == r); idx = idx[np.argsort(t[idx])]; p = int(person_id[idx[0]]); o = int(ride_order[idx[0]])
        L = tok.setdefault(p, [])
        for j in range(NH):
            pos = idx[Y[idx, j] == 1]
            if len(pos):
                L.append((o, int(pos[-1]), j, 1))
            neg = idx[(W[idx, j] > 0) & (Y[idx, j] == 0)]
            if len(neg):
                for i in rng.choice(neg, size=min(neg_per_ride, len(neg)), replace=False):
                    L.append((o, int(i), j, 0))
    return {p: np.array(sorted(L), dtype=np.int64).reshape(-1, 4) for p, L in tok.items()}


QUOTA = 3   # 헤드별 양성·음성 각각 최소 보장 개수


def select_tokens(cand, k, rng, recent):
    """헤드별 양성 QUOTA개 + 음성 QUOTA개를 먼저 보장(최근 또는 무작위), 남는 자리는 나머지에서 최근/무작위로."""
    pick = np.zeros(len(cand), bool)
    for j in range(NH):
        for lab in (1, 0):
            idx = np.flatnonzero((cand[:, 2] == j) & (cand[:, 3] == lab))
            if len(idx) > QUOTA:
                idx = idx[-QUOTA:] if recent else rng.choice(idx, QUOTA, replace=False)
            pick[idx] = True
    rest = np.flatnonzero(~pick); room = k - int(pick.sum())
    if room > 0 and len(rest):
        if len(rest) > room:
            rest = rest[-room:] if recent else rng.choice(rest, room, replace=False)
        pick[rest] = True
    elif room < 0:                      # 보장분만으로 k 를 넘으면 최근/무작위로 k 개만
        idx = np.flatnonzero(pick); idx = idx[-k:] if recent else np.sort(rng.choice(idx, k, replace=False)); pick[:] = False; pick[idx] = True
    return cand[pick]


def make_context(E, tok_p, order_now, k, rng, recent):
    """문맥 행렬 (k, 36)과 마스크(k,) — True = 빈 칸. recent=True 면 최근 k개, 아니면 앞선 토큰 중 무작위 k개."""
    C = np.zeros((k, E.shape[1] + NH + 1), np.float32); mask = np.ones(k, bool)
    if tok_p is None or len(tok_p) == 0:
        return C, mask
    cand = tok_p[tok_p[:, 0] < order_now]
    if len(cand) == 0:
        return C, mask
    if len(cand) > k:
        cand = select_tokens(cand, k, rng, recent)
    n = len(cand); C[:n, :E.shape[1]] = E[cand[:, 1]]; C[np.arange(n), E.shape[1] + cand[:, 2]] = 1.0; C[:n, -1] = cand[:, 3]; mask[:n] = False
    return C, mask


def make_net(torch, de, d=64, layers=2, heads=4, dropout=0.1):
    nn = torch.nn

    class Ctx(nn.Module):
        def __init__(self):
            super().__init__()
            self.pq = nn.Linear(de, d); self.pc = nn.Linear(de + NH + 1, d); self.tok_type = nn.Parameter(torch.zeros(2, d))
            self.enc = nn.TransformerEncoder(nn.TransformerEncoderLayer(d, heads, dim_feedforward=2 * d, dropout=dropout, batch_first=True), layers)
            self.head = nn.Linear(d, NH)

        def forward(self, q, c, mask):           # q (b, de), c (b, k, de+4), mask (b, k)
            tq = self.pq(q).unsqueeze(1) + self.tok_type[0]; tc = self.pc(c) + self.tok_type[1]
            x = torch.cat([tq, tc], 1); pad = torch.cat([torch.zeros(mask.shape[0], 1, dtype=torch.bool), mask], 1)
            return self.head(self.enc(x, src_key_padding_mask=pad)[:, 0])
    return Ctx()


def predict_ctx(torch, net, E, qidx, tok, pid, order, k, rng, recent=True, empty=False):
    net.eval(); out = np.zeros((len(qidx), NH), np.float32)
    with torch.no_grad():
        for s in range(0, len(qidx), 2048):
            ii = qidx[s:s + 2048]; Cs, Ms = [], []
            for i in ii:
                C, M = make_context(E, None if empty else tok.get(int(pid[i])), int(order[i]), k, rng, recent); Cs.append(C); Ms.append(M)
            out[s:s + len(ii)] = torch.sigmoid(net(torch.from_numpy(E[ii]), torch.from_numpy(np.stack(Cs)), torch.from_numpy(np.stack(Ms)))).numpy()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-dataset", required=True); ap.add_argument("--fleet", required=True); ap.add_argument("--cache", required=True)
    ap.add_argument("--k", type=int, default=32); ap.add_argument("--steps", type=int, default=10000); ap.add_argument("--lr", type=float, default=1e-3); ap.add_argument("--adapt-trips", type=int, default=20)
    ap.add_argument("--seed", type=int, default=0); ap.add_argument("--out", default=None)
    ap.add_argument("--extra-base", nargs="*", default=[], help="베이스 설정으로 더 만든 사람들 (make_log_world --same-world 출력). 학습 사람 수 늘리기")
    ap.add_argument("--val-persons", type=int, default=60, help="조기 종료용으로 떼어 둘 학습 사람 수")
    ap.add_argument("--extra-persons", type=int, default=None, help="추가 세계 파일마다 이 수만큼의 사람만 쓴다 (메모리)")
    ap.add_argument("--dropout", type=float, default=0.2); ap.add_argument("--wd", type=float, default=1e-3)
    a = ap.parse_args()
    from sklearn.metrics import roc_auc_score
    torch, FT = B.make_torch(); torch.manual_seed(a.seed); rng = np.random.default_rng(a.seed)
    cache = pathlib.Path(a.cache); out = pathlib.Path(a.out or cache.parent / "context"); out.mkdir(parents=True, exist_ok=True); T0 = time.time()
    logf = open(out / "log.txt", "w")
    def log(s): print(s, flush=True); logf.write(s + "\n"); logf.flush()

    # ---- 베이스 세계 (학습)
    m0 = FT(B.NF); m0.load_state_dict(torch.load(cache / "ft_base_response_1500.pt"))
    mu = sd = None

    def load_world(fp, label_seed, max_persons=None):
        """파일 하나 → 32차원 벡터·라벨·시각·탑승·사람·trip 만 남기고 원본 피처는 버린다 (메모리)."""
        nonlocal mu, sd
        dz = np.load(fp, allow_pickle=False); pw_ = dz["person_id"].astype(np.int64); keep = np.ones(len(pw_), bool)
        if max_persons is not None and len(np.unique(pw_)) > max_persons:          # 사람 일부만
            keep = np.isin(pw_, np.random.default_rng(label_seed).choice(np.unique(pw_), max_persons, replace=False))
        Xw = dz["X"][keep][:, B.ENABLED].astype(np.float32)
        if mu is None:                                           # 표준화 통계는 첫 파일(원래 베이스 세계)로
            mu = np.nan_to_num(np.nanmean(Xw, 0)); sd = np.nanstd(Xw, 0); sd[~np.isfinite(sd) | (sd < 1e-6)] = 1.0
        tw, rw, trw = dz["t"][keep].astype(float), dz["ride_id"][keep].astype(np.int64), dz["trip_id"][keep].astype(np.int64); misc = dz["misc"][keep].astype(float); pw_ = pw_[keep]; del dz
        Yw, Ww, _ = B.make_response_labels(Xw, misc, tw, rw, pw_, np.random.default_rng(label_seed)); del misc
        Ew = embed_raw(torch, m0, Xw, mu, sd); del Xw
        return Ew, Yw, Ww, tw, rw, pw_, trw

    EB, YB, WB, tB, ridB, pidB, tripB = load_world(a.base_dataset, 11)
    for n, fp in enumerate(a.extra_base):                        # 사람 추가 (라벨 시드는 따로, 원래 120명 라벨은 그대로)
        Ew, Yw, Ww, tw, rw, pw_, trw = load_world(fp, 12 + n, a.extra_persons)
        pw_ = pw_ + (0 if pw_.min() >= 10_000 else 100_000 * (n + 1))
        EB = np.vstack([EB, Ew]); YB = np.vstack([YB, Yw]); WB = np.vstack([WB, Ww]); tB = np.concatenate([tB, tw])
        ridB = np.concatenate([ridB, rw + ridB.max() + 1]); tripB = np.concatenate([tripB, trw + tripB.max() + 1]); pidB = np.concatenate([pidB, pw_])
        del Ew, Yw, Ww
        log(f"  추가 세계 {pathlib.Path(fp).name}: 누적 {len(EB)}행 ({time.time() - T0:.0f}s)")
    # 사람 안에서 탑승 순서 = trip_id 순
    orderB = np.zeros(len(tB), np.int64)
    for p in np.unique(pidB):
        m = pidB == p; trips = np.unique(tripB[m]); rank = {tr: i for i, tr in enumerate(trips)}; orderB[m] = [rank[tr] for tr in tripB[m]]
    tokB = build_tokens(YB, WB, tB, ridB, pidB, orderB, rng)
    persons = np.unique(pidB); val_p = rng.choice(persons, size=min(a.val_persons, len(persons) // 5), replace=False)
    is_val = np.isin(pidB, val_p); val_rows = np.flatnonzero(is_val & (WB.max(1) > 0)); val_rows = rng.choice(val_rows, size=min(30000, len(val_rows)), replace=False)
    log(f"베이스 세계 {len(EB)}행, 사람 {len(tokB)} (검증 {len(val_p)}명), 사람당 탑승 평균 {np.mean([orderB[pidB == p].max() + 1 for p in tokB]):.1f}, 문맥 토큰 {sum(len(v) for v in tokB.values())}개 ({time.time() - T0:.0f}s)")

    # ---- 플릿 세계 (채점)
    d = np.load(a.fleet, allow_pickle=False); X = d["X"][:, B.ENABLED].astype(np.float32)
    t, rid, pid, veh, order = d["t"].astype(float), d["ride_id"], d["person_id"], d["vehicle_id"], d["trip_order"].astype(np.int64)
    Y, W, _ = B.make_response_labels(X, d["misc"].astype(float), t, rid, pid, np.random.default_rng(1))
    Z, P = B.standardize(X, mu, sd), np.isfinite(X); E = embed_raw(torch, m0, X, mu, sd)
    # 사람 안에서 앞선 탑승 수 (가족 중 그 탑승에 안 탄 날도 있으므로 사람 기준으로 다시 셈)
    prior = np.zeros(len(t), np.int64)
    for p in np.unique(pid):
        m = pid == p; trips = np.unique(order[m]); rank = {o: i for i, o in enumerate(trips)}; prior[m] = [rank[o] for o in order[m]]
    tok = build_tokens(Y, W, t, rid, pid, prior, rng)     # 문맥 후보는 '사람 기준 앞선 탑승' 으로
    log(f"플릿 {len(np.unique(veh))}대, 행 {len(X)}, 사람 {len(tok)} ({time.time() - T0:.0f}s)")

    # ---- 2층 학습 (에피소드)
    net = make_net(torch, EB.shape[1], dropout=a.dropout); opt = torch.optim.AdamW(net.parameters(), lr=a.lr, weight_decay=a.wd)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=a.steps, eta_min=a.lr / 20)
    elig = np.flatnonzero((WB.max(1) > 0) & ~is_val); pos = (YB * WB).sum(0) / np.maximum(WB.sum(0), 1); pw = torch.tensor(((1 - pos) / np.maximum(pos, 1e-3)).astype(np.float32))
    net.train(); best = (-1.0, 0, None)
    for s in range(a.steps):
        ii = rng.choice(elig, 128); Cs, Ms = [], []
        for i in ii:
            kk = int(rng.integers(0, a.k + 1))                                  # 문맥 길이도 섞어서 (0개 포함)
            C, M = make_context(EB, tokB.get(int(pidB[i])), int(orderB[i]), a.k, rng, recent=False)
            if kk < a.k:
                keep = np.flatnonzero(~M)
                if len(keep) > kk:
                    drop = rng.choice(keep, len(keep) - kk, replace=False); M[drop] = True; C[drop] = 0
            Cs.append(C); Ms.append(M)
        logit = net(torch.from_numpy(EB[ii]), torch.from_numpy(np.stack(Cs)), torch.from_numpy(np.stack(Ms)))
        y = torch.from_numpy(YB[ii].astype(np.float32)); w = torch.from_numpy(WB[ii])
        bce = torch.nn.functional.binary_cross_entropy_with_logits(logit, y, pos_weight=pw, reduction="none")
        loss = (bce * w).sum() / w.sum().clamp(min=1.0); opt.zero_grad(); loss.backward(); opt.step(); sched.step()
        if (s + 1) % 1000 == 0:
            pv = predict_ctx(torch, net, EB, val_rows, tokB, pidB, orderB, a.k, rng, recent=True); net.train()
            score = np.mean([roc_auc_score(YB[val_rows, j][WB[val_rows, j] > 0], pv[WB[val_rows, j] > 0, j]) for j in range(NH)])
            if score > best[0]:
                best = (score, s + 1, {k_: v.clone() for k_, v in net.state_dict().items()})
            log(f"  스텝 {s + 1} 손실 {loss.item():.3f} · 검증 사람 ROC 평균 {score:.3f} (최고 {best[0]:.3f} @ {best[1]}) ({time.time() - T0:.0f}s)")
    net.load_state_dict(best[2]); log(f"조기 종료: 스텝 {best[1]} 가중치 사용 (검증 {best[0]:.3f})")
    torch.save(net.state_dict(), out / f"ctx_net_k{a.k}_{a.steps}.pt")

    # ---- 채점
    te = order >= a.adapt_trips; qi = np.flatnonzero(te)
    p_common = ft_predict(torch, m0, Z[te], P[te])
    p_empty = predict_ctx(torch, net, E, qi, tok, pid, prior, a.k, rng, empty=True)
    p_ctx = predict_ctx(torch, net, E, qi, tok, pid, prior, a.k, rng, recent=True)
    log(f"채점 끝 ({time.time() - T0:.0f}s)")
    Yt, Wt, Vt = Y[te], W[te], veh[te]; vehicles = np.unique(veh)
    def cells(p):
        c = []
        for j in range(NH):
            k = Wt[:, j] > 0; fl = roc_auc_score(Yt[k, j], p[k, j])
            pv = np.mean([roc_auc_score(Yt[kv, j], p[kv, j]) for v in vehicles if (kv := k & (Vt == v)).any() and Yt[kv, j].min() != Yt[kv, j].max()])
            c.append(f"{fl:.3f} / {pv:.3f}")
        return c
    M = [f"# 문맥 몸통 실험 — 베이스 세계 학습 → 플릿 20대 (k={a.k}, 최대 {a.steps}스텝, 조기 종료 @ {best[1]}, 학습 사람 {len(persons) - len(val_p)}명, 차별 파라미터 0)", "",
         "## 표 1 · 뒤 10탑승 시험 행 (플릿 ROC / 차별 평균)", "", "| 모델 | " + " | ".join(HEADS) + " |", "|---|---|---|---|",
         "| FT 공통 (무문맥) | " + " | ".join(cells(p_common)) + " |",
         "| 문맥 모델 · 문맥 0개 | " + " | ".join(cells(p_empty)) + " |",
         f"| 문맥 모델 · 최근 {a.k}개 | " + " | ".join(cells(p_ctx)) + " |", "",
         "비교 (fleet 실험, 차별 미세조정): FT 머리+마지막 층 0.852 / 0.895 / 0.834 · GBM 차별 이어학습 0.837 / 0.860 / 0.852", ""]
    # 표 2: 앞선 탑승 수별 — 전체 플릿 행
    qa = np.arange(len(X)); p_all = predict_ctx(torch, net, E, qa, tok, pid, prior, a.k, rng, recent=True); p_all_c = ft_predict(torch, m0, Z, P)
    buckets = [("0", prior == 0), ("1~3", (prior >= 1) & (prior <= 3)), ("4~9", (prior >= 4) & (prior <= 9)), ("10+", prior >= 10)]
    M += ["## 표 2 · 그 사람의 앞선 탑승 수별 (전체 플릿 행, 플릿 ROC)", "", "| 앞선 탑승 | 행 수 | " + " | ".join(f"{h} 공통 → 문맥" for h in HEADS) + " |", "|---|---|---|---|---|"]
    for name, bm in buckets:
        c = []
        for j in range(NH):
            k = bm & (W[:, j] > 0)
            c.append(f"{roc_auc_score(Y[k, j], p_all_c[k, j]):.3f} → {roc_auc_score(Y[k, j], p_all[k, j]):.3f}" if Y[k, j].min() != Y[k, j].max() else "-")
        M.append(f"| {name} | {int(bm.sum())} | " + " | ".join(c) + " |")
    M += ["", f"총 {time.time() - T0:.0f}초"]
    txt = "\n".join(M); log("\n" + txt); (out / "summary.md").write_text(txt + "\n")


if __name__ == "__main__":
    main()
