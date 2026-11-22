#!/usr/bin/env python3
"""Pi 없이 P6(p6_comm)·P5(p5_devmgr)·P4/P2 대역(rf_stub)을 실제 프로세스로 함께 돌리는 시험.

  python3 controller/tests/rf_sim_test.py <build 폴더>     (ctest의 rf_sim이 부른다)

- QNX 메시지 전달: tests/qnxsim (Unix 소켓). 프로세스 코드는 Pi와 같은 qnx/*.cpp
- UART(/dev/ser3): 가상 터미널(pty). 이 스크립트가 ESP32 자리에서 줄을 쓰고 읽는다
- 확인: HB 1초, HELLO 5초 DROP, P5가 늦게 떠도 등록, 프로필 회신, SCR/OVR → P4, Tier 3 + MOT 10 Hz,
  남의 폰 OVR 거부, HUD 등록과 Tier 2, 기기 5초 무응답 DROP, 재연결 상태 재전송, P4가 죽으면 안전 OFF와 복구,
  P5만 다시 시작(OUT_RESYNC → 명부·Tier 3 복구), P6만 다시 시작(시작 DROP → 기기 재연결 → 복구)
QNX 자체(커널 스케줄링, /dev/ser3 드라이버)는 확인하지 못한다. 그건 Pi에서.
"""
import json
import os
import pty
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tty

BUILD = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else "build")
PHONE_A = "phone-a"
PHONE_B = "phone-b"

failures = []


def check(cond, what):
    print(("  ok   " if cond else "  FAIL ") + what, flush=True)
    if not cond:
        failures.append(what)
    return cond


class Bridge:
    """ESP32 흉내: pty master 쪽. P6은 slave 쪽을 /dev/ser3 대신 연다."""

    def __init__(self):
        self.master, self.slave = pty.openpty()
        tty.setraw(self.slave)
        self.path = os.ttyname(self.slave)
        self.lines = []  # (시각, 줄)
        self.lock = threading.Lock()
        self.hb_conns = set()
        self.stop = False
        threading.Thread(target=self._reader, daemon=True).start()
        threading.Thread(target=self._heartbeat, daemon=True).start()

    def _reader(self):
        buf = b""
        while not self.stop:
            try:
                data = os.read(self.master, 4096)
            except OSError:
                return
            buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                with self.lock:
                    self.lines.append((time.monotonic(), line.decode(errors="replace")))

    def _heartbeat(self):  # 앱처럼 등록된 연결마다 1초에 한 번 HB
        seq = 0
        while not self.stop:
            for c in list(self.hb_conns):
                self.send(f'{c}|{{"t":"HB","seq":{seq}}}')
            seq += 1
            time.sleep(1.0)

    def send(self, line):
        os.write(self.master, (line + "\n").encode())

    def mark(self):
        with self.lock:
            return len(self.lines)

    def since(self, mark):
        with self.lock:
            return list(self.lines[mark:])

    def wait(self, pattern, mark, timeout):
        """mark 뒤에 pattern(정규식)과 맞는 줄이 timeout 안에 오면 (시각, 줄), 아니면 None"""
        rx = re.compile(pattern)
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            for t, line in self.since(mark):
                if rx.search(line):
                    return t, line
            time.sleep(0.02)
        return None

    def count(self, pattern, mark):
        rx = re.compile(pattern)
        return sum(1 for _, l in self.since(mark) if rx.search(l))

    def hello(self, conn, role, dev):
        self.send(f"{conn}|CONNECT")
        self.send(f'{conn}|{{"t":"HELLO","role":"{role}","dev":"{dev}","v":1}}')
        self.hb_conns.add(conn)


class Proc:
    def __init__(self, name, args, logdir, env):
        self.name = name
        self.log = os.path.join(logdir, name + ".log")
        self.f = open(self.log, "w")
        self.p = subprocess.Popen(args, stdout=self.f, stderr=subprocess.STDOUT, env=env)

    def text(self):
        with open(self.log, errors="replace") as f:
            return f.read()

    def size(self):
        return len(self.text())

    def wait_log(self, pattern, start=0, timeout=5.0):
        rx = re.compile(pattern)
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if rx.search(self.text()[start:]):
                return True
            if self.p.poll() is not None:
                return bool(rx.search(self.text()[start:]))
            time.sleep(0.05)
        return False

    def kill(self):
        if self.p.poll() is None:
            self.p.terminate()
            try:
                self.p.wait(3)
            except subprocess.TimeoutExpired:
                self.p.kill()
        self.f.close()


INT = r'^{c}\|\{{"t":"INT","tier":{t},"on":{on}\}}$'
MOT = r'^{c}\|\{{"t":"MOT",'


def main():
    for b in ("p6_comm_sim", "p5_devmgr_sim", "rf_stub_sim"):
        if not os.path.exists(os.path.join(BUILD, b)):
            print(f"missing {BUILD}/{b} (cmake --build 먼저)")
            return 2

    tmp = tempfile.mkdtemp(prefix="rfsim", dir="/tmp")  # Unix 소켓 경로 길이 제한 때문에 /tmp 바로 아래
    env = dict(os.environ, QNXSIM_DIR=os.path.join(tmp, "ipc"))
    state = os.path.join(tmp, "state")
    os.makedirs(os.path.join(state, "profiles"))
    with open(os.path.join(state, "profiles", PHONE_A + ".json"), "w") as f:
        json.dump({"mssq": 20.0, "sens_mssq": 1.3, "sens": 1.5}, f)  # 옛 형식 프로필: 설문값 sens_mssq를 돌려줘야 한다

    br = Bridge()
    procs = {}

    def start(name, *args):
        procs[name] = Proc(name, [os.path.join(BUILD, name + "_sim"), *args], tmp, env)
        return procs[name]

    try:
        print("[1] P6 혼자: HB 1초, HELLO 없으면 5초 뒤 DROP")
        m = br.mark()
        p6 = start("p6_comm", "--dev", br.path)
        check(p6.wait_log("p6_comm up"), "p6_comm starts on the pty")
        check(br.wait(r"^8\|DROP$", m, 2) is not None and br.count(r"^\d\|DROP$", m) == 9,
              "start: 0~8|DROP once (bridge ignores empty slots)")
        m = br.mark()
        hb = br.wait(r'^ALL\|\{"t":"HB","seq":\d+\}$', m, 2.5)
        check(hb is not None, "ALL HB within 2.5 s")
        if hb:
            m2 = br.mark()
            time.sleep(3.05)
            n = br.count(r"^ALL\|.*HB", m2)
            check(2 <= n <= 4, f"HB about once a second ({n} in 3 s)")
        m = br.mark()
        t0 = time.monotonic()
        br.send("0|CONNECT")
        drop = br.wait(r"^0\|DROP$", m, 7)
        check(drop is not None and 4.5 <= drop[0] - t0 <= 6.5,
              f"0|DROP about 5 s after CONNECT ({(drop[0] - t0) if drop else 0:.2f} s)")

        print("[2] P5가 늦게 떠도 등록: HELLO 재전송, 현재 상태와 저장된 프로필이 폰으로")
        m = br.mark()
        br.hello(1, "app", PHONE_A)
        time.sleep(1.5)
        check(br.count(r"^1\|", m) == 0, "nothing for the phone while P5 is down")
        p5 = start("p5_devmgr", "--state", state, "--safety-s", "6")
        stub = start("rf_stub", "--motion", "--period", "1")
        for t in (1, 2, 3):
            check(br.wait(INT.format(c=1, t=t, on="false"), m, 4) is not None, f"phone gets INT tier {t} off")
        check(br.wait(r'^1\|\{"t":"PROF","mssq":20\.00,"sens":1\.300\}$', m, 4) is not None,
              "phone gets its saved profile (mssq 20, survey sens 1.3)")
        check(stub.wait_log(r"registry: 1 device\(s\) phone-a", 0, 4), "P4 stand-in sees phone-a via DEV_SYNC")

        print("[3] 폰 → P4: SCR, OVR")
        s = stub.size()
        br.send('1|{"t":"SCR","on":true,"att":true}')
        check(stub.wait_log(r"SCR phone-a on=1 att=1", s, 3), "SCR reaches P4 with the sender filled in")

        print("[4] 점 찍기 ON: INT + MOT 10 Hz, OFF: MOT 멈춤")
        m = br.mark()
        br.send(f'1|{{"t":"OVR","dev":"{PHONE_A}","on":true}}')
        check(br.wait(INT.format(c=1, t=3, on="true"), m, 3) is not None, "OVR on -> P4 -> P5 -> INT tier 3 on")
        check(br.wait(MOT.format(c=1), m, 2) is not None, "MOT starts")
        m2 = br.mark()
        time.sleep(2.0)
        n = br.count(MOT.format(c=1), m2)
        check(15 <= n <= 25, f"MOT about 10 Hz ({n} in 2 s)")
        mot = [l for _, l in br.since(m2) if l.startswith("1|{\"t\":\"MOT\"")]
        if mot:
            j = json.loads(mot[-1].split("|", 1)[1])
            check(set(j) == {"t", "ax", "ay", "pax", "pay"}, "MOT has ax, ay, pax, pay")
        m = br.mark()
        br.send(f'1|{{"t":"OVR","dev":"{PHONE_A}","on":false}}')
        check(br.wait(INT.format(c=1, t=3, on="false"), m, 3) is not None, "OVR off -> INT tier 3 off")
        time.sleep(0.5)
        m2 = br.mark()
        time.sleep(1.5)
        check(br.count(MOT.format(c=1), m2) == 0, "MOT stops")

        print("[5] 남의 폰 OVR는 거부")
        s = stub.size()
        br.send('1|{"t":"OVR","dev":"phone-x","on":true}')
        time.sleep(1.0)
        check("phone-x" not in stub.text()[s:], "OVR for another phone never reaches P4")
        check(p6.wait_log(r"OVR target phone-x not allowed", 0, 1), "P6 logs the refusal")

        print("[6] HUD 등록(hud-01로), Tier 2 → 폰·HUD 모두 INT, HUD만 MOT")
        m = br.mark()
        br.hello(2, "hud", "browser-1234")
        check(br.wait(INT.format(c=2, t=2, on="false"), m, 3) is not None, "HUD gets its state")
        check(p6.wait_log(r"registered as hud-01", 0, 1), "HUD registered as hud-01")
        m = br.mark()
        br.send('2|{"t":"OVR","dev":"display-01","on":true}')
        check(br.wait(INT.format(c=1, t=2, on="true"), m, 3) is not None, "phone shows Tier 2 on")
        check(br.wait(INT.format(c=2, t=2, on="true"), m, 3) is not None, "HUD gets Tier 2 on")
        check(br.wait(MOT.format(c=2), m, 2) is not None, "HUD gets MOT (glow direction)")
        time.sleep(0.5)
        m2 = br.mark()
        time.sleep(1.0)
        check(br.count(MOT.format(c=1), m2) == 0, "phone gets no MOT for Tier 2")
        m = br.mark()
        br.send('2|{"t":"OVR","dev":"display-01","on":false}')
        check(br.wait(INT.format(c=2, t=2, on="false"), m, 3) is not None, "Tier 2 off")

        print("[7] 폰이 5초 동안 조용하면 DROP, P5 명부에서 빠짐")
        m = br.mark()
        s5 = p5.size()
        t0 = time.monotonic()
        br.hb_conns.discard(1)
        drop = br.wait(r"^1\|DROP$", m, 8)
        check(drop is not None and drop[0] - t0 <= 7, f"1|DROP after silence ({(drop[0] - t0) if drop else 0:.1f} s)")
        check(p5.wait_log(r"bye conn 1 phone-a", s5, 2), "P5 removes phone-a")

        print("[8] 다른 번호로 다시 붙으면 지금 상태를 다시 받음")
        m = br.mark()
        br.send('2|{"t":"OVR","dev":"airflow-01","on":true}')
        check(br.wait(INT.format(c=2, t=1, on="true"), m, 3) is not None, "airflow on (HUD button)")
        m = br.mark()
        br.hello(3, "app", PHONE_A)
        check(br.wait(INT.format(c=3, t=1, on="true"), m, 3) is not None, "reconnected phone gets Tier 1 on")
        check(br.wait(INT.format(c=3, t=3, on="false"), m, 3) is not None, "and Tier 3 off")

        print("[9] P4가 멈추면 6초 뒤 안전 OFF, 다시 오면 풀림")
        m = br.mark()
        s5 = p5.size()
        stub.kill()
        check(p5.wait_log(r"safe off: no DEV_SYNC", s5, 9), "P5 goes safe off")
        check(br.wait(INT.format(c=3, t=1, on="false"), m, 2) is not None, "phone told Tier 1 off")
        check(br.wait(INT.format(c=2, t=1, on="false"), m, 2) is not None, "HUD told Tier 1 off")
        s5 = p5.size()
        m = br.mark()
        stub = start("rf_stub", "--cycle", "--period", "1")
        check(p5.wait_log(r"safe off cleared", s5, 3), "DEV_SYNC back clears safe off")
        check(br.wait(INT.format(c=3, t=3, on="true"), m, 6) is not None, "cycle reaches Tier 3 on for phone-a")

        print("[10] DISCONNECT")
        s5 = p5.size()
        br.hb_conns.discard(3)
        br.send("3|DISCONNECT")
        check(p5.wait_log(r"bye conn 3 phone-a", s5, 2), "P5 removes the phone on DISCONNECT")

        print("[11] P5만 다시 시작: OUT_RESYNC → 등록·Tier 3·스트림이 그대로 돌아옴")
        stub.kill()
        stub = start("rf_stub", "--motion", "--period", "1")  # cycle 없이: OVR를 그대로 명령으로
        m = br.mark()
        br.hello(4, "app", PHONE_B)
        check(br.wait(INT.format(c=4, t=3, on="false"), m, 3) is not None, "phone-b registered on conn 4")
        m = br.mark()
        br.send(f'4|{{"t":"OVR","dev":"{PHONE_B}","on":true}}')
        check(br.wait(INT.format(c=4, t=3, on="true"), m, 3) is not None, "phone-b Tier 3 on")
        check(br.wait(MOT.format(c=4), m, 2) is not None, "phone-b gets MOT")
        p5.kill()
        s6 = p6.size()
        m = br.mark()
        s = stub.size()
        p5 = start("p5_devmgr", "--state", state, "--safety-s", "6")
        check(p6.wait_log(r"RESYNC \(P5 restarted\)", s6, 4), "P6 gets OUT_RESYNC from the new P5")
        check(p5.wait_log(r"resync: Tier 3 phone-b ON", 0, 4), "new P5 recovers phone-b Tier 3 ON from P6's record")
        check(br.wait(INT.format(c=4, t=3, on="true"), m, 4) is not None, "phone-b gets INT again (still on)")
        check(br.wait(INT.format(c=2, t=2, on="(true|false)"), m, 4) is not None, "HUD gets INT again")
        check(br.count(INT.format(c=4, t=3, on="false"), m) == 0, "phone-b never told Tier 3 off")
        check(stub.wait_log(r"registry: 2 device\(s\) hud-01\(hud\) phone-b", s, 4),
              "P4 stand-in sees hud-01 and phone-b again via DEV_SYNC")
        m2 = br.mark()
        time.sleep(1.0)
        check(br.count(MOT.format(c=4), m2) >= 7, "MOT to phone-b keeps going")
        m = br.mark()
        br.send(f'4|{{"t":"OVR","dev":"{PHONE_B}","on":false}}')
        check(br.wait(INT.format(c=4, t=3, on="false"), m, 3) is not None, "new P5 routes P4 commands to phone-b")
        m = br.mark()
        br.send(f'4|{{"t":"OVR","dev":"{PHONE_B}","on":true}}')
        check(br.wait(INT.format(c=4, t=3, on="true"), m, 3) is not None, "phone-b Tier 3 on again")

        print("[12] P6만 다시 시작: 시작 DROP → 기기 재연결·HELLO → 복구")
        p6.kill()
        m = br.mark()
        s5 = p5.size()
        p6 = start("p6_comm", "--dev", br.path)
        check(br.wait(r"^8\|DROP$", m, 3) is not None, "new P6 sends 0~8|DROP")
        check(br.count(r"^2\|DROP$", m) == 1 and br.count(r"^4\|DROP$", m) == 1, "HUD and phone-b are dropped")
        check(p5.wait_log(r"bye all \(P6 restarted\)", s5, 3), "P5 empties its registry (DEV_BYE ALL)")
        br.hb_conns.clear()  # 브리지가 끊음 → DISCONNECT → 기기가 다시 붙음 (번호는 바뀔 수 있음)
        br.send("2|DISCONNECT")
        br.send("4|DISCONNECT")
        time.sleep(0.3)
        m = br.mark()
        s = stub.size()
        br.hello(0, "app", PHONE_B)
        br.hello(1, "hud", "hud-01")
        check(br.wait(INT.format(c=0, t=3, on="true"), m, 4) is not None, "phone-b (now conn 0) gets Tier 3 on back")
        check(br.wait(INT.format(c=1, t=2, on="(true|false)"), m, 4) is not None, "HUD (now conn 1) gets its state")
        check(br.wait(MOT.format(c=0), m, 3) is not None, "MOT to phone-b resumes on the new conn")
        check(stub.wait_log(r"registry: 2 device\(s\) phone-b hud-01\(hud\)", s, 4),
              "P4 stand-in sees both again")
        s4 = stub.size()
        br.send('0|{"t":"SCR","on":true,"att":false}')
        check(stub.wait_log(r"SCR phone-b on=1 att=0", s4, 3), "phone-b messages reach P4 again")

        print("[13] 프로세스가 살아 있음 (교착·종료 없음)")
        for name, p in procs.items():
            check(p.p.poll() is None, f"{name} still running")
    finally:
        br.stop = True
        for p in procs.values():
            p.kill()
        if failures:
            for name, p in procs.items():
                print(f"----- {name}.log (last 40 lines)")
                print("\n".join(p.text().splitlines()[-40:]))
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\nrf_sim: {'all passed' if not failures else str(len(failures)) + ' failure(s)'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
