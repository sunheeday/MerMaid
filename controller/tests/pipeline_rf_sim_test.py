#!/usr/bin/env python3
"""Pi 없이 P1 → P2 → P3(대역) → P4 → P5 → P6 → ESP32(흉내)를 실제 프로세스로 함께 돌리는 시험.

  python3 controller/tests/pipeline_rf_sim_test.py <build 폴더>     (ctest의 pipeline_rf_sim이 부른다)

- P1, P2, P4, P5, P6은 Pi와 같은 qnx/*.cpp. P3만 LightGBM 없이 고정 확률을 내는 tests/fake_p3.cpp
- QNX 메시지 전달은 tests/qnxsim, UART는 가상 터미널 (rf_sim_test.py의 Bridge)
- 확인: P4가 DEV_SYNC 명부로 폰·HUD를 등록, PROF가 P4 → P3 프로필로, 위험이 높으면 TIER_CMD → INT,
  Tier 3 동안 P2의 MOTION이 MOT로, 폰의 OVR가 P4 정책을 거쳐 INT off, 프로필 저장, 실패 0
"""
import json
import math
import os
import re
import shutil
import struct
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rf_sim_test import INT, MOT, Bridge, Proc, check, failures  # noqa: E402

BUILD = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else "build")
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PHONE = "phone-a"
SECONDS = 180  # Tier 3은 12주기(120초) 연속이어야 켜진다 (policy.json hold_cycles)
SPEED = 10


def write_trip(path):
    """SECONDS초 가짜 주행 (100 Hz). 없는 신호는 NaN, 예측 열은 고정값"""
    cols = [c["name"] for c in json.load(open(os.path.join(REPO, "protocol", "trip_format.json")))["columns"]]
    idx = {c: i for i, c in enumerate(cols)}
    nan = float("nan")
    with open(path, "wb") as f:
        f.write(struct.pack("<3d", 1, len(cols), 100))
        for k in range(SECONDS * 100):
            t = k / 100
            r = [nan] * len(cols)
            r[idx["t"]] = t
            r[idx["ax"]] = 1.5 * math.sin(2 * math.pi * t / 6)
            r[idx["ay"]] = 1.0 * math.sin(2 * math.pi * t / 10)
            r[idx["az"]] = 9.81
            for g in ("gx", "gy", "gz"):
                r[idx[g]] = 0.0
            r[idx["speed"]] = 15.0
            r[idx["pred_ax_10s"]] = 0.5
            r[idx["pred_ay_10s"]] = -0.25
            f.write(struct.pack(f"<{len(cols)}d", *r))


def main():
    names = ("p1_acq", "p2_feat", "fake_p3", "p4_policy", "p5_devmgr", "p6_comm")
    for b in names:
        if not os.path.exists(os.path.join(BUILD, b + "_sim")):
            print(f"missing {BUILD}/{b}_sim (cmake --build 먼저)")
            return 2

    tmp = tempfile.mkdtemp(prefix="pipesim", dir="/tmp")
    env = dict(os.environ, QNXSIM_DIR=os.path.join(tmp, "ipc"))
    state = os.path.join(tmp, "state")
    os.makedirs(state)
    trip = os.path.join(tmp, "trip.bin")
    write_trip(trip)
    log = os.path.join(tmp, "cycles.csv")

    br = Bridge()
    procs = {}

    def start(name, *args):
        procs[name] = Proc(name, [os.path.join(BUILD, name + "_sim"), *args], tmp, env)
        return procs[name]

    try:
        print("[1] P6 → P5 → P4 → P3 대역 → P2 순서로 띄우기")
        p6 = start("p6_comm", "--dev", br.path)
        check(p6.wait_log("p6_comm up"), "p6_comm up")
        p5 = start("p5_devmgr", "--state", state)
        p4 = start("p4_policy", "--config", os.path.join(REPO, "controller", "config"), "--state", state,
                   "--log", log, "--rf", "--once")
        p3 = start("fake_p3", "--p", "0.95,0.95,0.95,0.95,0.95", "--once")
        check(p3.wait_log("fake_p3 up", 0, 10), "fake P3 up (P4 found)")
        p2 = start("p2_feat", "--motion", "--once")
        time.sleep(1.0)

        print("[2] 폰과 HUD가 붙고 화면·프로필을 보냄")
        br.hello(1, "app", PHONE)
        br.hello(2, "hud", "hud-01")
        time.sleep(0.5)
        br.send('1|{"t":"SCR","on":true,"att":true}')
        br.send('2|{"t":"SCR","on":true,"att":false}')
        br.send('1|{"t":"PROF","mssq":30.0,"sens":1.6}')
        time.sleep(1.0)
        check(p5.wait_log(r"hello conn 1 .*phone-a", 0, 3), "P5 registers phone-a")

        print(f"[3] P1이 {SECONDS}초 주행을 {SPEED}배속으로 재생")
        m = br.mark()
        p1 = start("p1_acq", trip, "--speed", str(SPEED), "--timeout-ms", "200")  # Mac은 실시간 OS가 아님. 시간은 Pi에서 확인
        check(p4.wait_log(r"REG phone phone-a on", 0, 5), "P4 registers phone-a from the DEV_SYNC reply")
        check(p4.wait_log(r"REG hud on", 0, 1), "P4 registers the HUD")
        check(p3.wait_log(r"PROFILES 1 phone-a:30\.00:1\.600:nan", 0, 8),
              "P3 gets phone-a with the PROF values (EV NaN)")
        check(br.wait(INT.format(c=2, t=1, on="true"), m, 8) is not None, "high risk -> Tier 1 on at the HUD")
        on3 = br.wait(INT.format(c=1, t=3, on="true"), m, 130 / SPEED + 5)
        check(on3 is not None, "high risk -> Tier 3 on at the phone")
        mot = br.wait(MOT.format(c=1), m, 3)
        check(mot is not None, "P2 MOTION reaches the phone as MOT while Tier 3 is on")
        if mot:
            j = json.loads(mot[1].split("|", 1)[1])
            check(j.get("pax") == 0.5 and j.get("pay") == -0.25, f"MOT carries pred_ax/ay_10s ({j})")

        print("[4] 폰이 점 찍기를 끄면 P4 정책을 거쳐 꺼짐")
        s4 = p4.size()
        m = br.mark()
        br.send(f'1|{{"t":"OVR","dev":"{PHONE}","on":false}}')
        check(p4.wait_log(rf"CMD [\d.]+ {PHONE} 3 0", s4, 3), "P4 turns Tier 3 off (CMD)")
        check(br.wait(INT.format(c=1, t=3, on="false"), m, 3) is not None, "phone gets INT tier 3 off")

        print("[5] 주행 끝: 통계와 저장")
        for name in ("p1_acq", "p2_feat", "fake_p3", "p4_policy"):
            pr = procs[name].p
            try:
                rc = pr.wait(SECONDS / SPEED + 20)
            except Exception:
                rc = None
            check(rc == 0, f"{name} exits 0 ({rc})")
        stats = {}
        for name in ("p2_feat", "p4_policy"):
            for line in procs[name].text().splitlines():
                if line.startswith('{"proc"'):
                    stats[name] = json.loads(line)
        s2, s4 = stats.get("p2_feat", {}), stats.get("p4_policy", {})
        check(s2.get("seq_gaps") == 0 and s2.get("received") == SECONDS * 100, f"P2 got every sample ({s2})")
        check(s2.get("mot_sent", 0) >= SECONDS * 10 - 1 and s2.get("mot_fail") == 0, "P2 sent MOTION at 10 Hz")
        check(s4.get("cycles") == SECONDS // 10 and s4.get("risk_gaps") == 0, f"P4 cycles ({s4})")
        check(s4.get("rf_phones") == 1 and s4.get("rf_hud") == 1, "P4 ends with phone-a and the HUD")
        check(s4.get("sync_fail") == 0 and s4.get("tier_cmd_fail") == 0, "no DEV_SYNC or TIER_CMD failures")
        prof = os.path.join(state, "profiles", PHONE + ".json")
        check(os.path.exists(prof) and json.load(open(prof)).get("mssq") == 30.0, "P4 saved phone-a's profile")
        with open(log) as f:
            head = f.readline().strip()
        check(head.endswith("airflow-01,display-01,own_phone"), f"cycles.csv columns ({head})")
        for name in ("p5_devmgr", "p6_comm"):
            check(procs[name].p.poll() is None, f"{name} still running")
    finally:
        br.stop = True
        for p in procs.values():
            p.kill()
        if failures:
            for name, p in procs.items():
                print(f"----- {name}.log (last 30 lines)")
                print("\n".join(p.text().splitlines()[-30:]))
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\npipeline_rf_sim: {'all passed' if not failures else str(len(failures)) + ' failure(s)'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
