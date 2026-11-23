"""주행 기록(sqlite) → <주행>/raw_caraxis/ (차 축으로 축 순서만 바꾼 100 Hz 원본). 다른 도구들의 입력이다.

Pi 기록은 그때 Pi에 들어 있던 imu_mount R로 이미 돌아가 있다(meta의 imu_mount). 이 스크립트는
    센서 축 = Rᵀ · 기록 값        (R을 되돌림, 기울기 보정이 들어 있었어도 지운다)
    차 축   = R_axes · 센서 축    (--forward/--left: 축 순서·부호만)
으로 만든다. 기울기는 run_calibration.py 가 다시 구한다.

출력
    raw_caraxis/samples_100hz.npy   float32 [t = 번호/100, ax, ay, az, gx, gy, gz, hold]
    raw_caraxis/transform.json      R_car_from_sensor(= R_axes), 기록 당시 R

쓰는 법:
    .venv/bin/python make_caraxis.py <주행 폴더> [--forward=+x] [--left=+y]
"""
import argparse
import glob
import json
import os
import sqlite3
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from make_mount import AXES, axes_rotation  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ride")
    ap.add_argument("--forward", default="+x", choices=AXES)
    ap.add_argument("--left", default="+y", choices=AXES)
    a = ap.parse_args()

    dbs = sorted(glob.glob(os.path.join(a.ride, "logs", "rides", "*.sqlite")))
    if len(dbs) != 1:
        raise SystemExit(f"주행 파일이 {len(dbs)}개: {dbs}")
    con = sqlite3.connect(f"file:{dbs[0]}?mode=ro", uri=True)
    meta = dict(con.execute("select key, value from meta").fetchall())
    rows = con.execute("select seq0, n, data, hold from samples order by seq0").fetchall()
    con.close()
    seq = np.array([r[0] for r in rows])
    n = np.array([r[1] for r in rows])
    if (np.diff(seq) != n[:-1]).any():
        raise SystemExit("샘플 번호가 이어지지 않음 (링 넘침으로 빠진 묶음). 이 도구는 이어진 기록만 다룬다")
    d = np.concatenate([np.frombuffer(r[2], "<f4").reshape(r[1], 6) for r in rows]).astype(float)
    hold = np.concatenate([np.unpackbits(np.frombuffer(r[3], np.uint8), bitorder="little")[:r[1]] if r[3] is not None
                           else np.zeros(r[1], np.uint8) for r in rows]).astype(float)

    R_rec = np.array(json.loads(meta.get("imu_mount", '{"R": [[1,0,0],[0,1,0],[0,0,1]]}'))["R"], float)
    R_axes = axes_rotation(a.forward, a.left)
    M = R_axes @ R_rec.T                       # 기록 값 → 차 축(축만)
    car = np.column_stack([np.arange(len(d)) / 100.0, d[:, :3] @ M.T, d[:, 3:] @ M.T, hold])

    out = os.path.join(a.ride, "raw_caraxis")
    os.makedirs(out, exist_ok=True)
    np.save(os.path.join(out, "samples_100hz.npy"), car.astype(np.float32))
    info = {"source": os.path.relpath(dbs[0], a.ride),
            "transform": f"axis relabel only (forward={a.forward}, left={a.left}); recorded imu_mount R undone",
            "R_car_from_sensor": R_axes.tolist(), "recorded_imu_mount_R": R_rec.tolist(),
            "car_axes": "x forward, y left, z up", "samples": int(len(d)), "hold": int(hold.sum())}
    json.dump(info, open(os.path.join(out, "transform.json"), "w"), ensure_ascii=False, indent=1)
    print(json.dumps(info, ensure_ascii=False))


if __name__ == "__main__":
    main()
