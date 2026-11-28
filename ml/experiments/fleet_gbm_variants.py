"""플릿 — GBM 차별 조정 변형 (공정성 확인): 이어학습 학습률·트리 수·잎 크기, 잎 재적합 decay. FT 머리 변형과 같은 자리.

    python3 ml/experiments/fleet_gbm_variants.py --base-dataset ml/out/runs/v002/dataset.npz --fleet ml/out/bulk_world/fleet.npz \
        --cache ml/out/bulk_3head_stk/response

fleet_adapt.py 와 같은 분할·라벨. GBM(쌓기 베이스)만 돈다. 결정적이라 시드 없음.
"""
import argparse
import pathlib
import sys
import time

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import bulk_pretrain_3head as B  # noqa: E402
HEADS = B.HEADS

# (이름, 종류, 옵션)
VARIANTS = [
    ("이어학습 · 지금 규칙 (G1)", "cont_rule", {}),
    ("이어학습 · lr 0.1 · 100트리 · 잎7 · min20", "cont", {"lr": 0.1, "rounds": 100, "leaves": 7, "min_leaf": 20}),
    ("이어학습 · lr 0.2 · 100트리 · 잎7 · min20", "cont", {"lr": 0.2, "rounds": 100, "leaves": 7, "min_leaf": 20}),
    ("이어학습 · lr 0.2 · 300트리 · 잎7 · min20", "cont", {"lr": 0.2, "rounds": 300, "leaves": 7, "min_leaf": 20}),
    ("이어학습 · lr 0.3 · 100트리 · 잎3 · min20", "cont", {"lr": 0.3, "rounds": 100, "leaves": 3, "min_leaf": 20}),
    ("이어학습 · lr 0.1 · 300트리 · 잎15 · min10", "cont", {"lr": 0.1, "rounds": 300, "leaves": 15, "min_leaf": 10}),
    ("잎 재적합 · decay 0.9 (G2)", "refit", {"decay": 0.9}),
    ("잎 재적합 · decay 0.5", "refit", {"decay": 0.5}),
    ("잎 재적합 · decay 0.0", "refit", {"decay": 0.0}),
]


def cont(base, Xs, ys, ws, lr, rounds, leaves, min_leaf):
    import lightgbm as lgb
    k = ws > 0
    if ys[k].min() == ys[k].max():
        return base
    params = dict(B.C.PARAMS, num_threads=4, monotone_constraints=B.mono_for(Xs), learning_rate=lr, num_leaves=leaves,
                  min_data_in_leaf=min_leaf, lambda_l2=1.0, bagging_fraction=0.8, bagging_freq=1, feature_fraction=0.8)
    return lgb.train(params, lgb.Dataset(Xs[k], ys[k]), num_boost_round=rounds, init_model=base, keep_training_booster=True)


def refit(base, Xs, ys, ws, decay):
    k = ws > 0
    if ys[k].min() == ys[k].max():
        return base
    return base.refit(Xs[k], ys[k], decay_rate=decay)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-dataset", required=True); ap.add_argument("--fleet", required=True); ap.add_argument("--cache", required=True)
    ap.add_argument("--adapt-trips", type=int, default=20); ap.add_argument("--out", default=None)
    a = ap.parse_args()
    import lightgbm as lgb
    from sklearn.metrics import roc_auc_score
    cache = pathlib.Path(a.cache); out = pathlib.Path(a.out or cache.parent / "fleet"); out.mkdir(parents=True, exist_ok=True); T0 = time.time()
    gs = lgb.Booster(model_file=str(cache / "gbm_base_response_stacked.txt"))
    d = np.load(a.fleet, allow_pickle=False)
    X = d["X"][:, B.ENABLED].astype(np.float32); rid = d["ride_id"]; pid = d["person_id"]; veh = d["vehicle_id"]; order = d["trip_order"]
    Y, W, _ = B.make_response_labels(X, d["misc"].astype(float), d["t"].astype(float), rid, pid, np.random.default_rng(1))
    vehicles = np.unique(veh); ad = order < a.adapt_trips; te = ~ad
    te_idx = np.flatnonzero(te); pos_in_te = {i: n for n, i in enumerate(te_idx)}
    gp3 = lambda b, Xq: np.stack([b.predict(B.head_X(Xq, j)) for j in range(3)], 1)
    pred = {v[0]: np.full((len(te_idx), 3), np.nan, np.float32) for v in VARIANTS}; p0 = gp3(gs, X[te])
    for v in vehicles:
        va = ad & (veh == v); vt = te & (veh == v); rows = [pos_in_te[i] for i in np.flatnonzero(vt)]
        Xs, ys, ws, rs = B.stack3(X[va], Y[va], W[va], rid[va])
        for name, kind, o in VARIANTS:
            if kind == "cont_rule":
                b = B.gbm_continue(gs, Xs, ys, ws, rs, 0)
            elif kind == "cont":
                b = cont(gs, Xs, ys, ws, **o)
            else:
                b = refit(gs, Xs, ys, ws, **o)
            pred[name][rows] = gp3(b, X[vt])
        print(f"  차 {v:2d} 끝 ({time.time() - T0:.0f}s)", flush=True)
    Yt, Wt, Vt = Y[te], W[te], veh[te]
    def cells(p):
        c = []
        for j in range(3):
            k = Wt[:, j] > 0; fl = roc_auc_score(Yt[k, j], p[k, j])
            pv = np.mean([roc_auc_score(Yt[kv, j], p[kv, j]) for v in vehicles if (kv := k & (Vt == v)).any() and Yt[kv, j].min() != Yt[kv, j].max()])
            c.append(f"{fl:.3f} / {pv:.3f}")
        return c
    M = [f"# 플릿 — GBM 차별 조정 변형 (차 {len(vehicles)}대, 조정 탑승 {a.adapt_trips})", "",
         "| 변형 | " + " | ".join(f"{h} 플릿 / 차별평균" for h in HEADS) + " |", "|---|" + "---|" * 3, "| 공통 그대로 | " + " | ".join(cells(p0)) + " |"]
    for name, *_ in VARIANTS:
        M.append(f"| {name} | " + " | ".join(cells(pred[name])) + " |")
    M += ["", "비교 기준 (FT, fleet_head_variants.md): 머리+마지막 층 0.852 / 0.895 / 0.834 · 머리만 lr 1e-2 0.848 / 0.872 / 0.816", f"총 {time.time() - T0:.0f}초"]
    txt = "\n".join(M); print("\n" + txt); (out / "gbm_variants.md").write_text(txt + "\n")


if __name__ == "__main__":
    main()
