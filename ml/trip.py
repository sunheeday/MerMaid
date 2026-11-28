"""trip.bin / features.bin 읽기와 쓰기, featurize 실행 (protocol/trip_format.json)."""
import json
import pathlib
import os
import subprocess

import numpy as np

REPO = pathlib.Path(__file__).resolve().parents[1]
FORMAT = json.loads((REPO / "protocol" / "trip_format.json").read_text())
FEATURES = json.loads((REPO / "protocol" / "feature_order.json").read_text())
COLUMNS = [c["name"] for c in FORMAT["columns"]]
COL = {n: i for i, n in enumerate(COLUMNS)}
FIDX = {f["name"]: f["index"] for f in FEATURES["features"]}  # 피처 이름 → 모델 입력 칸
NF = FEATURES["n_features"]                  # 모델 입력 칸 수
NV = FEATURES["vehicle_range"][1] + 1        # 차량 피처 수 (featurize와 P2가 만드는 앞쪽 칸)
FS = 100.0
# MM_FEATURIZE: 긴 데이터 생성 중에 build/를 다시 빌드해도 안 깨지게 복사본을 쓸 때
FEATURIZE = pathlib.Path(os.environ["MM_FEATURIZE"]) if os.environ.get("MM_FEATURIZE") else REPO / "build" / "featurize"


def empty(n):
    """n행짜리 빈 주행 (모든 신호 NaN, t만 채움)."""
    rows = np.full((n, len(COLUMNS)), np.nan)
    rows[:, COL["t"]] = np.arange(n) / FS
    return rows


def write_trip(path, rows):
    rows = np.asarray(rows, dtype="<f8")
    assert rows.ndim == 2 and rows.shape[1] == len(COLUMNS)
    with open(path, "wb") as f:
        np.array([FORMAT["version"], len(COLUMNS), FS], dtype="<f8").tofile(f)
        rows.tofile(f)


def read_features(path):
    """→ (t, X[:, NV]). 차량 피처만 들어 있다."""
    raw = np.fromfile(path, dtype="<f8")
    version, ncol = raw[:2]
    assert int(version) == FEATURES["version"], "feature_order version mismatch"
    m = raw[2:].reshape(-1, int(ncol))
    return m[:, 0], m[:, 1:]


def featurize(trip_path, out_path, oman_k=None):
    """C++ featurize 실행. 먼저 cmake -S controller -B build && cmake --build build"""
    if not FEATURIZE.exists():
        raise FileNotFoundError(f"{FEATURIZE} 없음: cmake -S controller -B build && cmake --build build")
    cmd = [str(FEATURIZE), str(trip_path), str(out_path)]
    if oman_k is not None:
        cmd += ["--oman-k", repr(float(oman_k))]
    subprocess.run(cmd, check=True, capture_output=True)
    return read_features(out_path)


def full_vector(Xv, mssq, sens, ev, vehicle_type):
    """차량 피처(n x NV) + 승객 피처 → 모델 입력 (n x NF). P3와 같이 G8(허용도)은 NaN, enabled=false 칸은 NaN"""
    X = np.full((len(Xv), NF), np.nan)
    X[:, :NV] = Xv
    X[:, FIDX["mssq"]], X[:, FIDX["sensitivity"]] = mssq, sens
    X[:, FIDX["ev_experience"]], X[:, FIDX["vehicle_type"]] = ev, vehicle_type
    for f in FEATURES["features"]:
        if not f["enabled"]:
            X[:, f["index"]] = np.nan
    return X
