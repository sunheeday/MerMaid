#!/usr/bin/env python3
"""P7 로거를 실제 프로세스로: P1 LIVE(가짜 IMU) → P2 → P3 대역 → P4, 그리고 P7 (docs/DATA_LOGGING.md §6).

  python3 controller/tests/log_sim_test.py <build 폴더>     (ctest의 log_sim이 부른다)

세 번 주행한다.
  A. 보통: 주행 파일 하나가 닫히고, samples 수 = P1이 보낸 수, infer 2주기, decision 2주기 × 기기 3, meta
  B. 주행 중 P7 kill -9: P1~P4는 그대로 끝난다 (샘플 손실 0, P4 주기 그대로). 다시 띄운 P7이 남은 파일을 복구
  C. 주행 중 P7 SIGSTOP 10초: P1~P4는 그대로 (보내기는 2 ms 시간 초과). 다시 움직이면 링에 남은 샘플을 모두 기록
Mac은 실시간 OS가 아니라 주기 시간 변화(< 0.5 ms)는 Pi에서 잰다. 약 1분 30초.
"""
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rf_sim_test import Proc, check, failures  # noqa: E402

BUILD = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else "build")
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CONFIG = os.path.join(REPO, "controller", "config")
RING = f"/mmls{os.getpid()}"


def json_line(proc, name):
    for line in proc.text().splitlines():
        if line.startswith("{") and f'"proc": "{name}"' in line:
            return json.loads(line)
    return {}


def q(db, sql):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        row = con.execute(sql).fetchone()
        return row[0] if row else None
    finally:
        con.close()


def meta(db, key):
    return q(db, f"SELECT value FROM meta WHERE key='{key}'")


class Run:
    """한 번의 주행: P4, P3 대역, P2, P1 LIVE (가짜 IMU). P7은 따로 띄운다"""

    def __init__(self, tmp, tag, env, state, ride_s):
        self.dir = os.path.join(tmp, tag)
        os.makedirs(self.dir)
        self.env, self.state, self.ride_s = env, state, ride_s
        self.procs = {}

    def start(self, name, *args, binary=None):
        b = os.path.join(BUILD, (binary or name) + "_sim")
        self.procs[name] = Proc(name, [b, *args], self.dir, self.env)
        return self.procs[name]

    def pipeline(self):
        self.start("p4_policy", "--config", CONFIG, "--state", self.state, "--log", os.path.join(self.dir, "cycles.csv"),
                   "--hud", "--phone", "phone-a:20:1.0", "--once")
        p3 = self.start("fake_p3", "--p", "0.95,0.9,0.5,0.2,0.1", "--once")
        check(p3.wait_log("fake_p3 up", 0, 10), "fake P3 up")
        self.start("p2_feat", "--once")
        p1 = self.start("p1_acq", "--live", "--fake-imu", "--state", self.state, "--duration-s", str(self.ride_s),
                        "--mount", os.path.join(CONFIG, "imu_mount.json"), "--timeout-ms", "200", "--ring", RING)
        check(p1.wait_log("RIDE_START", 0, 15), "RIDE_START")
        return p1

    def finish(self):
        p1 = self.procs["p1_acq"]
        check(p1.wait_log("RIDE_END", 0, self.ride_s + 10), "RIDE_END")
        for n in ("p1_acq", "p2_feat", "fake_p3", "p4_policy"):
            try:
                rc = self.procs[n].p.wait(15)
            except subprocess.TimeoutExpired:
                rc = None
            check(rc == 0, f"{n} exit code 0 (got {rc})")
        s1 = json_line(p1, "p1")
        check(abs(s1.get("samples", 0) - self.ride_s * 100) <= 2 and s1.get("send_fail") == 0 and s1.get("restarts") == 0,
              f"P1 sent every sample to P2: samples {s1.get('samples')}, send_fail {s1.get('send_fail')}")
        s4 = json_line(self.procs["p4_policy"], "p4")
        check(s4.get("cycles") == self.ride_s // 10 and s4.get("risk_gaps") == 0,
              f"P4 cycles {s4.get('cycles')} (expect {self.ride_s // 10}), no gaps")
        return s1, json_line(self.procs["fake_p3"], "fake_p3"), s4

    def kill(self):
        for p in self.procs.values():
            p.kill()


def wait_exit(proc, timeout):
    try:
        return proc.p.wait(timeout)
    except subprocess.TimeoutExpired:
        return None


def main():
    for b in ("p1_acq", "p2_feat", "fake_p3", "p4_policy", "p7_logger"):
        if not os.path.exists(os.path.join(BUILD, b + "_sim")):
            print(f"missing {BUILD}/{b}_sim (cmake --build 먼저)")
            return 2
    tmp = tempfile.mkdtemp(prefix="logsim", dir="/tmp")  # Unix 소켓 경로 길이 제한
    env = dict(os.environ, QNXSIM_DIR=os.path.join(tmp, "ipc"))
    state = os.path.join(tmp, "state")
    logs = os.path.join(tmp, "logs")
    rides = os.path.join(logs, "rides")
    os.makedirs(state)
    runs, p7s = [], []

    def p7(run, tag, *extra):
        pr = Proc(tag, [os.path.join(BUILD, "p7_logger_sim"), "--dir", logs, "--config", CONFIG, "--ring", RING,
                        "--device-id", "pi01", "--min-free-mb", "1", *extra], run.dir, env)  # 디스크 여유와 무관하게
        p7s.append(pr)
        check(pr.wait_log("logging to", 0, 10), f"{tag} up")
        return pr

    try:
        # ---------- A. 보통 주행 ----------
        print("A. normal ride")
        a = Run(tmp, "a", env, state, 21)
        runs.append(a)
        p7a = p7(a, "p7_a", "--once")
        a.pipeline()
        s1, s3, s4 = a.finish()
        check(p7a.wait_log("RIDE_CLOSED .* ride_end", 0, 10), "P7 closed the ride file")
        check(wait_exit(p7a, 10) == 0, "P7 --once exits 0")
        f = os.path.join(rides, "pi01-00001-001.sqlite")
        left = [x for x in os.listdir(rides) if ".open." in x]
        check(os.path.exists(f) and not left, f"closed ride file, nothing left under the open name {left}")
        if os.path.exists(f):
            n = q(f, "SELECT sum(n) FROM samples")
            check(n == s1.get("samples") and meta(f, "p1_samples") == str(s1.get("samples")),
                  f"samples in file {n} = P1 sent {s1.get('samples')}")
            check(q(f, "SELECT count(*) FROM samples WHERE hold IS NOT NULL") == 0, "no hold")
            check(q(f, "SELECT count(*) FROM infer") == 2 and q(f, "SELECT min(phone) FROM infer") == "phone-a",
                  "infer: 2 cycles of phone-a")
            check(abs(q(f, "SELECT min(t) FROM infer") - 9.99) < 1e-6, "first infer at t = 9.99")
            check(q(f, "SELECT count(*) FROM decision") == 6, f"decision: 2 cycles x 3 devices ({q(f, 'SELECT count(*) FROM decision')})")
            check(q(f, "SELECT count(*) FROM decision WHERE device='airflow-01' AND on_=1 AND tier=1") >= 1,
                  "airflow on at p2 = 0.95")
            check(q(f, "SELECT length(x) FROM infer LIMIT 1") == 77 * 8, "x is 77 doubles")
            check(meta(f, "source") == "live" and meta(f, "closed_reason") == "ride_end", "meta source, closed_reason")
            check(meta(f, "model_version") == "fake", "model version from LOG_INFER")
            bias = json.loads(meta(f, "imu_bias_dps") or "null")
            check(bias is not None and abs(bias[0] - 1.0) < 1e-6 and abs(bias[1] + 66 / 131) < 1e-6,
                  f"bias from ring RIDE_START: {bias}")
            check(meta(f, "accel_range_g") == "4" and meta(f, "gyro_range_dps") == "250", "IMU ranges")
            check(json.loads(meta(f, "imu_mount") or "{}").get("R") == [[1, 0, 0], [0, 1, 0], [0, 0, 1]], "imu_mount in meta")
            check(bool(meta(f, "policy_json")) and bool(meta(f, "git_commit")), "policy and git commit in meta")
            check(meta(f, "columns") == '["ax", "ay", "az", "gx", "gy", "gz"]', "columns")
        check(s3.get("log_fail") == 0 and s4.get("log_fail") == 0,
              f"no log send failures (p3 {s3.get('log_fail')}, p4 {s4.get('log_fail')})")
        s7 = json_line(p7a, "p7")
        check(s7.get("ring_lost") == 0 and s7.get("queue_drop") == 0 and s7.get("sqlite_errors") == 0,
              f"P7 lost nothing: {s7}")

        # ---------- B. P7 kill -9 ----------
        print("B. P7 killed during the ride")
        b = Run(tmp, "b", env, state, 12)
        runs.append(b)
        p7b = p7(b, "p7_b")
        b.pipeline()
        time.sleep(3)
        p7b.p.send_signal(signal.SIGKILL)
        p7b.p.wait(5)
        s1, s3, s4 = b.finish()
        check(s3.get("log_fail", 0) >= 1 and s4.get("log_fail", 0) >= 1,
              f"senders counted the failure and went on (p3 {s3.get('log_fail')}, p4 {s4.get('log_fail')})")
        check(os.path.exists(os.path.join(rides, "pi01-00002-001.open.sqlite")), "open file left by killed P7")
        p7b2 = p7(b, "p7_b2")
        check(p7b2.wait_log("recovered 1, broken 0", 0, 5), "restarted P7 recovered the file")
        fb = os.path.join(rides, "pi01-00002-001.sqlite")
        if os.path.exists(fb):
            n = q(fb, "SELECT sum(n) FROM samples") or 0
            check(meta(fb, "closed_reason") == "recovered" and 100 <= n <= 400,
                  f"recovered file: reason {meta(fb, 'closed_reason')}, samples {n} (about 3 s)")
        else:
            check(False, "recovered file renamed")
        time.sleep(1.5)  # 지난 실행의 링 칸을 다시 기록하지 않는지 (skip_to_head)
        p7b2.p.send_signal(signal.SIGTERM)
        check(wait_exit(p7b2, 10) == 0, "P7 SIGTERM exits 0")
        s7 = json_line(p7b2, "p7")
        check(s7.get("rides_opened") == 0 and s7.get("samples") == 0, f"stale ring not logged again: {s7}")

        # ---------- C. P7 SIGSTOP ----------
        print("C. P7 stopped during the ride")
        c = Run(tmp, "c", env, state, 12)
        runs.append(c)
        p7c = p7(c, "p7_c", "--once")
        c.pipeline()
        time.sleep(1)
        p7c.p.send_signal(signal.SIGSTOP)
        time.sleep(10)  # 10초 주기가 P7이 멈춘 사이에 지나간다
        p7c.p.send_signal(signal.SIGCONT)
        s1, s3, s4 = c.finish()
        check(s3.get("log_fail", 0) >= 1 and s4.get("log_fail", 0) >= 1,
              f"sends to a stopped P7 timed out (p3 {s3.get('log_fail')}, p4 {s4.get('log_fail')})")
        check(p7c.wait_log("RIDE_CLOSED .* ride_end", 0, 10), "P7 closed the ride after it resumed")
        check(wait_exit(p7c, 10) == 0, "P7 exits 0")
        fc = os.path.join(rides, "pi01-00004-001.sqlite")
        if os.path.exists(fc):
            n = q(fc, "SELECT sum(n) FROM samples")
            check(n == s1.get("samples"), f"ring kept every sample while P7 was stopped ({n} = {s1.get('samples')})")
        else:
            check(False, "ride file of run C")
    finally:
        for r in runs:
            r.kill()
        for p in p7s:
            try:
                p.p.send_signal(signal.SIGCONT)
            except ProcessLookupError:
                pass
            p.kill()
        try:
            import _posixshmem
            _posixshmem.shm_unlink(RING)
        except Exception:
            pass
        if not failures:
            shutil.rmtree(tmp, ignore_errors=True)
        else:
            print(f"logs kept in {tmp}")
    print("log_sim: all passed" if not failures else f"log_sim: {len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
