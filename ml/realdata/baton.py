"""BATON / CANTraj 리더 (데이터 CC BY-NC 4.0, github.com/OpenLKA/BATON, HF HenryYHW/BATON-Sample).

    <root>/driver_N/route_M/imu.csv, vehicle_dynamics.csv  (HF 저장소 실제 구조. README는 차종/운전자/경로 3단이라 적음)
imu.csv: time_s, accel_x..z, gyro_x..z (100 Hz, comma three, 축은 문서에 없음 → convert.align)
vehicle_dynamics.csv: time_s, vEgo, aEgo, gas, brake, gasPressed, brakePressed, cruiseState_enabled, ...
열 이름은 BATON baseline/config.py(IMU_COLS, VEHICLE_COLS) 기준. 실제 파일로는 아직 확인 못 함:
HF에서 약관 동의(gated)가 필요하다. gas/brake가 전부 0이면(차종이 안 주는 경우) NaN으로 둔다.
"""
import pathlib

import numpy as np
import pandas as pd

MIN_MINUTES = 5.0
IMU = ["accel_x", "accel_y", "accel_z"], ["gyro_x", "gyro_y", "gyro_z"]
EXTRA = {"gas": "accel_pedal", "brake": "brake_pedal", "cruiseState_enabled": "cruise_on"}


def routes(root):
    out = []
    for imu in sorted(pathlib.Path(root).rglob("imu.csv")):  # 폴더 깊이는 가정하지 않는다
        d = imu.parent
        if (d / "vehicle_dynamics.csv").exists():
            out.append((d, None))
    return out


def read(route_dir, _=None):
    imu = pd.read_csv(route_dir / "imu.csv")
    veh = pd.read_csv(route_dir / "vehicle_dynamics.csv")
    ti, tv = imu["time_s"].to_numpy(float), veh["time_s"].to_numpy(float)
    extra = {}
    for src, col in EXTRA.items():
        if src in veh:
            x = veh[src].astype(float).to_numpy()
            if np.nanmax(np.abs(x)) > 0:
                extra[col] = (tv, x)
    return {"name": f"{route_dir.parent.name}_{route_dir.name}",
            "acc": (ti, imu[IMU[0]].to_numpy(float)),
            "gyr": (ti, imu[IMU[1]].to_numpy(float)),
            "speed": (tv, veh["vEgo"].to_numpy(float)),
            "extra": extra}
