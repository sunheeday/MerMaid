"""실차 변환기 점검: 차량축 IMU를 임의로 돌려 놓으면 convert.align이 되돌리는지."""
import pathlib
import sys

import numpy as np

ML = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ML / "realdata"))
import convert  # noqa: E402
import sim  # noqa: E402  (convert가 ml/simulator를 경로에 넣는다)
import trip  # noqa: E402


def rot(axis, deg):
    a = np.radians(deg)
    c, s = np.cos(a), np.sin(a)
    i, j = [(1, 2), (2, 0), (0, 1)][axis]
    R = np.eye(3)
    R[i, i], R[i, j], R[j, i], R[j, j] = c, -s, s, c
    return R


def test_align_recovers_mount():
    rng = np.random.default_rng(5)
    route = sim.gen_route(rng)
    motion = sim.drive(route, sim.gen_driver(rng), None, rng)
    rows, _ = sim.ride_rows(route, motion, sim.gen_ride(rng, 0), rng)
    C = trip.COL
    acc = rows[:, [C["ax"], C["ay"], C["az"]]]
    gyr = rows[:, [C["gx"], C["gy"], C["gz"]]]
    # 장착: 앞-오른쪽-아래(comma 기기 축)에 피치 4°, 요 7°를 더 얹는다
    M = rot(2, 7) @ rot(1, 4) @ np.diag([1.0, -1.0, -1.0])
    a, w, info = convert.align(acc @ M.T, gyr @ M.T, rows[:, C["speed"]])
    assert info["corr_long"] > 0.9 and info["corr_lat"] > 0.9
    lp = lambda x: convert.lowpass(x, 0.5)
    assert np.sqrt(np.mean((lp(a[:, 0]) - lp(acc[:, 0])) ** 2)) < 0.1
    assert np.sqrt(np.mean((lp(a[:, 1]) - lp(acc[:, 1])) ** 2)) < 0.1
    assert np.sqrt(np.mean((w[:, 2] - gyr[:, 2]) ** 2)) < 0.01
    assert abs(np.mean(a[:, 2]) - 9.81) < 0.05
