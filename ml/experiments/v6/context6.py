"""축 B/C — 문맥 모델 (1단계 요약 → 2단계가 그 사람의 과거 k개를 읽음), v6 세계판.

    python3 ml/experiments/v6/context6.py --base ml/out/v6/base_weak.npz --fleet ml/out/v6/fleet_weak.npz --cache ml/out/v6/ctx_cache_weak \
        --stage1 ft --stage2 attn --k 128 [--input v6|v5] [--steps 10000] [--seed 0] [--d2 64] [--tag X] [--body ml/out/v6/axis_a_weak/ft_v6_s0.pt]

1단계 (--stage1)  ft: 피처 토큰 트랜스포머 몸통 → 32   mlp: [표준화 ‖ 마스크] → 128 → 64 → 32   linear: 몸통 없이 [표준화 ‖ 마스크] 그대로
   몸통은 베이스 세계 반응 라벨로 학습해 동결 (캐시 폴더에 저장, --body 로 축 A 가 학습한 것을 줄 수도 있음)
2단계 (--stage2)  attn: 지금이 질의가 되어 과거 k개를 고름   gru: 과거를 순서대로 상태 하나에   gru_qfirst: 지금을 먼저 넣고 과거
                  mean: 과거의 마스크 평균   mean_dev: 장치별 마스크 평균 3개 (질의 X, 순서 X, 장치 구분 O)
진단 (모두 같은 모델, 문맥만 바꿈)  empty: 문맥 0개   swap: 다른 사람의 이력   nolabel: '켰나' 비트 제거
                  nosit: 과거 토큰에서 상황 요약을 지우고 장치·결과만 남김 ← "상황이 닮은 과거를 골라 읽는가" (2단계 어텐션의 존재 이유)
채점  플릿 세계, 각 차 trip_order ≥ --adapt-trips 행. 문맥 = 그 사람의 앞선 탑승(ride_order 기준) 토큰 중 최근 k개. 미세조정 없음.
출력  <cache 의 부모>/context_<input>_<stage1>-<stage2>[-tag]_k<k>_s<seed>/{summary.md, oneline.csv, per_car.csv, log.txt, 가중치}
"""
import argparse
import pathlib
import sys
import time

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import common6 as C6  # noqa: E402
HEADS = C6.HEADS; NH = 3
QUOTA = 3


def build_tokens(Y, W, t, ride_id, person_id, ride_order, rng, neg_per_ride=2):
    """사람별 문맥 토큰 후보 (탑승 순서, 행 번호, 장치, 라벨). 양성 = 켠 순간(마지막 Y=1 행), 음성 = 자격 있는데 안 켠 순간 표본."""
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


def select_tokens(cand, k, rng, recent):
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
    elif room < 0:
        idx = np.flatnonzero(pick); idx = idx[-k:] if recent else np.sort(rng.choice(idx, k, replace=False)); pick[:] = False; pick[idx] = True
    return cand[pick]


def make_context(E, tok_p, order_now, k, rng, recent):
    de = E.shape[1]; C = np.zeros((k, de + NH + 1), np.float32); mask = np.ones(k, bool)
    if tok_p is None or len(tok_p) == 0:
        return C, mask
    cand = tok_p[tok_p[:, 0] < order_now]
    if len(cand) == 0:
        return C, mask
    if len(cand) > k:
        cand = select_tokens(cand, k, rng, recent)
    n = len(cand); C[:n, :de] = E[cand[:, 1]]; C[np.arange(n), de + cand[:, 2]] = 1.0; C[:n, -1] = cand[:, 3]; mask[:n] = False
    return C, mask


def make_net(torch, de, d=64, layers=2, heads=4, dropout=0.1, stage2="attn"):
    nn = torch.nn

    class Attn(nn.Module):
        def __init__(self):
            super().__init__()
            self.pq = nn.Linear(de, d); self.pc = nn.Linear(de + NH + 1, d); self.tok_type = nn.Parameter(torch.zeros(2, d))
            self.enc = nn.TransformerEncoder(nn.TransformerEncoderLayer(d, heads, dim_feedforward=2 * d, dropout=dropout, batch_first=True), layers)
            self.head = nn.Linear(d, NH)

        def forward(self, q, c, mask):
            tq = self.pq(q).unsqueeze(1) + self.tok_type[0]; tc = self.pc(c) + self.tok_type[1]
            x = torch.cat([tq, tc], 1); pad = torch.cat([torch.zeros(mask.shape[0], 1, dtype=torch.bool, device=mask.device), mask], 1)
            return self.head(self.enc(x, src_key_padding_mask=pad)[:, 0])

    class Gru(nn.Module):
        def __init__(self, qfirst):
            super().__init__(); self.qfirst = qfirst
            self.pq = nn.Linear(de, d); self.pc = nn.Linear(de + NH + 1, d); self.drop = nn.Dropout(dropout)
            self.gru = nn.GRU(d, d, num_layers=1, batch_first=True)
            self.head = nn.Sequential(nn.Linear(2 * d if not qfirst else d, d), nn.GELU(), nn.Dropout(dropout), nn.Linear(d, NH))

        def forward(self, q, c, mask):
            tq = self.pq(q); tc = self.drop(self.pc(c)) * (~mask).unsqueeze(-1).float(); n = (~mask).sum(1)
            if self.qfirst:
                out, _ = self.gru(torch.cat([tq.unsqueeze(1), tc], 1)); return self.head(out[torch.arange(len(n)), n])
            out, _ = self.gru(tc)
            h = torch.where((n > 0).unsqueeze(-1), out[torch.arange(len(n)), (n - 1).clamp(min=0)], torch.zeros_like(tq))
            return self.head(torch.cat([tq, h], 1))

    class Mean(nn.Module):
        def __init__(self, per_device):
            super().__init__(); self.per_device = per_device
            self.pq = nn.Linear(de, d); self.pc = nn.Linear(de + NH + 1, d); self.drop = nn.Dropout(dropout)
            self.head = nn.Sequential(nn.Linear((1 + (NH if per_device else 1)) * d, d), nn.GELU(), nn.Dropout(dropout), nn.Linear(d, NH))

        def forward(self, q, c, mask):
            keep = (~mask).unsqueeze(-1).float(); tc = self.drop(self.pc(c)) * keep
            if self.per_device:
                hs = []
                for j in range(NH):
                    kj = keep * c[:, :, de + j:de + j + 1]; hs.append((tc * kj).sum(1) / kj.sum(1).clamp(min=1.0))
                return self.head(torch.cat([self.pq(q)] + hs, 1))
            return self.head(torch.cat([self.pq(q), tc.sum(1) / keep.sum(1).clamp(min=1.0)], 1))

    return {"attn": lambda: Attn(), "gru": lambda: Gru(False), "gru_qfirst": lambda: Gru(True), "mean": lambda: Mean(False), "mean_dev": lambda: Mean(True)}[stage2]()


def predict_ctx(torch, net, E, qidx, tok, pid, order, k, rng, recent=True, empty=False, swap=None, strip_label=False, strip_sit=False):
    net.eval(); out = np.zeros((len(qidx), NH), np.float32); de = E.shape[1]; dev = next(net.parameters()).device
    with torch.no_grad():
        for s in range(0, len(qidx), 2048):
            ii = qidx[s:s + 2048]; Cs, Ms = [], []
            for i in ii:
                if swap is not None:
                    C, M = make_context(E, tok.get(int(swap[int(pid[i])])), 10 ** 9, k, rng, recent)
                else:
                    C, M = make_context(E, None if empty else tok.get(int(pid[i])), int(order[i]), k, rng, recent)
                if strip_label:
                    C[:, -1] = 0.0
                if strip_sit:
                    C[:, :de] = 0.0
                Cs.append(C); Ms.append(M)
            out[s:s + len(ii)] = torch.sigmoid(net(torch.from_numpy(E[ii].astype(np.float32)).to(dev), torch.from_numpy(np.stack(Cs)).to(dev), torch.from_numpy(np.stack(Ms)).to(dev))).cpu().numpy()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True); ap.add_argument("--fleet", required=True); ap.add_argument("--cache", required=True)
    ap.add_argument("--input", default="v6", help="1단계 입력 집합 (v6 / v5 …, 축 C 는 v5)")
    ap.add_argument("--stage1", choices=["ft", "mlp", "linear"], default="ft"); ap.add_argument("--stage2", choices=["attn", "gru", "gru_qfirst", "mean", "mean_dev"], default="attn")
    ap.add_argument("--body", default=None, help="학습된 1단계 몸통 .pt (예: 축 A 의 ft_v6_s0.pt). 없으면 캐시에 학습")
    ap.add_argument("--body-steps", type=int, default=4000)
    ap.add_argument("--k", type=int, default=128); ap.add_argument("--steps", type=int, default=10000); ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--d2", type=int, default=64); ap.add_argument("--dropout", type=float, default=0.2); ap.add_argument("--wd", type=float, default=1e-3)
    ap.add_argument("--seed", type=int, default=0); ap.add_argument("--tag", default=""); ap.add_argument("--adapt-trips", type=int, default=24)
    ap.add_argument("--val-persons", type=int, default=60); ap.add_argument("--threads", type=int, default=4); ap.add_argument("--out", default=None)
    ap.add_argument("--device", default="cpu", help="cpu | cuda  (2단계 학습·채점만 GPU, 1단계 몸통은 CPU)")
    a = ap.parse_args()
    from sklearn.metrics import roc_auc_score
    torch, NETS = C6.make_torch(); torch.set_num_threads(a.threads); torch.manual_seed(a.seed); rng = np.random.default_rng(a.seed)
    cache = pathlib.Path(a.cache); cache.mkdir(parents=True, exist_ok=True)
    out = pathlib.Path(a.out or cache.parent / f"context_{a.input}_{a.stage1}-{a.stage2}{('-' + a.tag) if a.tag else ''}_k{a.k}_s{a.seed}"); out.mkdir(parents=True, exist_ok=True)
    T = C6.Timer(); logf = open(out / "log.txt", "w")
    def log(s): print(s, flush=True); logf.write(s + "\n"); logf.flush()
    cols = C6.input_cols(a.input)

    # ---- 베이스 세계와 1단계 몸통
    base = C6.load_world(a.base); Xb = base["X"][:, cols]; YB, WB, tB, ridB, pidB, orderB = base["Y"], base["W"], base["t"], base["ride_id"], base["person_id"], base["ride_order"]
    is_val = C6.person_val_split(pidB, a.val_persons, 0)
    mu, sd = C6.stats(Xb[~is_val])
    body_name = f"{a.stage1}_{a.input}"; m0 = None
    if a.stage1 != "linear":
        fp = pathlib.Path(a.body) if a.body else cache / f"body_{body_name}.pt"; m0 = NETS[a.stage1](len(cols))
        if fp.exists():
            m0.load_state_dict(torch.load(fp)); m0.eval(); log(f"1단계 몸통 재사용 {fp}")
        else:
            Z, P = C6.standardize(Xb[~is_val], mu, sd), np.isfinite(Xb[~is_val]); Zv, Pv = C6.standardize(Xb[is_val], mu, sd), np.isfinite(Xb[is_val])
            m0, bs = C6.train_net(torch, m0, Z, P, YB[~is_val], WB[~is_val], steps=a.body_steps, seed=0, val=(Zv, Pv, YB[is_val], WB[is_val]), log=log)
            torch.save(m0.state_dict(), fp); log(f"1단계 몸통 학습 끝 (최고 스텝 {bs}, {T():.0f}s)"); del Z, P, Zv, Pv

    def embed(X, tag):
        ck = cache / f"E_{body_name}_{tag}.npy"
        if ck.exists():
            return np.load(ck)
        if m0 is None:
            E = np.hstack([C6.standardize(X, mu, sd), np.isfinite(X).astype(np.float16)]).astype(np.float16)
        else:
            E = np.concatenate([C6.net_embed(torch, m0, C6.standardize(X[i:i + 50000], mu, sd), np.isfinite(X[i:i + 50000])) for i in range(0, len(X), 50000)])
        np.save(ck, E); return E
    EB = embed(Xb, pathlib.Path(a.base).stem); del Xb
    tokB = build_tokens(YB, WB, tB, ridB, pidB, orderB, rng)
    val_rows = np.flatnonzero(is_val & (WB.max(1) > 0)); val_rows = rng.choice(val_rows, size=min(30000, len(val_rows)), replace=False)
    log(f"베이스 {len(EB)}행, 사람 {len(tokB)} (검증 {len(np.unique(pidB[is_val]))}명), 문맥 토큰 {sum(len(v) for v in tokB.values())}개, 요약 {EB.shape[1]}차원 ({T():.0f}s)")

    # ---- 플릿 세계
    fleet = C6.load_world(a.fleet); X = fleet["X"][:, cols]; Y, W, t, rid, pid, veh, prior, order = fleet["Y"], fleet["W"], fleet["t"], fleet["ride_id"], fleet["person_id"], fleet["vehicle_id"], fleet["ride_order"], fleet["trip_order"]
    E = embed(X, pathlib.Path(a.fleet).stem); Z, P = C6.standardize(X, mu, sd), np.isfinite(X)
    tok = build_tokens(Y, W, t, rid, pid, prior, rng)
    log(f"플릿 {len(np.unique(veh))}대, 행 {len(X)}, 사람 {len(tok)} ({T():.0f}s)")

    # ---- 2단계 학습
    dev = torch.device(a.device if a.device == "cpu" or torch.cuda.is_available() else "cpu"); log(f"2단계 장치 {dev}" + ("" if str(dev) == a.device else f" (요청 {a.device} 불가)"))
    net = make_net(torch, EB.shape[1], d=a.d2, dropout=a.dropout, stage2=a.stage2).to(dev); opt = torch.optim.AdamW(net.parameters(), lr=a.lr, weight_decay=a.wd)
    n_par = sum(p_.numel() for p_ in net.parameters()); log(f"2단계 = {a.stage2}, k = {a.k}, 파라미터 {n_par:,}개")
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=a.steps, eta_min=a.lr / 20)
    elig = np.flatnonzero((WB.max(1) > 0) & ~is_val); pw = C6.pos_weight(torch, YB, WB).to(dev)
    net.train(); best = (-1.0, 0, None); t_train = T()
    for s in range(a.steps):
        ii = rng.choice(elig, 128); Cs, Ms = [], []
        for i in ii:
            kk = int(rng.integers(0, a.k + 1))
            C, M = make_context(EB, tokB.get(int(pidB[i])), int(orderB[i]), a.k, rng, recent=False)
            if kk < a.k:
                keep = np.flatnonzero(~M)
                if len(keep) > kk:
                    drop = rng.choice(keep, len(keep) - kk, replace=False); M[drop] = True; C[drop] = 0
            Cs.append(C); Ms.append(M)
        logit = net(torch.from_numpy(EB[ii].astype(np.float32)).to(dev), torch.from_numpy(np.stack(Cs)).to(dev), torch.from_numpy(np.stack(Ms)).to(dev))
        y = torch.from_numpy(YB[ii].astype(np.float32)).to(dev); w = torch.from_numpy(WB[ii]).to(dev)
        bce = torch.nn.functional.binary_cross_entropy_with_logits(logit, y, pos_weight=pw, reduction="none")
        loss = (bce * w).sum() / w.sum().clamp(min=1.0); opt.zero_grad(); loss.backward(); opt.step(); sched.step()
        if (s + 1) % 1000 == 0:
            pv = predict_ctx(torch, net, EB, val_rows, tokB, pidB, orderB, a.k, rng); net.train()
            score = np.mean([roc_auc_score(YB[val_rows, j][WB[val_rows, j] > 0], pv[WB[val_rows, j] > 0, j]) for j in range(NH)])
            if score > best[0]:
                best = (score, s + 1, {k_: v.detach().cpu().clone() for k_, v in net.state_dict().items()})
            log(f"  스텝 {s + 1} 손실 {loss.item():.3f} · 검증 ROC {score:.3f} (최고 {best[0]:.3f} @ {best[1]}) ({T():.0f}s)")
    t_train = T() - t_train
    if best[2] is None:  # --steps 가 검증 주기보다 짧으면 마지막 가중치
        best = (float("nan"), a.steps, {k_: v.detach().cpu().clone() for k_, v in net.state_dict().items()})
    net.load_state_dict(best[2]); torch.save(best[2], out / f"ctx_{a.stage2}_k{a.k}.pt"); log(f"조기 종료: 스텝 {best[1]} (검증 {best[0]:.3f}), 학습 {t_train / 60:.1f}분")

    # ---- 채점
    te = order >= a.adapt_trips; qi = np.flatnonzero(te); Yt, Wt, Vt = Y[te], W[te], veh[te]
    preds = {}
    preds["common"] = C6.net_predict(torch, m0, Z[te], P[te]) if m0 is not None else None
    preds["empty"] = predict_ctx(torch, net, E, qi, tok, pid, prior, a.k, rng, empty=True)
    T1 = time.time(); preds["ctx"] = predict_ctx(torch, net, E, qi, tok, pid, prior, a.k, rng); t_row = (time.time() - T1) / max(len(qi), 1) * 1e3
    persons_f = np.unique(pid); perm = np.random.default_rng(99).permutation(persons_f); swap = {int(p_): int(q_) for p_, q_ in zip(persons_f, perm)}
    for i_, p_ in enumerate(persons_f):
        if swap[int(p_)] == int(p_):
            swap[int(p_)] = int(persons_f[(i_ + 1) % len(persons_f)])
    preds["swap"] = predict_ctx(torch, net, E, qi, tok, pid, prior, a.k, rng, swap=swap)
    preds["nolabel"] = predict_ctx(torch, net, E, qi, tok, pid, prior, a.k, rng, strip_label=True)
    preds["nosit"] = predict_ctx(torch, net, E, qi, tok, pid, prior, a.k, rng, strip_sit=True)
    log(f"채점 끝 ({T():.0f}s) · 2단계 추론 {t_row:.3f} ms/행")
    # 임계값 고르기용 (export_ctx / pick_thresholds_ctx.py): 채점 행의 문맥 확률과 라벨
    np.savez_compressed(out / "fleet_preds.npz", p=preds["ctx"], p_empty=preds["empty"], Y=Yt, W=Wt, vehicle=Vt,
                        ride_id=rid[te], person_id=pid[te], t=t[te])
    cells = {n: C6.roc_cells(p, Yt, Wt, Vt) for n, p in preds.items() if p is not None}
    names = {"common": f"1단계 공통 ({a.stage1}, 무문맥)", "empty": "문맥 모델 · 문맥 0개", "ctx": f"문맥 모델({a.stage2}) · 최근 {a.k}개",
             "swap": "진단 · 다른 사람의 이력", "nolabel": "진단 · '켰나' 비트 제거", "nosit": "진단 · 상황 요약 제거 (장치·결과만)"}
    M = [f"# 문맥 모델 — 입력 {a.input}, 1단계 {a.stage1}, 2단계 {a.stage2}, k {a.k}, 시드 {a.seed} (조기 종료 @ {best[1]}, 2단계 파라미터 {n_par:,}, 학습 {t_train / 60:.1f}분, 추론 {t_row:.3f} ms/행)", "",
         f"## 표 1 · 플릿 trip_order ≥ {a.adapt_trips} 행 (플릿 ROC / 차별 평균)", "", "| 모델 | " + " | ".join(HEADS) + " | 평균 |", "|---|---|---|---|---|"]
    for n_, (fl, per) in cells.items():
        pv = np.nanmean(np.array(list(per.values())), 0)
        M.append(f"| {names[n_]} | " + " | ".join(f"{fl[j]:.3f} / {pv[j]:.3f}" for j in range(NH)) + f" | {np.nanmean(fl):.3f} |")
    M += ["", "읽기: 다른 사람 이력이 문맥 0개 수준이면 '이 사람의' 이력을 읽는 것. '켰나' 제거로 떨어지는 몫 = 결과를 읽는 몫. "
          "상황 요약 제거로 떨어지는 몫 = 과거의 '상황'을 읽는 몫 — 2단계 어텐션이 GRU 보다 나으려면 이 몫이 어텐션에서 더 커야 한다.", ""]
    # 표 2: 앞선 탑승 수별 (전체 플릿 행)
    qa = np.arange(len(X)); p_all = predict_ctx(torch, net, E, qa, tok, pid, prior, a.k, rng); p_all_c = C6.net_predict(torch, m0, Z, P) if m0 is not None else None
    buckets = [("0", prior == 0), ("1~3", (prior >= 1) & (prior <= 3)), ("4~9", (prior >= 4) & (prior <= 9)), ("10+", prior >= 10)]
    M += ["## 표 2 · 그 사람의 앞선 탑승 수별 (전체 플릿 행, 플릿 ROC: 공통 → 문맥)", "", "| 앞선 탑승 | 행 수 | " + " | ".join(HEADS) + " |", "|---|---|---|---|---|"]
    bvals = []
    for name, bm in buckets:
        c = []
        for j in range(NH):
            k = bm & (W[:, j] > 0)
            if k.any() and Y[k, j].min() != Y[k, j].max():
                rc = roc_auc_score(Y[k, j], p_all[k, j]); c.append((f"{roc_auc_score(Y[k, j], p_all_c[k, j]):.3f} → " if p_all_c is not None else "") + f"{rc:.3f}"); bvals.append(f"{rc:.3f}")
            else:
                c.append("-"); bvals.append("-")
        M.append(f"| {name} | {int(bm.sum())} | " + " | ".join(c) + " |")
    M += ["", f"총 {T():.0f}초"]
    txt = "\n".join(M); log("\n" + txt); (out / "summary.md").write_text(txt + "\n")
    one = [a.input, a.stage1, a.stage2, str(a.k), str(a.seed), str(n_par), f"{t_row:.3f}", f"{t_train / 60:.1f}"] + [f"{v:.4f}" for v in cells["ctx"][0]] + bvals
    for n_ in ("empty", "swap", "nolabel", "nosit", "common"):
        one += [f"{v:.4f}" for v in cells[n_][0]] if n_ in cells else [""] * NH
    one += [a.tag, str(a.d2), str(best[1])]
    hdr = "input,stage1,stage2,k,seed,params,ms_per_row,train_min," + ",".join(f"roc_{h}" for h in HEADS) + "," + ",".join(f"{b}_{h}" for b, _ in buckets for h in HEADS)
    hdr += "," + ",".join(f"{n_}_{h}" for n_ in ("empty", "swap", "nolabel", "nosit", "common") for h in HEADS) + ",tag,d2,best_step"
    (out / "oneline.csv").write_text(hdr + "\n" + ",".join(one) + "\n")
    rows = ["input,stage1,stage2,k,seed,tag,vehicle,model," + ",".join(HEADS)]
    for n_, (fl, per) in cells.items():
        for v, vals in per.items():
            rows.append(f"{a.input},{a.stage1},{a.stage2},{a.k},{a.seed},{a.tag},{v},{n_}," + ",".join("" if np.isnan(x) else f"{x:.4f}" for x in vals))
    (out / "per_car.csv").write_text("\n".join(rows) + "\n")


if __name__ == "__main__":
    main()
