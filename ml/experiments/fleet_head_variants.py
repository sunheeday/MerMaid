"""플릿 — FT 차별 조정 범위 변형: 머리만(학습률·스텝 키움) / 머리+마지막 층 / 몸통+머리. 몸통 공유 비용이 얼마인지.

    python3 ml/experiments/fleet_head_variants.py --base-dataset ml/out/runs/v002/dataset.npz --fleet ml/out/bulk_world/fleet.npz \
        --cache ml/out/bulk_3head_stk/response [--seeds 1]

fleet_adapt.py 와 같은 분할(앞 20탑승 조정 / 뒤 10탑승 채점), 같은 라벨 시드. FT 만 돈다.
"""
import argparse
import pathlib
import sys
import time

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import bulk_pretrain_3head as B  # noqa: E402
from fleet_adapt import ft_predict  # noqa: E402
HEADS = B.HEADS

VARIANTS = [  # (이름, 범위, lr, 스텝)
    ("머리만 · lr 1e-3 · 200", "head", 1e-3, 200),
    ("머리만 · lr 1e-2 · 200", "head", 1e-2, 200),
    ("머리만 · lr 1e-2 · 600", "head", 1e-2, 600),
    ("머리+마지막 층 · lr 1e-3 · 200", "head+last", 1e-3, 200),
    ("몸통+머리 · lr 3e-4 · 200", "all", 3e-4, 200),
    ("몸통+머리 · lr 3e-4 · 600", "all", 3e-4, 600),
]


def finetune(torch, base_state, FT, Z, P, Y, W, steps, lr, seed, scope):
    torch.manual_seed(seed); m = FT(B.NF); m.load_state_dict(base_state)
    if scope == "all":
        params = list(m.parameters())
    else:
        for p in m.parameters():
            p.requires_grad_(False)
        params = list(m.head.parameters()) + (list(m.enc.layers[-1].parameters()) if scope == "head+last" else [])
        for p in params:
            p.requires_grad_(True)
    opt = torch.optim.AdamW(params, lr=lr, weight_decay=1e-4)
    pos = (Y * W).sum(0) / np.maximum(W.sum(0), 1); pw = torch.tensor(((1 - pos) / np.maximum(pos, 1e-3)).astype(np.float32))
    rng = np.random.default_rng(seed); m.train()
    if scope == "head":
        m.enc.eval()
    for _ in range(steps):
        idx = rng.integers(0, len(Z), min(128, len(Z)))
        cls, _ = m(torch.from_numpy(Z[idx]), torch.from_numpy(P[idx]))
        bce = torch.nn.functional.binary_cross_entropy_with_logits(m.head(cls), torch.from_numpy(Y[idx].astype(np.float32)), pos_weight=pw, reduction="none")
        w = torch.from_numpy(W[idx]); loss = (bce * w).sum() / w.sum().clamp(min=1.0); opt.zero_grad(); loss.backward(); opt.step()
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-dataset", required=True); ap.add_argument("--fleet", required=True); ap.add_argument("--cache", required=True)
    ap.add_argument("--adapt-trips", type=int, default=20); ap.add_argument("--seeds", type=int, default=1); ap.add_argument("--out", default=None)
    a = ap.parse_args()
    from sklearn.metrics import roc_auc_score
    torch, FT = B.make_torch(); cache = pathlib.Path(a.cache); out = pathlib.Path(a.out or cache.parent / "fleet"); out.mkdir(parents=True, exist_ok=True); T0 = time.time()
    db = np.load(a.base_dataset, allow_pickle=False); XB = db["X"][:, B.ENABLED].astype(np.float32)
    mu = np.nan_to_num(np.nanmean(XB, 0)); sd = np.nanstd(XB, 0); sd[~np.isfinite(sd) | (sd < 1e-6)] = 1.0
    ft_state = torch.load(cache / "ft_base_response_1500.pt")
    d = np.load(a.fleet, allow_pickle=False)
    X = d["X"][:, B.ENABLED].astype(np.float32); rid = d["ride_id"]; pid = d["person_id"]; veh = d["vehicle_id"]; order = d["trip_order"]
    Y, W, _ = B.make_response_labels(X, d["misc"].astype(float), d["t"].astype(float), rid, pid, np.random.default_rng(1))
    Z, P = B.standardize(X, mu, sd), np.isfinite(X); vehicles = np.unique(veh); ad = order < a.adapt_trips; te = ~ad
    te_idx = np.flatnonzero(te); pos_in_te = {i: n for n, i in enumerate(te_idx)}
    m0 = FT(B.NF); m0.load_state_dict(ft_state); p0 = ft_predict(torch, m0, Z[te], P[te])
    pred = {v[0]: {s: np.full((len(te_idx), 3), np.nan, np.float32) for s in range(a.seeds)} for v in VARIANTS}
    for v in vehicles:
        va = ad & (veh == v); vt = te & (veh == v); rows = [pos_in_te[i] for i in np.flatnonzero(vt)]
        for name, scope, lr, steps in VARIANTS:
            for s in range(a.seeds):
                pred[name][s][rows] = ft_predict(torch, finetune(torch, ft_state, FT, Z[va], P[va], Y[va], W[va], steps, lr, s, scope), Z[vt], P[vt])
        print(f"  차 {v:2d} 끝 ({time.time() - T0:.0f}s)", flush=True)
    Yt, Wt, Vt = Y[te], W[te], veh[te]
    M = [f"# 플릿 — FT 차별 조정 범위 변형 (차 {len(vehicles)}대, 조정 탑승 {a.adapt_trips}, 시드 {a.seeds})", "",
         "| 변형 | " + " | ".join(f"{h} 플릿 / 차별평균" for h in HEADS) + " |", "|---|" + "---|" * len(HEADS)]
    def cells(get):
        c = []
        for j in range(3):
            k = Wt[:, j] > 0; fl, pv = [], []
            for s in range(a.seeds):
                p = get(s)[:, j]; fl.append(roc_auc_score(Yt[k, j], p[k]))
                pv.append(np.mean([roc_auc_score(Yt[kv, j], p[kv]) for v in vehicles if (kv := k & (Vt == v)).any() and Yt[kv, j].min() != Yt[kv, j].max()]))
            c.append(f"{np.mean(fl):.3f} / {np.mean(pv):.3f}")
        return c
    M.append("| 공통 그대로 | " + " | ".join(cells(lambda s: p0)) + " |")
    for name, *_ in VARIANTS:
        M.append(f"| {name} | " + " | ".join(cells(lambda s, n=name: pred[n][s])) + " |")
    M += ["", f"총 {time.time() - T0:.0f}초"]
    txt = "\n".join(M); print("\n" + txt); (out / "head_variants.md").write_text(txt + "\n")


if __name__ == "__main__":
    main()
