"""AC-01 모델 동등성: Python lightgbm과 QNX lib_lightgbm이 같은 확률과 Tier 결정을 내는지 (PRD §4.3 검증 3)

    python3 ml/verify/model_parity.py make             # 벡터와 Python 기대값
    (Pi)    verify_model <models> vectors.bin actual_p.bin
    python3 ml/verify/model_parity.py check actual_p.bin

벡터: 데이터셋 행 2,000 + NaN 행(전부 / 그룹 단위) + 0 행(전부 / 결측을 0으로) + 분기 기준값 바로 위아래(± 1e-9)와 기준값 그 자체.
합격: max |차이| < 1e-6, Tier 결정(P(>=3) >= th1, P(>=4) >= th2, th3) 100% 일치.
모델은 배포 중인 버전(ml/runs.py CURRENT), 문턱은 그 버전의 thresholds.json.
출력: <모델 버전>/parity/ vectors.bin, expected_p.bin, meta.json, result.json
"""
import json
import pathlib
import sys

import lightgbm as lgb
import numpy as np

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "ml" / "train"))
import common as C  # noqa: E402

sys.path.insert(0, str(REPO / "ml"))
import runs  # noqa: E402

RUN = runs.current_v1_models()
OUT = RUN / "parity"
MODELS = RUN / "models"
EPS = 1e-9


def write_bin(path, A):
    A = np.asarray(A, dtype="<f8")
    with open(path, "wb") as f:
        np.array([1, A.shape[0], A.shape[1]], dtype="<f8").tofile(f)
        A.tofile(f)


def read_bin(path, ncol):
    a = np.fromfile(path, dtype="<f8")
    assert a[0] == 1 and a[2] == ncol, "bad header"
    return a[3:].reshape(int(a[1]), ncol)


def boosters():
    return [lgb.Booster(model_file=str(MODELS / f"model_{k}.txt")) for k in C.K]


def predict(bs, X):
    return C.monotone_fix(np.column_stack([b.predict(X) for b in bs]))


def splits(b):
    """모델의 수치 분기 (피처, 기준값) 전부"""
    out = []

    def walk(node):
        if "split_feature" in node:
            if node.get("decision_type") == "<=":
                out.append((node["split_feature"], node["threshold"]))
            walk(node["left_child"])
            walk(node["right_child"])

    for t in b.dump_model()["tree_info"]:
        walk(t["tree_structure"])
    return out


def make():
    rng = np.random.default_rng(11)
    d = C.load()
    X = d["X"]
    base = X[rng.choice(len(X), 2000, replace=False)]
    parts, kinds = [base], ["dataset"] * len(base)

    nan_all = np.full((1, X.shape[1]), np.nan)
    nan_grp = C.group_dropout(X[rng.choice(len(X), 200, replace=False)], np.arange(200), rng)
    zero_all = np.zeros((1, X.shape[1]))
    zero_miss = np.nan_to_num(nan_grp.copy(), nan=0.0)
    for A, k in ((nan_all, "nan_all"), (nan_grp, "nan_groups"), (zero_all, "zero_all"), (zero_miss, "zero_for_missing")):
        parts.append(A)
        kinds += [k] * len(A)

    bs = boosters()
    sp = sorted({s for b in bs for s in splits(b)})
    pick = rng.choice(len(sp), min(600, len(sp)), replace=False)
    edge = []
    for j, i in enumerate(pick):
        f, th = sp[i]
        row = X[rng.integers(len(X))].copy()
        for off, k in ((-EPS, "split_below"), (EPS, "split_above"), (0.0, "split_exact")):
            r = row.copy()
            r[f] = th + off
            edge.append(r)
            kinds.append(k)
    parts.append(np.array(edge))
    V = np.vstack(parts)
    P = predict(bs, V)
    OUT.mkdir(parents=True, exist_ok=True)
    write_bin(OUT / "vectors.bin", V)
    write_bin(OUT / "expected_p.bin", P)
    th = json.loads((RUN / "thresholds.json").read_text())["base_th"]
    meta = {"run": RUN.name, "rows": len(V), "kinds": {k: kinds.count(k) for k in dict.fromkeys(kinds)}, "splits_total": len(sp),
            "thresholds": th, "kind_per_row": kinds,
            "models_sha256": json.loads((MODELS / "models.json").read_text())["models"]}
    (OUT / "meta.json").write_text(json.dumps(meta))
    print(f"wrote {len(V)} rows to {OUT / 'vectors.bin'}: {meta['kinds']}")


def decisions(P, th):
    return np.column_stack([P[:, C.K.index(3)] >= th["1"], P[:, C.K.index(4)] >= th["2"], P[:, C.K.index(4)] >= th["3"]])


def check(actual_path):
    meta = json.loads((OUT / "meta.json").read_text())
    E = read_bin(OUT / "expected_p.bin", 5)
    A = read_bin(actual_path, 5)
    assert A.shape == E.shape, f"shape {A.shape} vs {E.shape}"
    kinds = np.array(meta["kind_per_row"])
    diff = np.abs(A - E)
    dec_ok = (decisions(A, meta["thresholds"]) == decisions(E, meta["thresholds"])).all(axis=1)
    res = {"rows": int(len(E)), "max_abs_diff": float(diff.max()), "decision_match": float(dec_ok.mean()),
           "by_kind": {k: {"rows": int((kinds == k).sum()), "max_abs_diff": float(diff[kinds == k].max()),
                           "decision_match": float(dec_ok[kinds == k].mean())} for k in meta["kinds"]}}
    res["pass"] = bool(res["max_abs_diff"] < 1e-6 and res["decision_match"] == 1.0)
    (OUT / "result.json").write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))
    return 0 if res["pass"] else 1


if __name__ == "__main__":
    if sys.argv[1:2] == ["make"]:
        make()
    elif sys.argv[1:2] == ["check"] and len(sys.argv) == 3:
        sys.exit(check(sys.argv[2]))
    else:
        print(__doc__)
        sys.exit(2)
