"""시뮬레이터 점검: python3 -m pytest ml/tests"""
import pathlib
import sys

import numpy as np
import pytest

ML = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ML / "simulator"))
sys.path.insert(0, str(ML))
import build_dataset  # noqa: E402
import sim  # noqa: E402
import trip  # noqa: E402


@pytest.fixture(scope="module")
def ride():
    rng = np.random.default_rng(3)
    route = sim.gen_route(rng)
    motion = sim.drive(route, sim.gen_driver(rng), 3, rng)
    r = sim.gen_ride(rng, 1)
    r["regen_level"] = 3
    rows, mult = sim.ride_rows(route, motion, r, rng)
    return route, motion, rows, mult


def test_motion_is_physical(ride):
    route, motion, rows, _ = ride
    v, a = motion["v"], motion["a_long"]
    assert v.min() >= 0 and v.max() < 40
    assert np.abs(a).max() < 4.5 and np.abs(motion["a_lat"]).max() < 4.5
    assert np.mean(v < 0.1) > 0.02  # 정차 구간이 있다
    assert 14 <= motion["t"][-1] / 60 <= 50
    assert np.all(np.diff(motion["s"]) >= 0)


def test_sway_and_road_profile(ride):
    # T-23: 차로 유지 흔들림은 a_lat와 yaw에 같이 들어가고(a_lat = v·ω), 노면 굴곡은 수직 운동을 만든다
    _, motion, rows, _ = ride
    v = motion["v"]
    m = v > 5
    assert np.allclose(motion["yaw_rate"][m] * v[m], motion["a_lat"][m])
    straight = m & (np.abs(motion["kappa"]) < 1e-5)
    assert straight.any() and np.std(motion["a_lat"][straight]) > 0.02
    assert 0.005 < np.std(motion["a_vert"][m]) < 0.5


def test_rows_follow_trip_format(ride):
    _, motion, rows, mult = ride
    C = trip.COL
    assert rows.shape == (len(motion["t"]), len(trip.COLUMNS))
    assert not np.isnan(rows[:, :C["noise_lowfreq_level"] + 1][:, [C[c] for c in ("ax", "az", "gz", "speed", "soc")]]).any()
    assert abs(np.median(rows[:, C["az"]]) - 9.81) < 0.1  # 중력 포함
    assert np.all((rows[:, C["regen_decel"]] >= 0) & (rows[:, C["regen_decel"]] <= 2.0 + 1e-9))
    assert rows[0, C["one_pedal"]] == 1.0
    assert 0.85 * 1.0 <= mult <= 1.5 * 1.2


def test_ice_has_no_ev_signals():
    rng = np.random.default_rng(5)
    route = sim.gen_route(rng)
    motion = sim.drive(route, sim.gen_driver(rng), None, rng)
    rows, _ = sim.ride_rows(route, motion, sim.gen_ride(rng, 0), rng)
    for c in ("regen_decel", "regen_level", "one_pedal", "soc", "batt_power", "motor_torque"):
        assert np.isnan(rows[:, trip.COL[c]]).all(), c


def test_regen_level_strengthens_braking():
    rng = np.random.default_rng(11)
    route, driver = sim.gen_route(rng), sim.gen_driver(rng)
    lo = sim.drive(route, driver, 0, np.random.default_rng(1))["a_long"]
    hi = sim.drive(route, driver, 3, np.random.default_rng(1))["a_long"]
    assert np.percentile(hi, 1) < np.percentile(lo, 1)


def test_labels_are_consistent(ride):
    _, motion, _, _ = ride
    emit = np.arange(999, len(motion["t"]), 1000)
    score, misc, fut, y, valid = sim.label(motion, 60.0, np.random.default_rng(0), emit)
    assert set(np.unique(misc)) <= set(range(11))
    yy = np.column_stack([y[k] for k in sim.CFG["label"]["k"]])[valid]
    assert np.all(np.diff(yy, axis=1) <= 0)  # y_2 >= y_3 >= ... >= y_6
    assert valid.sum() == len(emit) - 30
    j = 5
    assert fut[j] == misc[j + 1:j + 31].max()


def test_dataset_is_deterministic(tmp_path):
    pax = [sim.gen_passenger(np.random.default_rng(0), i) for i in range(4)]
    a = build_dataset.one_trip((2, 9, pax, 0))
    b = build_dataset.one_trip((2, 9, pax, 0))
    for ra, rb in zip(a, b):
        assert np.array_equal(ra["X"], rb["X"], equal_nan=True) and np.array_equal(ra["y"], rb["y"])
    X = a[0]["X"]
    F = trip.FIDX
    assert X.shape[1] == trip.NF
    assert np.isnan(X[:, [F["tol_display"], F["tol_phone"]]]).all()  # G8 비활성
    assert X[0, F["mssq"]] == pax[0]["mssq"] and X[0, F["sensitivity"]] == pax[0]["sens"]


def test_ice_lift_off_matches_engine_braking():
    # 내연기관 페달 뗌 감속 = 엔진 브레이크 + 구름·공기 저항: 50 km/h 약 0.5, 90~110 km/h 0.6~0.7 m/s2 (UNECE R13-H 논의의 0.7)
    for kmh in (50, 90, 110):
        assert 0.45 < sim.lift_decel_mps2(kmh / 3.6, None) < 0.75
    assert sim.lift_decel_mps2(15, 3) > sim.lift_decel_mps2(15, 1) > sim.lift_decel_mps2(15, 0) >= sim.lift_decel_mps2(15, None)


def test_accel_cap_is_power_limited_at_speed():
    lo, hi = sim.accel_cap_mps2(5.0, 0.0, "ice"), sim.accel_cap_mps2(30.0, 0.0, "ice")
    assert lo > 2.5 and hi < 2.0  # 저속은 토크 한계, 고속은 출력 한계 P/(m·v)
    assert sim.accel_cap_mps2(30.0, 0.0, "ev") > hi
    assert sim.accel_cap_mps2(10.0, 0.08, "ice") < sim.accel_cap_mps2(10.0, 0.0, "ice")  # 오르막


def test_ev_deceleration_sets_in_faster():
    # 회생은 페달을 떼면 바로 걸린다: 같은 경로·운전자에서 EV의 감속 시작 저크가 더 크고, 거리는 경로와 같다
    rng = np.random.default_rng(11)
    route, driver = sim.gen_route(rng), sim.gen_driver(rng)
    ice = sim.drive(route, driver, None, np.random.default_rng(1))
    ev = sim.drive(route, driver, 0, np.random.default_rng(1))
    jerk = lambda m: np.percentile(np.gradient(m["a_long"], sim.DT)[m["a_long"] < -0.3], 1)
    assert jerk(ev) < 1.3 * jerk(ice)
    for m in (ice, ev):
        assert abs(m["s"][-1] - route["length"]) < 5
