"""Wf 필터 사양이 ISO 2631-1 표와 맞는지: python3 -m pytest ml/tests"""
import pathlib
import sys

import numpy as np
from scipy.signal import sosfreqz

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import wf  # noqa: E402

FS = 100.0
# ISO 2631-1:1997 Wf 1/3 옥타브 가중치 (x 1000)
ISO_TABLE = {0.1: 695, 0.125: 895, 0.16: 1006, 0.2: 992, 0.25: 854, 0.315: 619,
             0.4: 384, 0.5: 224, 0.63: 116, 0.8: 53.0, 1.0: 23.5}


def test_analog_matches_iso_table():
    for f, g in ISO_TABLE.items():
        assert abs(wf.analog_gain(f) * 1000 / g - 1) < 0.03, f


def test_digital_matches_analog():
    f = np.array(list(ISO_TABLE))
    _, h = sosfreqz(wf.sos(FS), worN=f, fs=FS)
    # 사전 왜곡을 안 해서 1 Hz에서 0.12% 차이. 0.1~0.5 Hz 대역에서는 0.02% 이하
    assert np.max(np.abs(np.abs(h) / wf.analog_gain(f) - 1)) < 2e-3


def test_removes_gravity():
    # 1도 기울어 생긴 상수 0.17 m/s^2는 가중 후 사라진다
    t = np.arange(0, 600, 1 / FS)
    y = wf.weight(np.full_like(t, 9.81 * np.sin(np.radians(1))), FS)
    assert np.abs(y[-1000:]).max() < 1e-4
