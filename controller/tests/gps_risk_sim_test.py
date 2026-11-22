#!/usr/bin/env python3
"""폰 GPS(M09) → 주행 파일·속도 피처 (#18), 위험 확률 M10 RISK → 폰 (#19):
가짜 폰 2대 + HUD + P6·P5 + 진짜 P1(LIVE, 가짜 IMU)·P2·P4 + P3 대역 + P7.

  python3 controller/tests/gps_risk_sim_test.py <build 폴더>     (ctest의 gps_risk_sim이 부른다)

- 폰 A(conn 0)는 spd 10 m/s, acc 4 m, 폰 B(conn 1)는 spd 20 m/s, acc 15 m를 5 Hz로 보낸다 → P2는 더 정확한 A의 속도를 쓴다
- 주행 22초쯤 두 폰 모두 GPS를 멈춘다 → 3초 뒤부터 속도 없음(NaN)
- 확인: ① 주행 파일 gps 표에 두 폰의 행 (t, spd, acc, brg, alt NULL) ② 10초·20초 주기의 infer x에서 G2 피처
  (speed_mean_10s 등)가 NaN이 아니고 A의 속도(10) ③ 30초 주기는 G2가 NaN ④ 잘못된 GPS는 ctx REJECT
- RISK (#19): 폰마다 추론 주기(10초)마다 {"t":"RISK","p":[0.95,0.90,0.50,0.20,0.10]} (P3 대역의 값), HUD에는 없음,
  주행 파일 ctx에 RISK_OUT, P4 통계 risk_fwd
Mac은 실시간 OS가 아니라 시간은 느슨하게 본다. 약 50초.
"""
import json
import os
import re
import shutil
import struct
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from log_rf_sim_test import CONFIG, Run, rows  # noqa: E402
from rf_sim_test import INT, check, failures  # noqa: E402

BUILD = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else "build")
PHONE_A, PHONE_B = "phone-a", "phone-b"
RIDE_S = 32
GPS_STOP_S = 22
F_SPEED_MEAN_10S, F_SPEED_MAX_5M = 33, 39  # protocol/feature_order.json


def main():
    for b in ("p1_acq", "p2_feat", "fake_p3", "p4_policy", "p5_devmgr", "p6_comm", "p7_logger"):
        if not os.path.exists(os.path.join(BUILD, b + "_sim")):
            print(f"missing {BUILD}/{b}_sim (cmake --build 먼저)")
            return 2
    tmp = tempfile.mkdtemp(prefix="gps", dir="/tmp")  # Unix 소켓 경로 길이 제한
    run = Run(tmp, "a", f"/mmgp{os.getpid()}")
    stop_gps = threading.Event()
    try:
        br = run.br
        p7 = run.start_p7()
        p6 = run.start("p6_comm", "--dev", br.path)
        check(p6.wait_log("p6_comm up"), "p6_comm up")
        p5 = run.start("p5_devmgr", "--state", run.state, "--safety-s", "30")
        check(p5.wait_log("p5_devmgr up"), "p5_devmgr up")
        time.sleep(0.3)
        m = br.mark()
        br.hello(0, "app", PHONE_A)
        br.hello(1, "app", PHONE_B)
        check(br.wait(INT.format(c=0, t=3, on="false"), m, 3) is not None, "phone A registered")
        check(br.wait(INT.format(c=1, t=3, on="false"), m, 3) is not None, "phone B registered")
        br.hello(2, "hud", "hud-01")
        check(br.wait(INT.format(c=2, t=2, on="false"), m, 3) is not None, "HUD registered")

        def gps_loop():
            while not stop_gps.is_set():
                br.send('0|{"t":"GPS","spd":10.00,"brg":90.0,"acc":4.0}')
                br.send('1|{"t":"GPS","spd":20.00,"acc":15.0,"alt":45.1}')
                time.sleep(0.2)

        threading.Thread(target=gps_loop, daemon=True).start()

        run.start("p4_policy", "--config", CONFIG, "--state", run.state, "--log", os.path.join(run.dir, "cycles.csv"),
                  "--rf", "--hud", "--once")
        p3 = run.start("fake_p3", "--p", "0.95,0.9,0.5,0.2,0.1", "--once")
        check(p3.wait_log("fake_p3 up", 0, 10), "fake P3 up")
        p2 = run.start("p2_feat", "--motion", "--once")
        p1 = run.start("p1_acq", "--live", "--fake-imu", "--state", run.state, "--duration-s", str(RIDE_S),
                       "--mount", os.path.join(CONFIG, "imu_mount.json"), "--timeout-ms", "200", "--ring", run.ring)
        check(p1.wait_log("RIDE_START", 0, 15), "RIDE_START")
        t0 = time.monotonic()
        time.sleep(1.0)
        br.send('0|{"t":"GPS","spd":150,"acc":3}')  # 거부: 속도 범위 밖
        br.send('0|{"t":"GPS","spd":10,"acc":3,"lat":37.5}')  # 위도는 모르는 칸이라 무시 (기록하지 않음)
        time.sleep(max(0.0, t0 + GPS_STOP_S - time.monotonic()))
        stop_gps.set()
        check(p1.wait_log("RIDE_END", 0, RIDE_S + 10 - (time.monotonic() - t0)), "RIDE_END")
        for n in ("p1_acq", "p2_feat", "fake_p3", "p4_policy"):
            try:
                rc = run.procs[n].p.wait(15)
            except Exception:
                rc = None
            check(rc in (0, 1) if n == "p1_acq" else rc == 0, f"{n} exited ({rc})")
        check(p7.wait_log("RIDE_CLOSED .* ride_end", 0, 10), "P7 closed the ride file")
        try:
            p7.p.wait(10)
        except Exception:
            pass
        st2 = [json.loads(l) for l in p2.text().splitlines() if l.startswith('{"proc"')]
        if check(bool(st2), "P2 stats line"):
            check(st2[-1].get("gps_rx", 0) > 50 and st2[-1].get("gps_filled", 0) > 1000,
                  f"P2 got GPS_SPEED from P6 and filled samples (rx {st2[-1].get('gps_rx')}, filled {st2[-1].get('gps_filled')})")
        # P2가 뜨기 전에 보낸 것은 실패로 세어진다 (그동안 P6은 멈추지 않는다: 3 ms 제한)
        st6 = re.findall(r"gps (\d+) \(to P2 (\d+), fail (\d+)\)", p6.text())
        check(bool(st6) and int(st6[-1][1]) >= 100, f"P6 sent GPS speed to P2 (gps, to P2, fail = {st6[-1] if st6 else '-'})")

        f = os.path.join(run.logs, "rides", "pi01-00001-001.sqlite")
        if not check(os.path.exists(f), "closed ride file"):
            return 1
        check(rows(f, "SELECT value FROM meta WHERE key='schema_version'") == [("2",)], "schema_version 2")
        g = rows(f, "SELECT t, phone, spd, brg, acc, alt FROM gps ORDER BY mono_ns")
        ga = [r for r in g if r[1] == PHONE_A and r[0] is not None]
        gb = [r for r in g if r[1] == PHONE_B and r[0] is not None]
        check(len(ga) >= 60 and len(gb) >= 60, f"gps rows during the ride: A {len(ga)}, B {len(gb)} (5 Hz x ~{GPS_STOP_S} s)")
        check(all(r[2] == 10.0 and r[3] == 90.0 and r[4] == 4.0 and r[5] is None for r in ga if r[4] == 4.0),
              "phone A rows: spd 10, brg 90, acc 4, alt NULL")
        check(all(r[2] == 20.0 and r[3] is None and r[4] == 15.0 and r[5] == 45.1 for r in gb), "phone B rows: brg NULL, alt 45.1")
        check(not any(r[2] == 150 for r in g), "out-of-range GPS not in the gps table")
        check(max(r[0] for r in g if r[0] is not None) <= GPS_STOP_S + 2, "no gps rows after the phones stopped")
        rej = [json.loads(v) for (v,) in rows(f, "SELECT value FROM ctx WHERE kind='REJECT'")]
        check(any("GPS spd out of range" in j.get("reason", "") for j in rej), "bad GPS logged as REJECT")

        inf = rows(f, "SELECT seq, t, phone, x FROM infer ORDER BY seq, phone")
        by_seq = {}
        for seq, t, ph, x in inf:
            v = struct.unpack("<77d", x)
            by_seq.setdefault(seq, []).append((t, ph, v))
        print("  infer:", {s: [(round(t, 1), ph, round(v[F_SPEED_MEAN_10S], 2)) for t, ph, v in r] for s, r in by_seq.items()})
        check(len(by_seq) == 3, f"3 inference cycles ({len(by_seq)})")
        for seq in (0, 1):
            r = by_seq.get(seq, [])
            check(bool(r) and all(abs(v[F_SPEED_MEAN_10S] - 10.0) < 1e-6 and abs(v[F_SPEED_MAX_5M] - 10.0) < 1e-6
                                  for _, _, v in r),
                  f"cycle {seq}: G2 speed features from the more accurate phone A (10 m/s)")
        r = by_seq.get(2, [])
        check(bool(r) and all(v[F_SPEED_MEAN_10S] != v[F_SPEED_MEAN_10S] for _, _, v in r),
              "cycle 2 (GPS stopped > 3 s before): G2 speed features NaN again")

        # ---- #19 RISK ----
        want = '{"t":"RISK","p":[0.95,0.90,0.50,0.20,0.10]}'
        ra = [t for t, l in br.since(0) if l == "0|" + want]
        rb = [t for t, l in br.since(0) if l == "1|" + want]
        check(len(ra) == 3 and len(rb) == 3, f"each phone got RISK every inference cycle (A {len(ra)}, B {len(rb)})")
        gaps = [b - a for a, b in zip(ra, ra[1:])]
        check(all(8.0 <= g <= 12.0 for g in gaps), f"RISK about every 10 s ({[round(g, 2) for g in gaps]})")
        check(not any(l.startswith("2|") and '"RISK"' in l for _, l in br.since(0)), "HUD gets no RISK")
        check(all(l.split("|", 1)[1] == want for _, l in br.since(0) if '"RISK"' in l), "RISK lines are exactly the M10 format")
        ro = [json.loads(v) for (v,) in rows(f, "SELECT value FROM ctx WHERE kind='RISK_OUT' AND sender=?", (PHONE_A,))]
        check(len(ro) == 3 and all(j == {"conn": 0, "p": [0.95, 0.9, 0.5, 0.2, 0.1]} for j in ro),
              f"RISK_OUT in the ride file ({len(ro)} for phone A)")
        st4 = [json.loads(l) for l in run.procs["p4_policy"].text().splitlines() if l.startswith('{"proc"')]
        if check(bool(st4), "P4 stats line"):
            check(st4[-1].get("risk_fwd") == 6 and st4[-1].get("risk_fwd_fail") == 0,
                  f"P4 forwarded 2 phones x 3 cycles (risk_fwd {st4[-1].get('risk_fwd')}, fail {st4[-1].get('risk_fwd_fail')})")

        for name, p in run.procs.items():
            if name in ("p5_devmgr", "p6_comm"):
                check(p.p.poll() is None, f"{name} still running")
    finally:
        stop_gps.set()
        run.kill()
        if failures:
            for name, p in run.procs.items():
                print(f"----- {name}.log (last 30 lines)")
                print("\n".join(p.text().splitlines()[-30:]))
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\ngps_risk_sim: {'all passed' if not failures else str(len(failures)) + ' failure(s)'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
