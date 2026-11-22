"""MPU-6050 녹화 CSV → 재생용 trip.bin (개발 PC에서 변환. QNX strtod가 소수를 1~2 ULP 다르게 읽기 때문)

    python3 controller/tools/csv2bin.py controller/bringup/recordings/imu.csv ml/data/trips/imu.bin

입력: controller/bringup/mpu_test.c --csv 형식 `t_ns,ax,ay,az,gx,gy,gz,dt_us` (m/s², dps)
출력: protocol/trip_format.json, 100 Hz 격자에 선형 보간. 차량 신호는 모두 NaN.
센서 축이 차량 축(x 앞, y 왼쪽, z 위)과 다르면 --axes로 바꾼다. 예: --axes -y,x,z
"""
import argparse
import pathlib
import sys

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "ml"))
import trip  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("csv")
ap.add_argument("out")
ap.add_argument("--axes", default="x,y,z", help="차량 x,y,z가 센서의 어느 축인지 (부호 포함)")
a = ap.parse_args()

d = np.genfromtxt(a.csv, delimiter=",", names=True)
t = (d["t_ns"] - d["t_ns"][0]) * 1e-9
acc = {"x": d["ax"], "y": d["ay"], "z": d["az"]}
gyr = {"x": np.radians(d["gx"]), "y": np.radians(d["gy"]), "z": np.radians(d["gz"])}
pick = lambda src, spec: (-1.0 if spec.startswith("-") else 1.0) * src[spec.lstrip("-")]
spec = a.axes.split(",")
assert len(spec) == 3 and sorted(s.lstrip("-") for s in spec) == ["x", "y", "z"], "--axes 예: x,y,z 또는 -y,x,z"

T = np.arange(0, t[-1], 1 / trip.FS)
rows = trip.empty(len(T))
for out_axis, s in zip("xyz", spec):
    rows[:, trip.COL["a" + out_axis]] = np.interp(T, t, pick(acc, s))
    rows[:, trip.COL["g" + out_axis]] = np.interp(T, t, pick(gyr, s))
trip.write_trip(a.out, rows)
gaps = np.diff(t)
print(f"{len(t)} samples, {t[-1]:.1f} s -> {len(T)} rows at 100 Hz (max gap {gaps.max() * 1e3:.1f} ms) -> {a.out}")
