"""학습 보조 함수 점검: python3 -m pytest ml/tests"""
import pathlib
import sys

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "train"))
import common as C  # noqa: E402
import policy  # noqa: E402
import train  # noqa: E402


def test_select_threshold_rules():
    p = np.array([0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1, 0.05])
    y = np.array([1, 1, 1, 0, 1, 0, 1, 0, 0, 0])
    # 재현율 0.8 이상(양성 5개 중 4개)인 가장 큰 문턱 = 0.5
    assert train.select_threshold(p, y, "recall", 0.8) == 0.5
    # 정밀도 0.8 이상인 가장 작은 문턱: 0.5에서 4/5 = 0.8
    assert train.select_threshold(p, y, "precision", 0.8) == 0.5
    # 도달할 수 없는 정밀도면 1.0 (켜지지 않음)
    assert train.select_threshold(p, np.zeros(10), "precision", 0.7) == 1.0


def test_select_threshold_ties():
    p = np.array([0.9, 0.5, 0.5, 0.5, 0.1])
    y = np.array([1, 1, 0, 0, 0])
    assert train.select_threshold(p, y, "precision", 0.9) == 0.9
    assert train.select_threshold(p, y, "recall", 1.0) == 0.5


def test_replay_hold_and_off():
    r = np.array([0.1, 0.6, 0.6, 0.6, 0.45, 0.45, 0.3, 0.3, 0.3])
    state, starts = policy.replay_ride(r, th=0.5, hold=3, off_margin=0.1, off_hold=2)
    assert starts == [3]                       # 3주기 연속 넘은 뒤 켜짐
    assert state.tolist() == [0, 0, 0, 1, 1, 1, 1, 0, 0]  # 0.4 아래 2주기 연속에서 꺼짐


def test_group_dropout_never_touches_protected_columns():
    rng = np.random.default_rng(0)
    X = rng.normal(size=(2000, 77))
    ride = np.repeat(np.arange(100), 20)
    Xd = C.group_dropout(X, ride, rng)
    assert not np.isnan(Xd[:, C.NEVER]).any()
    assert not np.isnan(X).any()               # 원본은 그대로
    for g, cols in C.DROPPABLE.items():
        frac = np.isnan(Xd[:, cols]).all(axis=1).mean()
        assert abs(frac - C.FO["groups"][g]["dropout"]) < 0.15, g
    # 같은 탑승 안에서는 그룹이 같이 지워진다
    g3b = np.isnan(Xd[:, C.DROPPABLE["G3b"]]).all(axis=1).reshape(100, 20)
    assert np.all(g3b.all(axis=1) | ((~g3b).sum(axis=1) >= 15))


def test_monotone_fix():
    P = np.array([[0.5, 0.7, 0.2, 0.3, 0.1]])
    assert C.monotone_fix(P).tolist() == [[0.5, 0.5, 0.2, 0.2, 0.1]]
