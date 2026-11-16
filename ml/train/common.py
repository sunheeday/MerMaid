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
    "objective": "binary", "learning_rate": 0.03, "num_leaves": 7,  # T-24: 드문 k(5, 6)가 과적합하지 않게 강한 규제
    "min_data_in_leaf": 1000, "lambda_l2": 10.0, "feature_fraction": 0.9, "bagging_fraction": 0.8, "bagging_freq": 1,
    "verbosity": -1, "seed": 0, "deterministic": True, "force_row_wise": True, "num_threads": 8,
}
# 조기 종료: 검증 사람이 15%(약 18명)라 드문 k에서는 손실이 흔들려 트리 1개에서 멈추기도 했다 (T-24).
# MIN_ROUNDS 전에는 멈추지 않고, 그 뒤 EARLY_STOP 라운드 동안 나아지지 않으면 멈춘다.
MAX_ROUNDS, EARLY_STOP, MIN_ROUNDS = 2000, 50, 100


def load(path=None):
    """기본: 배포 중인 모델(ml/runs.py CURRENT)이 학습한 데이터셋"""
    if path is None:
        import sys
        sys.path.insert(0, str(REPO / "ml"))
        import runs
        path = runs.dataset_for(runs.current_models())
    d = dict(np.load(path, allow_pickle=False))
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


def early_stop_after(min_rounds, patience):
    """min_rounds 전에는 멈추지 않는 조기 종료. 가장 좋은 라운드도 min_rounds 이상에서만 고른다."""
    best = {"score": float("inf"), "it": min_rounds, "res": None}

    def cb(env):
        it = env.iteration + 1
        if it < min_rounds:
            return
        score = env.evaluation_result_list[0][2]  # binary_logloss, 낮을수록 좋음
        if score < best["score"]:
            best.update(score=score, it=it, res=env.evaluation_result_list)
        elif it - best["it"] >= patience:
            raise lgb.callback.EarlyStopException(best["it"] - 1, best["res"])
    cb.order = 30
    cb.best = best
    return cb


def train_binary(Xtr, ytr, Xva, yva, monotone=MONOTONE):
    ds = lgb.Dataset(Xtr, ytr, params={"monotone_constraints": monotone}, free_raw_data=True)
    dv = lgb.Dataset(Xva, yva, reference=ds)
    params = dict(PARAMS, monotone_constraints=monotone)
    stop = early_stop_after(MIN_ROUNDS, EARLY_STOP)
    b = lgb.train(params, ds, MAX_ROUNDS, valid_sets=[dv], callbacks=[stop])
    if not b.best_iteration:  # MAX_ROUNDS까지 멈추지 않은 경우
        b.best_iteration = stop.best["it"]
    return b


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
