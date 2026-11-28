#!/usr/bin/env python3
"""IMU 없이 P1 LIVE(가짜 MPU) → P2 → P3(대역) → P4를 실제 프로세스로 함께 돌리는 시험.

  python3 controller/tests/live_fake_sim_test.py <build 폴더>     (ctest의 live_fake_sim이 부른다)

- P1은 `--live --fake-imu`: 실제와 같은 imu::Acquirer 경로 (초기화 → 정지 2초 보정 → RIDE_START → FIFO 읽기)
- 확인: 보정 뒤 RIDE_START, 21초 주행에 100 Hz 샘플이 빠짐 없이 P2로, 10초 주기 2번이 P4까지,
  자이로 바이어스가 state/imu_bias.json에 저장, --duration-s로 RIDE_END, 전송 실패 0
- P2는 `--xslope`(횡단 경사 제거)를 켠다: GPS가 없으니 뺀 값 0, 1초 기록이 주행 길이만큼 (켜도 경로가 안 깨지는지)
- 약 30초 걸린다. Mac은 실시간 OS가 아니라 시간 측정은 하지 않는다 (Pi에서 확인)
"""
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rf_sim_test import Proc, check, failures, v1_config  # noqa: E402

BUILD = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else "build")
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
RIDE_S = 21


def main():
    for b in ("p1_acq", "p2_feat", "fake_p3", "p4_policy"):
        if not os.path.exists(os.path.join(BUILD, b + "_sim")):
            print(f"missing {BUILD}/{b}_sim (cmake --build 먼저)")
            return 2
    tmp = tempfile.mkdtemp(prefix="livesim", dir="/tmp")  # Unix 소켓 경로 길이 제한 때문에 짧은 경로
    env = dict(os.environ, QNXSIM_DIR=os.path.join(tmp, "ipc"))
    state = os.path.join(tmp, "state")
    os.makedirs(state)
    log = os.path.join(tmp, "cycles.csv")
    procs = {}

    def start(name, *args):
        procs[name] = Proc(name, [os.path.join(BUILD, name + "_sim"), *args], tmp, env)
        return procs[name]

    try:
        start("p4_policy", "--config", v1_config(), "--state", state, "--log", log,
              "--hud", "--phone", "phone-a:20:1.0", "--once")
        p3 = start("fake_p3", "--p", "0.95,0.9,0.5,0.2,0.1", "--once")
        check(p3.wait_log("fake_p3 up", 0, 10), "fake P3 up (P4 found)")
        xs_log = os.path.join(tmp, "p2_xslope.csv")
        start("p2_feat", "--once", "--xslope", "--xslope-log", xs_log)
        p1 = start("p1_acq", "--live", "--fake-imu", "--state", state, "--duration-s", str(RIDE_S),
                   "--mount", os.path.join(v1_config(), "imu_mount.json"),
                   "--timeout-ms", "200", "--ring", "none")
        check(p1.wait_log("RIDE_START", 0, 15), "RIDE_START after calibration")
        check(p1.wait_log("gyro bias", 0, 5), "gyro bias reported")
        check(p1.wait_log("RIDE_END", 0, RIDE_S + 10), "RIDE_END after --duration-s")
        for n, pr in procs.items():
            try:
                rc = pr.p.wait(15)
            except subprocess.TimeoutExpired:
                rc = None
            check(rc == 0, f"{n} exit code 0 (got {rc})")

        stats = {}
        for line in procs["p1_acq"].text().splitlines():
            if line.startswith("{"):
                stats["p1_acq"] = json.loads(line)
        s1 = stats.get("p1_acq", {})
        check(s1.get("mode") == "live-fake", "P1 live-fake mode")
        check(s1.get("rides") == 1 and s1.get("restarts") == 0, f"one ride, no restart: {s1}")
        check(abs(s1.get("samples", 0) - RIDE_S * 100) <= 2, f"100 Hz samples: {s1.get('samples')}")
        check(s1.get("send_fail") == 0 and s1.get("edge_fail") == 0, "no send failures")
        check(s1.get("hold") == 0 and s1.get("overflow") == 0, "no hold, no overflow")

        for line in procs["p2_feat"].text().splitlines():
            if line.startswith("{"):
                stats["p2_feat"] = json.loads(line)
        s2 = stats.get("p2_feat", {})
        check(abs(s2.get("xslope_s", 0) - RIDE_S) <= 1 and s2.get("xslope_used_s") == 0 and s2.get("xslope_last") == 0,
              f"P2 cross-slope: no GPS -> nothing removed: {s2}")
        xs_rows = list(csv.DictReader(open(xs_log))) if os.path.exists(xs_log) else []
        check(abs(len(xs_rows) - RIDE_S) <= 1 and all(r["use"] == "0" for r in xs_rows), f"xslope log lines ({len(xs_rows)})")

        bias_path = os.path.join(state, "imu_bias.json")
        bias = json.load(open(bias_path))["gyro_bias_dps"] if os.path.exists(bias_path) else None
        check(bias is not None and abs(bias[0] - 1.0) < 1e-6 and abs(bias[1] + 66 / 131) < 1e-6,
              f"bias saved: {bias}")

        rows = list(csv.DictReader(open(log))) if os.path.exists(log) else []
        check(len(rows) == 2, f"two 10 s cycles reached P4 ({len(rows)})")
        if rows:
            check(abs(float(rows[0]["t"]) - 9.99) < 1e-6, f"first cycle at t = 9.99 ({rows[0]['t']})")
    finally:
        for p in procs.values():
            p.kill()
        if not failures:
            shutil.rmtree(tmp, ignore_errors=True)
        else:
            print(f"logs kept in {tmp}")
    print("live_fake_sim: all passed" if not failures else f"live_fake_sim: {len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
