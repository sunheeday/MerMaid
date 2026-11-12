"""BATON / CANTraj 리더 (데이터 CC BY-NC 4.0, github.com/OpenLKA/BATON, HF HenryYHW/BATON-Sample).

    <root>/{vehicle_model}/{driver_id}/{route_hash}/imu.csv, vehicle_dynamics.csv
imu.csv: time_s, accel_x..z, gyro_x..z (100 Hz, comma three, 축은 문서에 없음 → common.align)
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
    for imu in sorted(pathlib.Path(root).glob("*/*/*/imu.csv")):
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
    rel = route_dir.relative_to(route_dir.parents[2])
    return {"name": "_".join(rel.parts),
            "acc": (ti, imu[IMU[0]].to_numpy(float)),
            "gyr": (ti, imu[IMU[1]].to_numpy(float)),
            "speed": (tv, veh["vEgo"].to_numpy(float)),
            "extra": extra}
