"""플릿 적응 실험 — 공통 베이스(베이스 세계) → 차마다 다른 세계·가족, 차별 조정은 어디까지 필요한가.

    python3 ml/experiments/fleet_adapt.py --base-dataset ml/out/runs/v002/dataset.npz --fleet ml/out/bulk_world/fleet.npz \
        --cache ml/out/bulk_3head_stk/response [--adapt-trips 20] [--seeds 3] [--ft-steps 200]

베이스는 캐시 재사용 (gbm_base_response_stacked.txt, ft_base_response_1500.pt — bulk_pretrain_3head.py --gbm stacked 가 만든 것).
차마다 앞 adapt-trips 탑승(가족의 반응 라벨)으로 조정하고, 뒤 탑승(같은 가족)으로 채점한다.

계보
  G0 GBM 공통 그대로           F0 FT 공통 그대로
  G1 GBM 차별 이어학습          Fh FT 차별 머리만 미세조정 (몸통 고정, lr 1e-3)
  G2 GBM 차별 잎 재적합         Ff FT 차별 몸통+머리 미세조정 (lr 3e-4)
  Gp GBM 플릿 전체로 이어학습    Fp FT 플릿 전체로 몸통+머리 미세조정  (차별 아님 — 대조)
채점: 각 차의 시험 행을 그 차의 모델로 예측 → 플릿 전체 ROC(헤드별, W>0), 차별 ROC 평균, 공통(G0/F0) 대비 이긴 차 수.
"""
import argparse
import copy
import json
import pathlib
import sys
import time

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import bulk_pretrain_3head as B  # noqa: E402
HEADS = B.HEADS


def ft_predict(torch, m, Z, P):
    m.eval()
    with torch.no_grad():
        return np.concatenate([torch.sigmoid(m.head(m(torch.from_numpy(Z[i:i + 8192]), torch.from_numpy(P[i:i + 8192]))[0])).numpy() for i in range(0, len(Z), 8192)])


def ft_finetune(torch, base_state, FT, Z, P, Y, W, steps, lr, seed, head_only):
    torch.manual_seed(seed); m = FT(B.NF); m.load_state_dict(base_state)
    params = list(m.head.parameters()) if head_only else list(m.parameters())
    if head_only:
        for p in m.parameters():
            p.requires_grad_(False)
        for p in params:
            p.requires_grad_(True)
    opt = torch.optim.AdamW(params, lr=lr, weight_decay=1e-4)
    pos = (Y * W).sum(0) / np.maximum(W.sum(0), 1); pw = torch.tensor(((1 - pos) / np.maximum(pos, 1e-3)).astype(np.float32))
    rng = np.random.default_rng(seed); m.train()
    if head_only:
        m.enc.eval()                                       # 몸통 드롭아웃도 끔
    for _ in range(steps):
        idx = rng.integers(0, len(Z), min(128, len(Z)))
        cls, _ = m(torch.from_numpy(Z[idx]), torch.from_numpy(P[idx]))
        logit = m.head(cls); y = torch.from_numpy(Y[idx].astype(np.float32)); w = torch.from_numpy(W[idx])
        bce = torch.nn.functional.binary_cross_entropy_with_logits(logit, y, pos_weight=pw, reduction="none")
        loss = (bce * w).sum() / w.sum().clamp(min=1.0); opt.zero_grad(); loss.backward(); opt.step()
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-dataset", required=True); ap.add_argument("--fleet", required=True); ap.add_argument("--cache", required=True)
    ap.add_argument("--adapt-trips", type=int, default=20); ap.add_argument("--seeds", type=int, default=3); ap.add_argument("--ft-steps", type=int, default=200)
    ap.add_argument("--head-lr", type=float, default=1e-3); ap.add_argument("--out", default=None)
    a = ap.parse_args()
    import lightgbm as lgb
    from sklearn.metrics import roc_auc_score
    torch, FT = B.make_torch(); cache = pathlib.Path(a.cache); out = pathlib.Path(a.out or cache.parent / "fleet"); out.mkdir(parents=True, exist_ok=True)
    T0 = time.time(); logf = open(out / "log.txt", "w")
    def log(s): print(s, flush=True); logf.write(s + "\n"); logf.flush()

    db = np.load(a.base_dataset, allow_pickle=False); XB = db["X"][:, B.ENABLED].astype(np.float32)
    mu = np.nan_to_num(np.nanmean(XB, 0)); sd = np.nanstd(XB, 0); sd[~np.isfinite(sd) | (sd < 1e-6)] = 1.0
    gs = lgb.Booster(model_file=str(cache / "gbm_base_response_stacked.txt")); ft_state = torch.load(cache / "ft_base_response_1500.pt")

    d = np.load(a.fleet, allow_pickle=False); assert int(d["feature_version"]) == B.FO["version"]
    X = d["X"][:, B.ENABLED].astype(np.float32); misc = d["misc"].astype(float); t = d["t"].astype(float)
    rid = d["ride_id"]; pid = d["person_id"]; veh = d["vehicle_id"]; order = d["trip_order"]
    Y, W, _ = B.make_response_labels(X, misc, t, rid, pid, np.random.default_rng(1))
    Z, P = B.standardize(X, mu, sd), np.isfinite(X)
    vehicles = np.unique(veh); ad = order < a.adapt_trips; te = ~ad
    log(f"플릿 {len(vehicles)}대 · 행 {len(X)} · 사람 {len(np.unique(pid))} · 조정 탑승 {len(np.unique(rid[ad]))} / 시험 탑승 {len(np.unique(rid[te]))}")
    for j, h in enumerate(HEADS):
        ev_ad = sum(Y[(rid == r), j].max() > 0 for r in np.unique(rid[ad])); ev_te = sum(Y[(rid == r), j].max() > 0 for r in np.unique(rid[te]))
        k = te & (W[:, j] > 0)
        log(f"  [{h}] 조정 사건 {ev_ad} (차당 {ev_ad / len(vehicles):.1f}) · 시험 사건 {ev_te} · 시험 Oman ROC {roc_auc_score(Y[k, j], np.nan_to_num(X[k, B.OMAN_POS])):.3f}")

    # ---- 예측 모음: pred[lineage][seed] = (n_test_rows, 3)
    nte = int(te.sum()); L = ["G0", "G1", "G2", "Gp", "F0", "Fh", "Ff", "Fp"]
    pred = {l: {s: np.full((nte, 3), np.nan, np.float32) for s in range(a.seeds)} for l in L}
    te_idx = np.flatnonzero(te); pos_in_te = {i: n for n, i in enumerate(te_idx)}
    def put(l, s, rows, p):
        pred[l][s][[pos_in_te[i] for i in rows]] = p
    gp3 = lambda b, Xq: np.stack([b.predict(B.head_X(Xq, j)) for j in range(3)], 1)

    # 공통 / 플릿 전체 조정 (차별 아님)
    p_g0 = gp3(gs, X[te]); p_f0 = ft_predict(torch, (lambda m: (m.load_state_dict(ft_state), m)[1])(FT(B.NF)), Z[te], P[te])
    Xs, ys, ws, rs = B.stack3(X[ad], Y[ad], W[ad], rid[ad])
    for s in range(a.seeds):
        put("G0", s, te_idx, p_g0); put("F0", s, te_idx, p_f0)
        put("Gp", s, te_idx, gp3(B.gbm_continue(gs, Xs, ys, ws, rs, s), X[te]))
        put("Fp", s, te_idx, ft_predict(torch, ft_finetune(torch, ft_state, FT, Z[ad], P[ad], Y[ad], W[ad], a.ft_steps, 3e-4, s, False), Z[te], P[te]))
    log(f"공통·플릿 전체 조정 끝 ({time.time() - T0:.0f}s)")

    # 차별 조정
    for v in vehicles:
        va = ad & (veh == v); vt = te & (veh == v); rows = np.flatnonzero(vt)
        Xs, ys, ws, rs = B.stack3(X[va], Y[va], W[va], rid[va])
        for s in range(a.seeds):
            put("G1", s, rows, gp3(B.gbm_continue(gs, Xs, ys, ws, rs, s), X[vt]))
            put("G2", s, rows, gp3(B.gbm_refit(gs, Xs, ys, ws), X[vt]))
            put("Fh", s, rows, ft_predict(torch, ft_finetune(torch, ft_state, FT, Z[va], P[va], Y[va], W[va], a.ft_steps, a.head_lr, s, True), Z[vt], P[vt]))
            put("Ff", s, rows, ft_predict(torch, ft_finetune(torch, ft_state, FT, Z[va], P[va], Y[va], W[va], a.ft_steps, 3e-4, s, False), Z[vt], P[vt]))
        log(f"  차 {v:2d} 끝 ({time.time() - T0:.0f}s)")

    # ---- 채점
    Yt, Wt, Vt = Y[te], W[te], veh[te]
    names = {"G0": "G0 GBM 공통", "G1": "G1 GBM 차별 이어학습", "G2": "G2 GBM 차별 잎 재적합", "Gp": "Gp GBM 플릿 전체 이어학습",
             "F0": "F0 FT 공통", "Fh": "Fh FT 차별 머리만", "Ff": "Ff FT 차별 몸통+머리", "Fp": "Fp FT 플릿 전체 몸통+머리"}
    rows_out = []
    for j, h in enumerate(HEADS):
        k = Wt[:, j] > 0
        for l in L:
            fleet, per_v, wins = [], [], []
            ref = "G0" if l[0] == "G" else "F0"
            for s in range(a.seeds):
                p = pred[l][s][:, j]; fleet.append(roc_auc_score(Yt[k, j], p[k]))
                pv, wv = [], []
                for v in vehicles:
                    kv = k & (Vt == v)
                    if Yt[kv, j].min() == Yt[kv, j].max():
                        continue
                    r1 = roc_auc_score(Yt[kv, j], p[kv]); r0 = roc_auc_score(Yt[kv, j], pred[ref][s][kv, j]); pv.append(r1); wv.append(r1 > r0)
                per_v.append(np.mean(pv)); wins.append((sum(wv), len(wv)))
            rows_out.append({"head": h, "lineage": l, "fleet": (np.mean(fleet), np.std(fleet)), "per_v": (np.mean(per_v), np.std(per_v)),
                             "wins": f"{int(np.mean([w[0] for w in wins]))}/{wins[0][1]}" if l not in ("G0", "F0") else "-"})
    M = [f"# 플릿 적응 실험 — 차 {len(vehicles)}대, 조정 탑승 {a.adapt_trips}, 시드 {a.seeds}, FT {a.ft_steps}스텝 (머리만 lr {a.head_lr})", "",
         "값은 ROC-AUC. '플릿'은 모든 차의 시험 행을 각자 모델로 예측해 한데 모은 것, '차별 평균'은 차마다 따로 낸 ROC 의 평균. '이긴 차'는 공통(G0/F0)보다 높은 차 수.", ""]
    for h in HEADS:
        M += [f"## {h}", "", "| 계보 | 플릿 ROC | 차별 평균 | 이긴 차 |", "|---|---|---|---|"]
        for r in rows_out:
            if r["head"] == h:
                M.append(f"| {names[r['lineage']]} | {r['fleet'][0]:.3f} ± {r['fleet'][1]:.3f} | {r['per_v'][0]:.3f} ± {r['per_v'][1]:.3f} | {r['wins']} |")
        M.append("")
    M += ["## 읽는 법", "- Fh ≈ Ff 이면 차별 조정은 머리로 충분 → 몸통 공유 구조 성립.", "- Fh, Ff 가 F0 보다 못하면 차당 사건이 너무 적어 차별 조정 자체가 손해 (Fp, Gp 가 더 나을 것).",
          "- G1/G2 대 Fh: 같은 차별 조정을 GBM 으로 했을 때.", f"- 총 {time.time() - T0:.0f}초"]
    txt = "\n".join(M); log("\n" + txt); (out / "summary.md").write_text(txt + "\n")
    np.savez_compressed(out / "preds.npz", **{f"{l}_{s}": pred[l][s] for l in L for s in range(a.seeds)}, Y=Yt, W=Wt, veh=Vt)
    log(f"저장: {out}")


if __name__ == "__main__":
    main()
