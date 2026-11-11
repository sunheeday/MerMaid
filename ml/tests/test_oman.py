"""ml/oman.py 단위 테스트: python3 -m pytest ml/tests"""
import math
import pathlib
import sys

import numpy as np
from scipy.signal import butter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import oman  # noqa: E402

DT = 0.01


def test_butter_matches_scipy():
    b, a = oman.butter2_lowpass(0.63, 100.0)
    sb, sa = butter(2, 0.63, fs=100.0)
    assert np.allclose(b, sb, rtol=0, atol=1e-15) and np.allclose(a, sa, rtol=0, atol=1e-15)


def test_lp2_step_response():
    # 1차 2단의 계단 응답: 1 - (1 + t/beta) e^(-t/beta)
    beta, n = 5.0, 3000
    y = oman.lp2(np.ones(n), beta, DT)
    t = np.arange(1, n + 1) * DT
    assert np.max(np.abs(y - (1 - (1 + t / beta) * np.exp(-t / beta)))) < 2e-3


def test_constant_input_steady_state():
    # 상수 c면 S -> c, F -> K c^2, 점수 -> (K c^2 + c)^0.4
    c, K = 0.8, 51.4
    score, S = oman.run(np.full(int(8000 / DT), c), K, DT)
    assert abs(S[-1] - c) < 1e-3
    assert abs(score[-1] - (K * c * c + c) ** 0.4) < 1e-2


def test_zero_input_stays_zero():
    score, S = oman.run(np.zeros(1000), 51.4, DT)
    assert not score.any() and not S.any()


def test_monotone_in_gain_and_amplitude():
    t = np.arange(0, 600, DT)
    a = np.sin(2 * np.pi * 0.3 * t)
    c = oman.input_signal(a, None, DT)
    s1 = oman.run(c, 40.0, DT)[0][-1]
    assert oman.run(c, 80.0, DT)[0][-1] > s1
    assert oman.run(2 * c, 40.0, DT)[0][-1] > s1


def test_input_is_horizontal_magnitude():
    t = np.arange(0, 60, DT)
    ax, ay = np.sin(t), np.cos(t)
    c = oman.input_signal(ax, ay, DT, lowpass_hz=None)
    assert np.allclose(c, 1.0)


def test_sensitivity_rule():
    assert math.isclose(float(oman.sensitivity(12.90)), 1.0)
    assert math.isclose(float(oman.sensitivity(0)), math.exp(0.3 * -12.90 / 9.90), rel_tol=1e-12)
    assert math.isclose(float(oman.sensitivity(54)), min(2.0, math.exp(0.3 * (54 - 12.90) / 9.90)), rel_tol=1e-12)
    assert 0.5 <= float(oman.sensitivity(-100)) and float(oman.sensitivity(1000)) == 2.0
