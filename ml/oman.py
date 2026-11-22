"""Oman 모델 (Irmak 2022 구조): 시뮬레이터 라벨과 TU Delft 검증이 함께 쓰는 기준 구현.

C++ P2(controller/common)는 이 파일과 같은 식을 샘플 단위로 계산해야 한다.
- 입력: 가중 전 수평 가속도. 축마다 2차 버터워스 저역통과(0.63 Hz) 후 크기 |(ax, ay)|
- 느린 경로 S = LP2(c, beta_slow), 빠른 경로 F = LP2(K * S * c, beta_fast)
- 출력 (F + S)^exponent = MISC 척도
- LP2 = 1차 저역통과 2단, y[n] = a * y[n-1] + (1 - a) * x[n], a = exp(-dt / beta)
- 한 샘플에서 S를 먼저 갱신하고, 갱신된 S로 F를 계산한다.
"""
import json
import math
import pathlib

import numpy as np
from scipy.signal import lfilter, lfilter_zi

CONST = json.loads((pathlib.Path(__file__).resolve().parents[1] / "protocol" / "constants.json").read_text())
OMAN = CONST["oman"]


def butter2_lowpass(fc, fs):
    """2차 버터워스 저역통과 (쌍선형 변환, 주파수 사전 왜곡). C++과 같은 닫힌 식."""
    k = math.tan(math.pi * fc / fs)
    norm = 1.0 / (1.0 + math.sqrt(2.0) * k + k * k)
    b0 = k * k * norm
    b = [b0, 2.0 * b0, b0]
    a = [1.0, 2.0 * (k * k - 1.0) * norm, (1.0 - math.sqrt(2.0) * k + k * k) * norm]
    return b, a


def lp2(x, beta, dt):
    """1차 저역통과 2단, 정확한 지수 이산화. 초기 상태 0."""
    al = math.exp(-dt / beta)
    f = lambda u: lfilter([1.0 - al], [1.0, -al], u)
    return f(f(x))


def input_signal(ax, ay, dt, lowpass_hz=OMAN["input_lowpass_hz"], prime=False):
    """Oman 입력 c(t). lowpass_hz=None이면 필터 없이 크기만.
    prime=True면 저역통과를 첫 샘플의 정상상태에서 시작한다 (C++ featurize, T-22).
    라벨(sim.label)은 정지 상태(가속도 0)에서 출발하므로 기본값 False 그대로 쓴다."""
    ax = np.asarray(ax, dtype=float)
    ay = np.zeros_like(ax) if ay is None else np.asarray(ay, dtype=float)
    if lowpass_hz:
        b, a = butter2_lowpass(lowpass_hz, 1.0 / dt)
        if prime and len(ax):
            zi = lfilter_zi(b, a)
            ax, ay = lfilter(b, a, ax, zi=zi * ax[0])[0], lfilter(b, a, ay, zi=zi * ay[0])[0]
        else:
            ax, ay = lfilter(b, a, ax), lfilter(b, a, ay)
    return np.hypot(ax, ay)


def run(c, K, dt, beta_fast=OMAN["beta_fast_s"], beta_slow=OMAN["beta_slow_s"], exponent=OMAN["exponent"]):
    """c(t) → (score, S). score는 MISC 척도, S는 민감화(느린 상태)."""
    S = lp2(c, beta_slow, dt)
    F = lp2(K * S * c, beta_fast, dt)
    return np.maximum(F + S, 0.0) ** exponent, S


def sensitivity(mssq, c=CONST["mssq"]):
    """MSSQ 원점수 → 민감도 = clip(exp(coef * z), min, max)."""
    z = (np.asarray(mssq, dtype=float) - c["norm_mean"]) / c["norm_sd"]
    return np.clip(np.exp(c["sens_coef"] * z), c["sens_min"], c["sens_max"])
