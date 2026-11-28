"""⓪ 베이스 그대로 — 사건 0개로 로그 세계 시험 사람에게 바로 채점 (GBM 베이스 3개 · FT 베이스).

    python3 ml/experiments/base_zeroshot_3head.py --base-dataset ml/out/runs/v002/dataset.npz \
        --log-dataset ml/out/bulk_world/log_big.npz  --cache ml/out/bulk_3head/response
    python3 ml/experiments/base_zeroshot_3head.py --base-dataset ml/out/runs/v002/dataset.npz \
        --log-dataset ml/out/bulk_world/same_big.npz --cache ml/out/bulk_3head_same/response --no-log-missing

bulk_pretrain_3head.py 와 같은 분할(시드 0)·같은 라벨(시드 1)·같은 결측(시드 4)·같은 표준화(베이스 세계 통계)를 쓴다.
ROC 는 시험 사람 자격 행(W>0) 묶어서. 표의 ①(사건 50)과 ⓪(GBM)이 비슷하고 ②와 ⓪(FT)가 비슷하면 '베이스 차이',
⓪ 둘이 비슷한데 ②만 높으면 '미세조정 차이'.
"""
import argparse
import pathlib
import sys

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import bulk_pretrain_3head as B  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-dataset", required=True); ap.add_argument("--log-dataset", required=True)
    ap.add_argument("--cache", required=True, help="gbm_base_response_*.txt 와 ft_base_response_1500.pt 가 있는 폴더")
    ap.add_argument("--base-steps", type=int, default=1500); ap.add_argument("--base-label", default="response")
    ap.add_argument("--no-log-missing", action="store_true"); ap.add_argument("--test-frac", type=float, default=0.2); ap.add_argument("--splits", type=int, default=3)
    ap.add_argument("--gbm", choices=["perhead", "stacked"], default="perhead", help="캐시의 GBM 베이스 종류 (bulk 스크립트의 --gbm 과 맞출 것)")
    a = ap.parse_args()
    import lightgbm as lgb
    from sklearn.metrics import roc_auc_score
    torch, FT = B.make_torch(); cache = pathlib.Path(a.cache)

    db = np.load(a.base_dataset, allow_pickle=False); XB = db["X"][:, B.ENABLED].astype(np.float32)
    mu = np.nan_to_num(np.nanmean(XB, 0)); sd = np.nanstd(XB, 0); sd[~np.isfinite(sd) | (sd < 1e-6)] = 1.0

    d = np.load(a.log_dataset, allow_pickle=False)
    X = d["X"][:, B.ENABLED].astype(np.float32); misc = d["misc"].astype(float); t = d["t"].astype(float); rid = d["ride_id"]; pid = d["person_id"]
    if not a.no_log_missing:
        X = B.log_world_missing(X, rid, np.random.default_rng(4))
    Y, W, _ = B.make_response_labels(X, misc, t, rid, pid, np.random.default_rng(1))
    persons = np.unique(pid); perm_p = np.random.default_rng(0).permutation(persons); n_te = max(1, int(len(persons) * a.test_frac))
    splits = [perm_p[i * n_te:(i + 1) * n_te] for i in range(a.splits)]
    Z, P = B.standardize(X, mu, sd), np.isfinite(X)

    if a.gbm == "stacked":
        gs = lgb.Booster(model_file=str(cache / f"gbm_base_{a.base_label}_stacked.txt"))
        pg = np.stack([gs.predict(B.head_X(X, j)) for j in range(len(B.HEADS))], 1)
    else:
        gb = {h: lgb.Booster(model_file=str(cache / f"gbm_base_{a.base_label}_{h}.txt")) for h in B.HEADS}
        pg = np.stack([gb[h].predict(X) for h in B.HEADS], 1)
    m = FT(B.NF); m.load_state_dict(torch.load(cache / f"ft_base_{a.base_label}_{a.base_steps}.pt")); m.eval()
    with torch.no_grad():
        pf = np.concatenate([torch.sigmoid(m.head(m(torch.from_numpy(Z[i:i + 8192]), torch.from_numpy(P[i:i + 8192]))[0])).numpy() for i in range(0, len(Z), 8192)])

    print(f"로그 세계 {pathlib.Path(a.log_dataset).name} · 결측 {'없음' if a.no_log_missing else '적용'} · GBM {a.gbm} · 시험 {n_te}명 × 분할 {a.splits}\n")
    print("| 헤드 | Oman 규칙 | ⓪ GBM 베이스 | ⓪ FT 베이스 | FT−GBM |"); print("|---|---|---|---|---|")
    for j, h in enumerate(B.HEADS):
        ro, rg, rf = [], [], []
        for sp in splits:
            k = np.isin(pid, sp) & (W[:, j] > 0)
            if Y[k, j].min() == Y[k, j].max():
                continue
            ro.append(roc_auc_score(Y[k, j], np.nan_to_num(X[k, B.OMAN_POS]))); rg.append(roc_auc_score(Y[k, j], pg[k, j])); rf.append(roc_auc_score(Y[k, j], pf[k, j]))
        f = lambda v: f"{np.mean(v):.3f} ± {np.std(v):.3f}"
        print(f"| {h} | {f(ro)} | {f(rg)} | {f(rf)} | {np.mean(rf) - np.mean(rg):+.3f} |")
    print("\n± 는 분할 3개에 걸친 표준편차 (시드 없음 — 베이스는 고정).")


if __name__ == "__main__":
    main()
