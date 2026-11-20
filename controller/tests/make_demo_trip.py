#!/usr/bin/env python3
"""폰 시연용 가짜 주행 trip.bin (100 Hz). 앞뒤·좌우로 크게 흔들려서 폰의 모션 큐 점이 잘 보이게 한다.

  python3 controller/tests/make_demo_trip.py out/demo/trip.bin [--seconds 600]

- ax: 8초 주기 ±1.5 m/s² (가속·제동), ay: 12초 주기 ±2.0 m/s² (좌우 회전), 속도 15 m/s
- pred_ax_10s / pred_ay_10s: 같은 파형을 2초 앞당긴 값 (경로 예측이 있는 것처럼)
- 나머지 신호는 NaN. 위험 확률은 이 데이터와 상관없이 fake_p3가 정한다
"""
import argparse
import json
import math
import os
import struct

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--seconds", type=int, default=600)
    a = ap.parse_args()
    cols = [c["name"] for c in json.load(open(os.path.join(REPO, "protocol", "trip_format.json")))["columns"]]
    idx = {c: i for i, c in enumerate(cols)}
    nan = float("nan")
    ax = lambda t: 1.5 * math.sin(2 * math.pi * t / 8)
    ay = lambda t: 2.0 * math.sin(2 * math.pi * t / 12)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "wb") as f:
        f.write(struct.pack("<3d", 1, len(cols), 100))
        for k in range(a.seconds * 100):
            t = k / 100
            r = [nan] * len(cols)
            r[idx["t"]] = t
            r[idx["ax"]], r[idx["ay"]], r[idx["az"]] = ax(t), ay(t), 9.81
            for g in ("gx", "gy", "gz"):
                r[idx[g]] = 0.0
            r[idx["speed"]] = 15.0
            r[idx["pred_ax_10s"]], r[idx["pred_ay_10s"]] = ax(t + 2), ay(t + 2)
            f.write(struct.pack(f"<{len(cols)}d", *r))
    print(f"{a.out}: {a.seconds} s, {len(cols)} columns")


if __name__ == "__main__":
    main()
