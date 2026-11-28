#!/usr/bin/env python3
"""폰과 오간 사건이 주행 로그에 남는지: 가짜 폰 + P6·P5 + 진짜 P1(LIVE, 가짜 IMU)·P2·P4 + P3 대역 + P7.

  python3 controller/tests/log_rf_sim_test.py <build 폴더>     (ctest의 log_rf_sim이 부른다)

같은 폰 대본을 두 번 돌린다 (docs/controller/DATA_LOGGING.md §6-2, §13).
  A. P7이 살아 있음: 닫힌 주행 파일(SQLite)을 열어 ctx·label 표를 검사
     - 주행 전: CONNECT, HELLO, 첫 INT, HELLO 없는 연결의 DROP(no_hello_5s) → t NULL로 들어감
       (P5가 --safety-s 4라 주행 전에도 안전 OFF에 들어가 있다. 첫 주기의 송풍 ON은 잡혔다가 DEV_SYNC 뒤에 적용)
     - 주행 중: SCR, OVR off/on, 그에 따라 나간 INT, SYM 두 번(label), 거부(SYM 11, 남의 폰 OVR, 깨진 줄),
       5초 무응답 DROP(silent_5s) + BYE, 다른 번호로 재연결 → CONNECT·HELLO·INT·PROF 회신(PROF_OUT)
     - P5: 안전 OFF 진입·해제, 잡아 둔 ON 적용 (--safety-s 4라서 10초 주기 사이마다 생긴다)
     - HB·MOT는 없음. 주행 중 사건은 mono_ns와 주행 시각 t가 둘 다 있고, 폰이 본 순서와 같다
  B. P7이 멈춰 있다가(SIGSTOP: 보내기가 2 ms 시간 초과) 죽음(kill -9): 폰이 받는 줄이 A와 같고
     OVR → INT 왕복 시간도 같은 수준. P6은 log_fail을 세기만 한다
Mac은 실시간 OS가 아니라 시간 수치는 느슨하게 본다. Pi에서 다시 잰다. 약 1분 40초.
"""
import json
import os
import shutil
import signal
import sqlite3
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rf_sim_test import INT, Bridge, Proc, check, failures, v1_config  # noqa: E402

BUILD = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else "build")
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CONFIG = v1_config()  # V1 정책 (배포된 모델과 무관)
PHONE = "phone-a"
OTHER = "phone-zz"
RIDE_S = 32
SAFETY_S = 4


def rows(db, sql, args=()):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


class Run:
    """P6·P5 → (폰이 붙음) → P4·P3 대역·P2·P1. 폰 대본을 돌리고, 폰이 본 것을 돌려준다"""

    def __init__(self, tmp, tag, ring):
        self.dir = os.path.join(tmp, tag)
        os.makedirs(self.dir)
        self.env = dict(os.environ, QNXSIM_DIR=os.path.join(self.dir, "ipc"))
        self.state = os.path.join(self.dir, "state")
        self.logs = os.path.join(self.dir, "logs")
        os.makedirs(self.state)
        self.ring = ring
        self.br = Bridge()
        self.procs = {}

    def start(self, name, *args):
        self.procs[name] = Proc(name, [os.path.join(BUILD, name + "_sim"), *args], self.dir, self.env)
        return self.procs[name]

    def start_p7(self):
        p7 = self.start("p7_logger", "--dir", self.logs, "--config", CONFIG, "--ring", self.ring, "--device-id", "pi01",
                        "--min-free-mb", "1", "--once")
        check(p7.wait_log("logging to", 0, 10), "P7 up")
        return p7

    def drive(self, tag, mid=None):
        """폰 대본. seen: 단계마다 폰이 무엇을 받았는지 (A와 B를 비교), rtt: OVR → INT 왕복 (초)"""
        br, seen, rtt = self.br, [], []

        def step(name, got):
            seen.append((name, got is not None))
            check(got is not None, f"{tag} {name}")
            return got

        p6 = self.start("p6_comm", "--dev", br.path)
        check(p6.wait_log("p6_comm up"), f"{tag} p6_comm up")
        p5 = self.start("p5_devmgr", "--state", self.state, "--safety-s", str(SAFETY_S))
        check(p5.wait_log("p5_devmgr up"), f"{tag} p5_devmgr up")
        time.sleep(0.3)

        # ---- 주행 전: 폰이 붙고, HELLO를 안 보내는 연결이 하나 ----
        m = br.mark()
        br.hello(0, "app", PHONE)
        br.send("1|CONNECT")  # HELLO 없음 → 5초 뒤 DROP
        step("pre: INT tier 3 off after HELLO", br.wait(INT.format(c=0, t=3, on="false"), m, 3))
        br.send('0|{"t":"SCR","on":true,"att":true}')
        br.send('0|{"t":"PROF","mssq":30.0,"sens":1.6}')
        step("pre: DROP of the conn that never said HELLO", br.wait(r"^1\|DROP$", m, 7))
        br.send("1|DISCONNECT")

        # ---- 주행 시작 ----
        self.start("p4_policy", "--config", CONFIG, "--state", self.state, "--log", os.path.join(self.dir, "cycles.csv"),
                   "--rf", "--hud", "--once")
        p3 = self.start("fake_p3", "--p", "0.95,0.9,0.5,0.2,0.1", "--once")
        check(p3.wait_log("fake_p3 up", 0, 10), f"{tag} fake P3 up")
        self.start("p2_feat", "--motion", "--once")
        m = br.mark()
        p1 = self.start("p1_acq", "--live", "--fake-imu", "--state", self.state, "--duration-s", str(RIDE_S),
                        "--mount", os.path.join(CONFIG, "imu_mount.json"), "--timeout-ms", "200", "--ring", self.ring)
        check(p1.wait_log("RIDE_START", 0, 15), f"{tag} RIDE_START")
        t0 = time.monotonic()
        # 첫 주기(10초): P4가 송풍 ON → P5는 안전 OFF 중이라 잡아 두었다가 DEV_SYNC 뒤 적용 → 폰에 INT tier 1 on
        step("ride: INT tier 1 on (held by P5, applied after DEV_SYNC)", br.wait(INT.format(c=0, t=1, on="true"), m, 16))

        def ovr(on, name):
            mk = br.mark()
            t = time.monotonic()
            br.send(f'0|{{"t":"OVR","dev":"airflow-01","on":{"true" if on else "false"}}}')
            got = step(name, br.wait(INT.format(c=0, t=1, on="true" if on else "false"), mk, 3))
            if got:
                rtt.append(got[0] - t)

        ovr(False, "ride: OVR airflow off -> INT tier 1 off")
        br.send('0|{"t":"SYM","misc":4}')
        br.send('0|{"t":"SYM","misc":11}')                              # 거부: 범위 밖
        br.send(f'0|{{"t":"OVR","dev":"{OTHER}","on":true}}')           # 거부: 남의 폰
        br.send("this is not a frame")                                  # 거부: 줄 형식
        br.send('0|{"t":"SCR","on":false,"att":false}')
        time.sleep(0.3)
        ovr(True, "ride: OVR airflow on -> INT tier 1 on")
        if mid:
            mid()

        # ---- 5초 무응답 → DROP, 다른 번호로 재연결 ----
        mk = br.mark()
        br.hb_conns.discard(0)
        step("ride: DROP after 5 s of silence", br.wait(r"^0\|DROP$", mk, 8))
        mk = br.mark()
        br.hello(2, "app", PHONE)
        # 조용한 5초 사이에 P5가 안전 OFF를 했다 (마지막 DEV_SYNC에서 4초) → 다시 붙은 폰은 꺼진 상태를 받는다
        step("ride: reconnect on conn 2 gets the state again (off: P5 went safe off meanwhile)",
             br.wait(INT.format(c=2, t=1, on="false"), mk, 3))
        step("ride: reconnect gets the stored profile (PROF reply)", br.wait(r'^2\|\{"t":"PROF","mssq":30\.00,"sens":', mk, 3))
        br.send('2|{"t":"SYM","misc":2}')
        ovr2 = br.mark()
        t = time.monotonic()
        br.send('2|{"t":"OVR","dev":"airflow-01","on":false}')
        got = step("ride: OVR after reconnect -> INT tier 1 off", br.wait(INT.format(c=2, t=1, on="false"), ovr2, 3))
        if got:
            rtt.append(got[0] - t)

        check(p1.wait_log("RIDE_END", 0, RIDE_S + 10 - (time.monotonic() - t0)), f"{tag} RIDE_END")
        for n in ("p1_acq", "p2_feat", "fake_p3", "p4_policy"):
            try:
                rc = self.procs[n].p.wait(15)
            except Exception:
                rc = None
            if n == "p1_acq" and rc == 1:
                # Mac은 실시간 OS가 아니라 부하가 걸리면 P1 → P2 보내기(200 ms)가 드물게 한 번 늦는다. 이 시험이 보는 것은
                # 로그와 폰 쪽 동작이라 2번까지는 넘어간다 (P7과 무관한지는 log_sim이, 실제 수치는 Pi에서 본다)
                st = [json.loads(l) for l in self.procs[n].text().splitlines() if l.startswith('{"proc"')]
                fails = st[-1].get("send_fail", 99) if st else 99
                print(f"  note {tag} p1_acq send_fail {fails} (Mac scheduling)")
                check(fails <= 2 and st[-1].get("edge_fail") == 0, f"{tag} p1_acq sent the ride (send_fail {fails} <= 2)")
                continue
            check(rc == 0, f"{tag} {n} exit code 0 (got {rc})")
        check(p5.p.poll() is None and p6.p.poll() is None, f"{tag} P5 and P6 still running")
        # 폰이 받은 줄 (HB·MOT 빼고): A와 B가 같아야 한다. 안전 OFF 박자는 실행마다 조금 달라 INT·DROP·PROF의 종류만 본다
        got_kinds = sorted({l.split("|", 1)[0] + "|" + (json.loads(l.split("|", 1)[1]).get("t", "?") if "{" in l else l.split("|", 1)[1])
                            for _, l in br.since(0) if '"t":"HB"' not in l and '"t":"MOT"' not in l})
        return seen, rtt, got_kinds

    def kill(self):
        for p in self.procs.values():
            try:
                p.p.send_signal(signal.SIGCONT)
            except ProcessLookupError:
                pass
            p.kill()
        self.br.stop = True


def check_file(f, a):
    ctx = rows(f, "SELECT mono_ns, t, sender, kind, value FROM ctx ORDER BY mono_ns, rowid")
    kinds = [r[3] for r in ctx]
    start_ns = int(rows(f, "SELECT value FROM meta WHERE key='mono_start_ns'")[0][0])

    def find(kind, sender=None, **want):
        out = []
        for mono, t, s, k, v in ctx:
            if k != kind or (sender is not None and s != sender):
                continue
            j = json.loads(v)
            if all(j.get(key) == val for key, val in want.items()):
                out.append((mono, t, s, j))
        return out

    check(all(json.loads(r[4]) is not None for r in ctx), "every ctx value is valid JSON")
    # P6·P5는 P7의 회신이 2 ms를 넘기면 같은 사건을 다시 보낸다 (Mac은 부하가 걸리면 자주). P7이 한 번만 적어야 한다
    check(len(set(ctx)) == len(ctx), f"no event is written twice (P7 saw {a['s7'].get('dup_msgs')} resends)")
    lab_all = rows(f, "SELECT mono_ns, phone, misc FROM label")
    check(len(set(lab_all)) == len(lab_all), "no label is written twice")
    check(all(len(r[4]) <= 191 for r in ctx), "no ctx value was cut")
    check(not any(k in ("HB", "MOT") for k in kinds) and not any('"t":"MOT"' in r[4] or '"t":"HB"' in r[4] for r in ctx),
          "no HB or MOT in the log")
    check(all((r[1] is None) == (r[0] < start_ns) for r in ctx),
          "events before RIDE_START have t NULL, events in the ride have a ride time")
    check(all(r[1] is None or -0.5 <= r[1] <= RIDE_S + 3 for r in ctx), "ride times are inside the ride")

    # 주행 전
    pre = [r for r in ctx if r[1] is None]
    pk = [r[3] for r in pre]
    check("CONNECT" in pk and "HELLO" in pk and "INT" in pk, f"pre-ride CONNECT, HELLO and first INT are kept ({pk})")
    check(len(find("HELLO", PHONE, conn=0)) == 1 and find("HELLO", PHONE, conn=0)[0][1] is None, "HELLO of phone-a on conn 0 (before the ride)")
    check(len(find("DROP", "conn1", reason="no_hello_5s")) == 1, "DROP reason no_hello_5s for the silent conn 1")
    check(len(find("DISCONNECT", "conn1", registered=0)) == 1, "DISCONNECT of conn 1")
    check(len(find("DROP", "all", reason="p6_start")) == 1, "P6 start DROP")
    check(len(find("SCR", PHONE, on=1, att=1)) >= 1 and len(find("PROF", PHONE, mssq=30)) >= 1, "SCR and PROF sent before the ride")

    # 주행 중: 폰이 보낸 것
    off, on = find("OVR", PHONE, device="airflow-01", on=0), find("OVR", PHONE, device="airflow-01", on=1)
    check(len(off) == 2 and len(on) == 1 and all(r[1] is not None for r in off + on), f"OVR off x2, on x1 with ride time ({len(off)}, {len(on)})")
    check(len(find("SCR", PHONE, on=0, att=0)) == 1, "SCR off in the ride")
    lab = rows(f, "SELECT mono_ns, t, phone, misc FROM label ORDER BY mono_ns")
    check([(r[2], r[3]) for r in lab] == [(PHONE, 4), (PHONE, 2)], f"label: SYM 4 then SYM 2 from phone-a ({lab})")
    check(all(r[1] is not None and 0 < r[1] < RIDE_S and r[0] > start_ns for r in lab), "labels carry mono_ns and ride time")
    check("SYM" not in kinds, "accepted SYM goes to label only")

    # 거부
    rej = find("REJECT")
    reasons = [r[3]["reason"] for r in rej]
    check(len(rej) == 3, f"3 rejects ({reasons})")
    check(any("0..10" in x for x in reasons) and any(r[3]["raw"].startswith('{"t":"SYM","misc":11') and r[2] == PHONE for r in rej),
          "SYM 11 rejected with reason and raw text")
    check(any(f"OVR target {OTHER} not allowed" == x for x in reasons), "OVR on another phone rejected")
    check(any(r[2] == "uart" and r[3]["raw"] == "this is not a frame" and r[3]["conn"] is None for r in rej), "bad line rejected (sender uart)")

    # Pi → 폰
    ints = find("INT", PHONE)
    seq = [(r[3]["conn"], r[3]["tier"], r[3]["on"]) for r in ints if r[3]["tier"] == 1 and r[1] is not None]
    c0, c2 = [x for x in seq if x[0] == 0], [x for x in seq if x[0] == 2]
    while c0 and c0[0] == (0, 1, 0):  # 첫 주기 전의 안전 OFF가 주행 안에 들어올 수도 있다
        c0.pop(0)
    check(c0[:4] == [(0, 1, 1), (0, 1, 0), (0, 1, 1), (0, 1, 0)] and c2 and set(c2) == {(2, 1, 0)},
          f"INT tier 1 as the phone saw it: held ON, OVR off, OVR on, safe off, then off on the new conn ({seq})")
    # OVR off → 그 뒤 첫 INT off가 3초 안 (실제 시각)
    first_off = [r for r in ints if r[3]["tier"] == 1 and r[3]["on"] == 0 and r[0] > off[0][0]] if off else []
    check(bool(first_off) and (first_off[0][0] - off[0][0]) < 3e9, "INT off is logged after its OVR, within 3 s of real time")
    po = find("PROF_OUT", PHONE, conn=2)
    check(len(po) == 1 and po[0][3]["mssq"] == 30 and po[0][1] is not None, "PROF reply to the reconnected phone")

    # 무응답 DROP → BYE → 재연결
    dr = find("DROP", PHONE, conn=0, reason="silent_5s")
    bye = find("BYE", "conn0")
    con2, hel2 = find("CONNECT", "conn2"), find("HELLO", PHONE, conn=2)
    check(len(dr) == 1 and len(bye) == 1 and len(con2) == 1 and len(hel2) == 1, "silent DROP, BYE, CONNECT and HELLO of the reconnect")
    if dr and bye and con2 and hel2:
        check(dr[0][0] <= bye[0][0] < con2[0][0] <= hel2[0][0], "order by real time: DROP, BYE, CONNECT, HELLO")
        gap = (dr[0][0] - max(r[0] for r in on)) / 1e9 if on else -1
        check(4.0 <= gap <= 8.0, f"DROP came about 5 s after the phone went silent ({gap:.1f} s)")

    # P5 안전 OFF (sender p5)
    so, sc, ho = find("SAFE_OFF", "p5"), find("SAFE_CLEAR", "p5"), find("HELD_ON", "p5", device="airflow-01", tier=1)
    check(len(so) >= 1 and len(sc) >= 1 and len(ho) >= 1, f"P5: safe off {len(so)}, cleared {len(sc)}, held ON applied {len(ho)}")
    if ho and sc:
        first_on = [r for r in ints if r[3]["tier"] == 1 and r[3]["on"] == 1]
        check(any(c[0] <= ho[0][0] for c in sc) and bool(first_on) and first_on[0][0] >= ho[0][0],
              "held ON applied after the safe off was cleared, then INT on went out")
    check(a["s7"].get("ctx_rows") == len(ctx) and a["s7"].get("label_rows") == 2 and a["s7"].get("sqlite_errors") == 0,
          f"P7 stats match the file ({a['s7'].get('ctx_rows')} ctx rows)")


def main():
    for b in ("p1_acq", "p2_feat", "fake_p3", "p4_policy", "p5_devmgr", "p6_comm", "p7_logger"):
        if not os.path.exists(os.path.join(BUILD, b + "_sim")):
            print(f"missing {BUILD}/{b}_sim (cmake --build 먼저)")
            return 2
    tmp = tempfile.mkdtemp(prefix="lrf", dir="/tmp")  # Unix 소켓 경로 길이 제한
    runs = []
    try:
        print("A. P7 running: every phone event is in the ride file")
        a = Run(tmp, "a", f"/mmlr{os.getpid()}a")
        runs.append(a)
        p7 = a.start_p7()
        seen_a, rtt_a, kinds_a = a.drive("A")
        check(p7.wait_log("RIDE_CLOSED .* ride_end", 0, 10), "P7 closed the ride file")
        try:
            rc = p7.p.wait(10)
        except Exception:
            rc = None
        check(rc == 0, "P7 --once exits 0")
        s7 = {}
        for line in p7.text().splitlines():
            if line.startswith("{") and '"proc": "p7"' in line:
                s7 = json.loads(line)
        f = os.path.join(a.logs, "rides", "pi01-00001-001.sqlite")
        if check(os.path.exists(f), "closed ride file"):
            check_file(f, {"s7": s7})
        p6a = a.procs["p6_comm"].text()
        check("log_drop 0" in p6a.split("stat:")[-1], f"P6 lost no log event ({p6a.split('stat:')[-1].strip()[:90]})")
        a.kill()

        print("B. P7 stopped, then dead: the phone sees the same")
        b = Run(tmp, "b", f"/mmlr{os.getpid()}b")
        runs.append(b)
        p7b = b.start_p7()
        p7b.p.send_signal(signal.SIGSTOP)  # 이름은 있지만 회신하지 않는다 → 보내기마다 2 ms 시간 초과

        def die():
            p7b.p.send_signal(signal.SIGKILL)  # 여기부터는 P7이 없다
            p7b.p.wait(5)

        seen_b, rtt_b, kinds_b = b.drive("B", mid=die)
        check(seen_a == seen_b and all(ok for _, ok in seen_b), "every step of the phone script got the same answer without P7")
        check(kinds_a == kinds_b, f"the phone received the same kinds of lines ({kinds_b})")
        if rtt_a and rtt_b:
            ma, mb = max(rtt_a), max(rtt_b)
            print(f"  OVR -> INT round trip: with P7 max {ma * 1000:.0f} ms, without {mb * 1000:.0f} ms (Mac, loose)")
            check(mb < ma + 0.25, "OVR -> INT round trip is not slower without P7 (within 250 ms on a Mac)")
        p6b = b.procs["p6_comm"]
        time.sleep(1.0)
        stat = p6b.text().split("stat:")[-1]
        check("log_fail 0," not in stat and "log_sent 0," in stat, f"P6 only counted the failures ({stat.strip()[:80]})")
        check(not os.path.exists(os.path.join(b.logs, "rides", "pi01-00001-001.sqlite")), "no closed ride file from the dead P7")
    finally:
        for r in runs:
            r.kill()
        for r in runs:
            try:
                import _posixshmem
                _posixshmem.shm_unlink(r.ring)
            except Exception:
                pass
        if not failures:
            shutil.rmtree(tmp, ignore_errors=True)
        else:
            print(f"logs kept in {tmp}")
    print("log_rf_sim: all passed" if not failures else f"log_rf_sim: {len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
