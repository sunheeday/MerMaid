"""C++ featurize가 Python 기준 구현(ml/oman.py, ml/wf.py)과 같은 값을 내는지 (PRD 골든 테스트 기준)

합격: |차이| <= 1e-9 또는 상대 1e-7. featurize가 없으면 cmake로 빌드한다.
"""
import pathlib
import subprocess
import sys

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import oman  # noqa: E402
import trip  # noqa: E402
import wf  # noqa: E402

DT = 1 / trip.FS
NAMES = [f["name"] for f in trip.FEATURES["features"]]
IDX = {n: i for i, n in enumerate(NAMES)}


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    if not trip.FEATURIZE.exists():
        b = trip.REPO / "build"
        subprocess.run(["cmake", "-S", str(trip.REPO / "controller"), "-B", str(b)], check=True, capture_output=True)
        subprocess.run(["cmake", "--build", str(b), "--target", "featurize"], check=True, capture_output=True)
    rng = np.random.default_rng(7)
    n = int(20 * 60 * trip.FS)
    rows = trip.empty(n)
    t = rows[:, 0]
    ax = sum(a * np.sin(2 * np.pi * f * t + p) for a, f, p in [(0.8, 0.05, 0.3), (1.2, 0.2, 1.0), (0.5, 0.7, 2.0)])
    ay = sum(a * np.sin(2 * np.pi * f * t + p) for a, f, p in [(1.0, 0.12, 0.1), (0.6, 0.35, 0.5)])
    rows[:, trip.COL["ax"]] = ax + rng.normal(0, 0.05, n)
    rows[:, trip.COL["ay"]] = ay + rng.normal(0, 0.05, n)
    rows[:, trip.COL["az"]] = 9.81 + rng.normal(0, 0.05, n)
    for c in ("gx", "gy", "gz"):
        rows[:, trip.COL[c]] = rng.normal(0, 0.01, n)
    d = tmp_path_factory.mktemp("golden")
    trip.write_trip(d / "trip.bin", rows)
    tf, X = trip.featurize(d / "trip.bin", d / "features.bin")
    return rows, tf, X


def close(actual, expected):
    return np.all(np.abs(actual - expected) <= np.maximum(1e-9, 1e-7 * np.abs(expected)))


def test_row_timing(result):
    rows, tf, X = result
    assert X.shape == (120, 71)
    assert np.allclose(tf, rows[999::1000, 0])


def test_oman_matches_python(result):
    rows, tf, X = result
    c = oman.input_signal(rows[:, trip.COL["ax"]], rows[:, trip.COL["ay"]], DT)
    score, S = oman.run(c, oman.OMAN["K0"], DT)
    at = np.arange(999, len(rows), 1000)
    assert close(X[:, IDX["oman_score"]], score[at])
    assert close(X[:, IDX["sensitization"]], S[at])


def test_weighted_rms_and_msdv_match_python(result):
    rows, tf, X = result
    w = {a: wf.weight(rows[:, trip.COL[a]], trip.FS) for a in ("ax", "ay", "az")}
    at = np.arange(999, len(rows), 1000)
    for a, axis in (("ax", "x"), ("ay", "y"), ("az", "z")):
        for name, n in (("10s", 1000), ("1m", 6000), ("5m", 30000)):
            exp = np.array([np.sqrt(np.mean(w[a][max(0, i + 1 - n):i + 1] ** 2)) for i in at])
            assert close(X[:, IDX[f"rms_{axis}_{name}"]], exp), f"rms_{axis}_{name}"
    h2 = w["ax"] ** 2 + w["ay"] ** 2
    assert close(X[:, IDX["msdv"]], np.sqrt(np.cumsum(h2 * DT))[at])
