"""BATON / CANTraj 리더 (데이터 CC BY-NC 4.0, github.com/OpenLKA/BATON, HF HenryYHW/BATON-Sample).

    <root>/driver_N/route_M/imu.csv, vehicle_dynamics.csv  (HF 저장소 실제 구조. README는 차종/운전자/경로 3단이라 적음)
imu.csv: time_s, accel_x..z, gyro_x..z (100 Hz, comma three, 축은 문서에 없음 → convert.align)
vehicle_dynamics.csv: time_s, vEgo, aEgo, gas, brake, gasPressed, brakePressed, cruiseState_enabled, ...
BATON-Sample로 확인한 것 (2026-11-12):
- IMU는 comma three 기기 축 그대로 (정차 때 accel_x ≈ +9.8) → convert.align이 차량축으로 돌린다.
- vEgo에 정차 중 0.4초쯤 79.9 m/s로 튀는 값(CAN 무효값)이 있다 → 3초 중앙값에서 5 m/s 넘게 벗어난 샘플을
  지우고 보간 (단조로운 가감속은 창 중앙값이 곧 그 값이라 걸리지 않는다).
- gas/brake 단위가 차종마다 다르다 (0~1, 0~200, 0~5 등) → 0~1 페달 칸에 넣지 않는다 (NaN).
- 운전 보조 작동(cc_latActive 또는 cc_longActive)은 trip.bin에 없는 칸이라 분석용 태그 "das"로 넘긴다.
HF에서 약관 동의(gated)가 필요하다.
"""
import pathlib

import numpy as np
import pandas as pd

MIN_MINUTES = 5.0
IMU = ["accel_x", "accel_y", "accel_z"], ["gyro_x", "gyro_y", "gyro_z"]
EXTRA = {"cruiseState_enabled": "cruise_on"}
DAS = ["cc_latActive", "cc_longActive"]


def routes(root):
    out = []
    for imu in sorted(pathlib.Path(root).rglob("imu.csv")):  # 폴더 깊이는 가정하지 않는다
        d = imu.parent
        if (d / "vehicle_dynamics.csv").exists():
            out.append((d, None))
    return out


def despike(v, window=301, tol=5.0):
    med = pd.Series(v).rolling(window, center=True, min_periods=1).median().to_numpy()
    bad = np.abs(v - med) > tol
    if bad.any() and not bad.all():
        i = np.arange(len(v))
        v = v.copy()
        v[bad] = np.interp(i[bad], i[~bad], v[~bad])
    return v


def read(route_dir, _=None):
    """5분 미만 경로는 None"""
    veh = pd.read_csv(route_dir / "vehicle_dynamics.csv",
                      usecols=lambda c: c in {"time_s", "vEgo", *EXTRA, *DAS})
    tv = veh["time_s"].to_numpy(float)
    if len(tv) < 2 or tv[-1] - tv[0] < MIN_MINUTES * 60:
        return None
    imu = pd.read_csv(route_dir / "imu.csv")
    ti = imu["time_s"].to_numpy(float)
    extra = {col: (tv, veh[src].astype(float).to_numpy()) for src, col in EXTRA.items() if src in veh}
    das = np.max([veh[c].astype(float).to_numpy() for c in DAS if c in veh], axis=0)
    return {"name": f"{route_dir.parent.name}_{route_dir.name}",
            "acc": (ti, imu[IMU[0]].to_numpy(float)),
            "gyr": (ti, imu[IMU[1]].to_numpy(float)),
            "speed": (tv, despike(veh["vEgo"].to_numpy(float))),
            "extra": extra,
            "tags": {"das": (tv, das)}}
