"""ISO 2631-1 Wf 가중 필터의 기준 계수. C++ P2 필터를 검증할 때 정답으로 쓴다.

constants.json의 "wf" 절을 그대로 따른다: 아날로그 2차 구간 4개(Hh, Hl, Ht, Hs)를
구간마다 쌍선형 변환(사전 왜곡 없음)으로 이산화해 순서대로 잇는다.
"""
import json
import math
import pathlib

import numpy as np
from scipy.signal import bilinear, sosfilt

WF = json.loads((pathlib.Path(__file__).resolve().parents[1] / "protocol" / "constants.json").read_text())["wf"]


def analog_sections(p=WF):
    w = lambda f: 2 * math.pi * f
    w1, w2, w4, w5, w6 = w(p["f1_hz"]), w(p["f2_hz"]), w(p["f4_hz"]), w(p["f5_hz"]), w(p["f6_hz"])
    q1, q4, q5, q6 = p["q1"], p["q4"], p["q5"], p["q6"]
    return [
        ([1.0, 0.0, 0.0], [1.0, w1 / q1, w1 * w1]),          # Hh 고역통과
        ([0.0, 0.0, w2 * w2], [1.0, w2 / q1, w2 * w2]),      # Hl 저역통과
        ([0.0, 0.0, w4 * w4], [1.0, w4 / q4, w4 * w4]),      # Ht 가속도-속도 전이 (f3 = 무한대)
        ([1.0, w5 / q5, w5 * w5], [1.0, w6 / q6, w6 * w6]),  # Hs 계단
    ]


def sos(fs):
    """이산 2차 구간 계수 [b0 b1 b2 a0 a1 a2] x 4."""
    rows = []
    for b, a in analog_sections():
        bz, az = bilinear(b, a, fs)
        rows.append(np.concatenate([bz, az]) / az[0])
    return np.array(rows)


def analog_gain(f):
    s = 1j * 2 * math.pi * np.asarray(f, dtype=float)
    h = np.ones_like(s)
    for b, a in analog_sections():
        h = h * np.polyval(b, s) / np.polyval(a, s)
    return np.abs(h)


def weight(x, fs):
    return sosfilt(sos(fs), np.asarray(x, dtype=float))
