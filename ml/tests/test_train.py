"""학습 보조 함수 점검: python3 -m pytest ml/tests"""
import pathlib
import sys

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "train"))
import common as C  # noqa: E402
import policy  # noqa: E402
import train  # noqa: E402


def test_pick_threshold_detection():
    g = np.array([0.1, 0.2, 0.3, 0.4, 0.5])
    # 0.4에서 0.86이지만 0.3에서 이미 목표 아래로 떨어졌으므로 0.2
    assert train.pick_threshold(g, [0.95, 0.9, 0.8, 0.86, 0.5], ">=", 0.85) == 0.2
    assert train.pick_threshold(g, [0.5, 0.4, 0.3, 0.2, 0.1], ">=", 0.85) == 0.1   # 못 미치면 가장 낮은 문턱
    assert train.pick_threshold(g, [None, 0.9, 0.9, 0.9, 0.9], ">=", 0.85) == 0.1


def test_pick_threshold_false_alarms():
    g = np.array([0.1, 0.2, 0.3, 0.4, 0.5])
    # 0.2에서 0.4지만 0.3에서 다시 넘으므로 0.4
    assert train.pick_threshold(g, [2.0, 0.4, 0.7, 0.3, 0.1], "<=", 0.5) == 0.4
    assert train.pick_threshold(g, [2.0, 2.0, 2.0, 2.0, 0.6], "<=", 0.5) == 1.0    # 어디서도 안 되면 켜지지 않음


def test_thresholds_from_replay():
    """도달 전 확률 0.6, 도달 뒤 0.99인 탑승: Tier 1 문턱은 0.6이어야 한다 (행 재현율 규칙이면 0.99)"""
    n, rides = 60, 20
    P = np.full((n * rides, len(C.K)), 0.1)
    misc = np.zeros(n * rides)
    for i in range(rides):
        P[i * n + 20:i * n + 31] = 0.6     # MISC 4 도달 전 10주기 + 도달 주기 (같은 주기 ON도 탐지로 침)
        P[i * n + 31:(i + 1) * n] = 0.99
        misc[i * n + 30:(i + 1) * n] = 4
    fm = np.zeros_like(misc)
    for i in range(rides):
        m = misc[i * n:(i + 1) * n]
        fm[i * n:(i + 1) * n] = [m[j + 1:j + 31].max(initial=0) for j in range(n)]
    ride = np.repeat(np.arange(rides), n)
    t = np.tile(np.arange(n) * 10.0, rides)
    th, sweep = train.thresholds_from(P, misc, fm, t, ride)
    assert th["1"] == 0.6
    assert len(sweep["1"]["detection_rate"]) == len(train.GRID)


def test_replay_hold_and_off():
    r = np.array([0.1, 0.6, 0.6, 0.6, 0.45, 0.45, 0.3, 0.3, 0.3])
    state, starts = policy.replay_ride(r, th=0.5, hold=3, off_margin=0.1, off_hold=2)
    assert starts == [3]                       # 3주기 연속 넘은 뒤 켜짐
    assert state.tolist() == [0, 0, 0, 1, 1, 1, 1, 0, 0]  # 0.4 아래 2주기 연속에서 꺼짐


def test_group_dropout_never_touches_protected_columns():
    rng = np.random.default_rng(0)
    X = rng.normal(size=(2000, len(C.NAMES)))
    ride = np.repeat(np.arange(100), 20)
    Xd = C.group_dropout(X, ride, rng)
    assert not np.isnan(Xd[:, C.NEVER]).any()
    assert not np.isnan(X).any()               # 원본은 그대로
    for g, cols in C.DROPPABLE.items():
        frac = np.isnan(Xd[:, cols]).all(axis=1).mean()
        assert abs(frac - C.FO["groups"][g]["dropout"]) < 0.15, g
    # 같은 탑승 안에서는 그룹이 같이 지워진다
    g5 = np.isnan(Xd[:, C.DROPPABLE["G5"]]).all(axis=1).reshape(100, 20)
    assert np.all(g5.all(axis=1) | ((~g5).sum(axis=1) >= 15))


def test_monotone_fix():
    P = np.array([[0.5, 0.7, 0.2, 0.3, 0.1]])
    assert C.monotone_fix(P).tolist() == [[0.5, 0.5, 0.2, 0.2, 0.1]]
