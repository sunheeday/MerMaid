"""학습 공용: 데이터 읽기, 피처 그룹, 그룹 드롭아웃, LightGBM 설정 (PRD 4.2)"""
import json
import pathlib

import lightgbm as lgb
import numpy as np

REPO = pathlib.Path(__file__).resolve().parents[2]
FO = json.loads((REPO / "protocol" / "feature_order.json").read_text())
FEATS = FO["features"]
NAMES = [f["name"] for f in FEATS]
GROUPS = {g: [f["index"] for f in FEATS if f["group"] == g] for g in FO["groups"]}
MONOTONE = [f["monotone"] for f in FEATS]
# 그룹 드롭아웃이 지울 수 있는 칸: G1, G9, 차량 유형은 절대 지우지 않음. G10은 설문과 EV 경험만
DROPPABLE = {g: [i for i in idx if FEATS[i].get("droppable", True)] for g, idx in GROUPS.items()
             if FO["groups"][g]["dropout"] > 0}
NEVER = sorted(set(GROUPS["G1"]) | set(GROUPS["G9"]) | {i for i, f in enumerate(FEATS) if not f.get("droppable", True)})
K = [2, 3, 4, 5, 6]

PARAMS = {
    "objective": "binary", "learning_rate": 0.05, "num_leaves": 31,  # PRD 시작값 [추측]
    "min_data_in_leaf": 50, "feature_fraction": 0.9, "bagging_fraction": 0.8, "bagging_freq": 1,
    "verbosity": -1, "seed": 0, "deterministic": True, "force_row_wise": True, "num_threads": 8,
}
MAX_ROUNDS, EARLY_STOP = 2000, 50


def load(path=REPO / "ml" / "out" / "dataset.npz"):
    d = dict(np.load(path))
    assert int(d["feature_version"]) == FO["version"], "dataset이 다른 feature_order version으로 만들어짐"
    assert list(d["feature_names"]) == NAMES
    return d


def group_dropout(X, ride_id, rng):
    """탑승 단위로 그룹을 통째로 NaN, 그 위에 칸 단위 2%. 원본은 바꾸지 않는다."""
    X = X.copy()
    rides, inv = np.unique(ride_id, return_inverse=True)
    for g, cols in DROPPABLE.items():
        drop = rng.random(len(rides)) < FO["groups"][g]["dropout"]
        X[np.ix_(drop[inv], cols)] = np.nan
    cell_cols = [i for i in range(X.shape[1]) if i not in NEVER]
    mask = rng.random((len(X), len(cell_cols))) < FO["cell_dropout"]
    sub = X[:, cell_cols]
    sub[mask] = np.nan
    X[:, cell_cols] = sub
    return X


def train_binary(Xtr, ytr, Xva, yva, monotone=MONOTONE):
    ds = lgb.Dataset(Xtr, ytr, params={"monotone_constraints": monotone}, free_raw_data=True)
    dv = lgb.Dataset(Xva, yva, reference=ds)
    params = dict(PARAMS, monotone_constraints=monotone)
    return lgb.train(params, ds, MAX_ROUNDS, valid_sets=[dv],
                     callbacks=[lgb.early_stopping(EARLY_STOP, verbose=False)])


def monotone_fix(P):
    """P[:, j] = P(>= K[j]). k가 커질수록 확률이 커지지 않게: p[k] = min(p[k], p[k-1])"""
    P = P.copy()
    for j in range(1, P.shape[1]):
        P[:, j] = np.minimum(P[:, j], P[:, j - 1])
    return P


def person_split(persons, frac, rng):
    """사람 단위로 frac만큼을 떼어 낸다 (조기 종료와 문턱 선택용)."""
    u = np.unique(persons)
    pick = rng.choice(u, max(1, int(round(len(u) * frac))), replace=False)
    return np.isin(persons, pick)
