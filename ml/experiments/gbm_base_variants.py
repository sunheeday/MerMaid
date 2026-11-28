"""GBM 베이스 변형 — 반응 라벨 베이스를 GBM 설정만 바꿔서 FT 베이스와 제로샷 비교.

    python3 ml/experiments/gbm_base_variants.py --base-dataset ml/out/bulk_world/log_base.npz \
        --log-dataset ml/out/bulk_world/log_big.npz --cache ml/out/bulk_3head_loglog/response --no-log-missing

변형 (전부 베이스 세계 반응 라벨, 드롭아웃 복사본 포함, 헤드별 W>0 행만):
  (a) repo 설정     : common.PARAMS + 단조 제약, 300트리            ← 지금까지 쓴 것 (캐시 재사용)
  (b) 단조 끔       : (a)에서 monotone_constraints 만 제거
  (c) 느슨하게      : 단조 끔 + num_leaves 31, min_data_in_leaf 200, lambda_l2 1, lr 0.05, 600트리
  (d) 3헤드 한 모델 : (c) 설정으로, 헤드 번호를 75번째 피처로 넣고 행을 3배 쌓아 부스터 하나
  FT 베이스         : 캐시 (ft_base_response_1500.pt)
채점: 로그 세계 시험 사람(bulk 스크립트와 같은 분할 3개), 자격 행 ROC-AUC. 결과는 <cache>/gbm_variants.md 에도 저장.
"""
import argparse
import pathlib
import sys
import time

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import bulk_pretrain_3head as B  # noqa: E402
C = B.C

LOOSE = dict(C.PARAMS, num_leaves=31, min_data_in_leaf=200, lambda_l2=1.0, learning_rate=0.05, num_threads=4)


def fit_heads(X, Xd, Y, W, params, rounds, mono):
    import lightgbm as lgb
    out = {}
    for j, h in enumerate(B.HEADS):
        k = W[:, j] > 0
        p = dict(params, monotone_constraints=B.MONO) if mono else dict(params)
        out[h] = lgb.train(p, lgb.Dataset(np.vstack([X[k], Xd[k]]), np.concatenate([Y[k, j], Y[k, j]])), num_boost_round=rounds)
    return out


def fit_stacked(X, Xd, Y, W, params, rounds):
    import lightgbm as lgb
    Xs, ys, ws = [], [], []
    for j in range(len(B.HEADS)):
        k = W[:, j] > 0
        for Xi in (X, Xd):
            Xs.append(np.hstack([Xi[k], np.full((k.sum(), 1), j, np.float32)])); ys.append(Y[k, j]); ws.append(W[k, j])
    return lgb.train(dict(params), lgb.Dataset(np.vstack(Xs), np.concatenate(ys), weight=np.concatenate(ws)), num_boost_round=rounds)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-dataset", required=True); ap.add_argument("--log-dataset", required=True); ap.add_argument("--cache", required=True)
    ap.add_argument("--no-log-missing", action="store_true"); ap.add_argument("--test-frac", type=float, default=0.2); ap.add_argument("--splits", type=int, default=3)
    a = ap.parse_args()
    import lightgbm as lgb
    from sklearn.metrics import roc_auc_score
    torch, FT = B.make_torch(); cache = pathlib.Path(a.cache); T0 = time.time(); rng = np.random.default_rng(0)

    db = np.load(a.base_dataset, allow_pickle=False); XB = db["X"][:, B.ENABLED].astype(np.float32); ridB = db["ride_id"]
    YB, WB, _ = B.make_response_labels(XB, db["misc"].astype(float), db["t"].astype(float), ridB, db["person_id"], np.random.default_rng(11))
    XBd = B.group_dropout74(XB, ridB, rng)
    mu = np.nan_to_num(np.nanmean(XB, 0)); sd = np.nanstd(XB, 0); sd[~np.isfinite(sd) | (sd < 1e-6)] = 1.0

    d = np.load(a.log_dataset, allow_pickle=False)
    X = d["X"][:, B.ENABLED].astype(np.float32); rid = d["ride_id"]; pid = d["person_id"]
    if not a.no_log_missing:
        X = B.log_world_missing(X, rid, np.random.default_rng(4))
    Y, W, _ = B.make_response_labels(X, d["misc"].astype(float), d["t"].astype(float), rid, pid, np.random.default_rng(1))
    persons = np.unique(pid); perm_p = np.random.default_rng(0).permutation(persons); n_te = max(1, int(len(persons) * a.test_frac))
    splits = [perm_p[i * n_te:(i + 1) * n_te] for i in range(a.splits)]
    print(f"베이스 {len(XB)}행 · 로그 세계 {len(X)}행 · 결측 {'없음' if a.no_log_missing else '적용'}", flush=True)

    preds = {}
    # (a) repo — 캐시
    ga = {h: lgb.Booster(model_file=str(cache / f"gbm_base_response_{h}.txt")) for h in B.HEADS}
    preds["(a) GBM repo 설정"] = np.stack([ga[h].predict(X) for h in B.HEADS], 1); print(f"(a) 캐시 ({time.time() - T0:.0f}s)", flush=True)
    gb_ = fit_heads(XB, XBd, YB, WB, dict(C.PARAMS, num_threads=4), 300, mono=False)
    preds["(b) 단조 끔"] = np.stack([gb_[h].predict(X) for h in B.HEADS], 1); print(f"(b) 끝 ({time.time() - T0:.0f}s)", flush=True)
    gc = fit_heads(XB, XBd, YB, WB, LOOSE, 600, mono=False)
    preds["(c) 느슨하게"] = np.stack([gc[h].predict(X) for h in B.HEADS], 1); print(f"(c) 끝 ({time.time() - T0:.0f}s)", flush=True)
    gd = fit_stacked(XB, XBd, YB, WB, LOOSE, 600)
    preds["(d) 3헤드 한 모델"] = np.stack([gd.predict(np.hstack([X, np.full((len(X), 1), j, np.float32)])) for j in range(3)], 1); print(f"(d) 끝 ({time.time() - T0:.0f}s)", flush=True)
    # FT 베이스 — 캐시
    Z, P = B.standardize(X, mu, sd), np.isfinite(X)
    m = FT(B.NF); m.load_state_dict(torch.load(cache / "ft_base_response_1500.pt")); m.eval()
    with torch.no_grad():
        preds["FT 베이스"] = np.concatenate([torch.sigmoid(m.head(m(torch.from_numpy(Z[i:i + 8192]), torch.from_numpy(P[i:i + 8192]))[0])).numpy() for i in range(0, len(Z), 8192)])

    names = list(preds); L = ["| 헤드 | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
    for j, h in enumerate(B.HEADS):
        cells = []
        for n in names:
            v = []
            for sp in splits:
                k = np.isin(pid, sp) & (W[:, j] > 0)
                if Y[k, j].min() != Y[k, j].max():
                    v.append(roc_auc_score(Y[k, j], preds[n][k, j]))
            cells.append(f"{np.mean(v):.3f} ± {np.std(v):.3f}")
        L.append(f"| {h} | " + " | ".join(cells) + " |")
    L.append(""); L.append("± 는 분할 3개 표준편차. (a)는 지금까지의 GBM 베이스, FT 베이스는 같은 캐시.")
    txt = "\n".join(L); print("\n" + txt); (cache / "gbm_variants.md").write_text(txt + "\n")
    print(f"\n총 {time.time() - T0:.0f}초")


if __name__ == "__main__":
    main()
