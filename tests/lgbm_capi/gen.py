"""Mac에서 실행: 모델(텍스트), 입력, Python 예측값 생성."""
from collections import Counter
from pathlib import Path

import lightgbm as lgb
import numpy as np

OUT = Path(__file__).parent / "fixtures"
OUT.mkdir(exist_ok=True)
N = 12


def make_data(rng, n):
    X = rng.normal(size=(n, N))
    z = (1.2 * X[:, 0] + X[:, 1] * X[:, 2] + 1.0 * (X[:, 3] > 0.5)
         + 0.8 * X[:, 4] - 0.6 * X[:, 5] + 0.7 * (X[:, 8] > 0))
    y = (rng.random(n) < 1 / (1 + np.exp(-z))).astype(int)
    X[rng.random(n) < 0.3, 4:8] = np.nan
    sub = X[:, 1:4]
    sub[rng.random(sub.shape) < 0.02] = np.nan
    X[rng.random(n) < 0.3, 8] = 0.0
    return X, y


def walk(node, thresholds, missing):
    if "leaf_value" in node:
        return
    thresholds.append((node["split_feature"], node["threshold"]))
    missing[node["missing_type"]] += 1
    walk(node["left_child"], thresholds, missing)
    walk(node["right_child"], thresholds, missing)


for name, extra in {"model_nan": {}, "model_zero": {"zero_as_missing": True}}.items():
    rng = np.random.default_rng(42)
    X, y = make_data(rng, 6000)
    params = {"objective": "binary", "num_leaves": 15, "learning_rate": 0.1,
              "min_data_in_leaf": 20, "seed": 42, "deterministic": True, "verbosity": -1,
              "monotone_constraints": [1] + [0] * (N - 1), **extra}
    booster = lgb.train(params, lgb.Dataset(X, label=y), num_boost_round=60)
    booster.save_model(str(OUT / f"{name}.txt"))

    thresholds, missing = [], Counter()
    for tree in booster.dump_model()["tree_info"]:
        walk(tree["tree_structure"], thresholds, missing)

    rows = [make_data(rng, 2000)[0], np.full((1, N), np.nan), np.zeros((1, N))]
    picks = rng.permutation(len(thresholds))[:300]
    base = make_data(rng, len(picks))[0]
    for row, i in zip(base, picks):
        feature, thr = thresholds[i]
        for value in (thr, np.nextafter(thr, -np.inf), np.nextafter(thr, np.inf)):
            r = row.copy()
            r[feature] = value
            rows.append(r[None, :])
    test_x = np.vstack(rows)

    np.savetxt(OUT / f"{name}_inputs.csv", test_x, fmt="%.17g", delimiter=",")
    expected = np.column_stack([booster.predict(test_x, raw_score=True), booster.predict(test_x)])
    np.savetxt(OUT / f"{name}_expected.csv", expected, fmt="%.17g", delimiter=",")
    # QNX strtod는 CSV 값을 1~2 ULP 다르게 읽으므로, 비교에는 원본 비트(리틀엔디언 double)를 쓴다
    test_x.astype("<f8").tofile(OUT / f"{name}_inputs.bin")
    expected.astype("<f8").tofile(OUT / f"{name}_expected.bin")
    print(f"{name}: rows={len(test_x)} missing_type={dict(missing)} lightgbm={lgb.__version__}")
