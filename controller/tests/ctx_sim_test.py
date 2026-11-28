#!/usr/bin/env python3
"""V3 문맥 모델(ctx_gru / ctx_attn / ctx_xattn)로 P1 재생 → P2 → 진짜 P3 → P4를 두 주행 돌리는 시험 (Pi 없이, qnxsim).

  python3 controller/tests/ctx_sim_test.py <build 폴더> <ctx_gru · ctx_attn · ctx_xattn 모델 폴더>
  (ctest의 ctx_sim: cmake -DMM_CTX_MODEL=<모델 폴더>일 때만)

- 정책 설정은 controller/config를 복사하고 thresholds.json만 kind response3로 바꾼다. base_th: fan·ambient는 높게(꺼진 채로
  음성 토큰 2개씩), 폰 dots는 낮게(자동으로 켜고 그대로 두면 양성 1개)
- 주행 1: 승객 phone-a의 과거 토큰이 없어 h = 0. 주행 끝에 P4가 고른 토큰을 P3가 state/ctx/phone-a.ctx에 저장
- 주행 2: 같은 상태 폴더로 다시. P3가 주행 1의 토큰으로 h를 계산(로그 "phone-a history N tokens", N > 0)하고 토큰을 더 붙인다
- 확인: RISK가 response3 형식(앞 3칸 확률, 뒤 2칸 NaN), 정책이 그 주기를 썼는지(risk_kind_skip 없음), 토큰 수와 파일 내용
"""
import csv
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rf_sim_test import Proc, check, failures  # noqa: E402

BUILD = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else "build")
MODELS = os.path.abspath(sys.argv[2]) if len(sys.argv) > 2 else ""
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
RIDE_S = 720  # 끝 5분은 음성 후보에서 빠지므로 7분 이상
SPEED = 40
KIND = json.load(open(os.path.join(MODELS, "models.json"))).get("kind") if MODELS and os.path.exists(os.path.join(MODELS, "models.json")) else ""


def stats_of(proc, name):
    for line in proc.text().splitlines():
        if line.startswith("{") and f'"proc": "{name}"' in line:
            return json.loads(line)
    return {}


def read_ctx(path):
    with open(path, "rb") as f:
        b = f.read()
    if b[:8] != b"MMCTX1\0\0":
        return None
    de, rides, n = struct.unpack_from("<3i", b, 8)
    off, toks = 20, []
    for _ in range(n):
        ride, dev, lab, _pad = struct.unpack_from("<iBBH", b, off)
        off += 8 + 4 * de
        toks.append((ride, dev, lab))
    return {"de": de, "rides": rides, "tokens": toks, "size_ok": off == len(b)}


def ride(tmp, env, cfg, state, trip, k):
    procs = {}

    def start(name, *args):
        procs[name] = Proc(f"{name}_{k}", [os.path.join(BUILD, name + "_sim"), *args], tmp, env)
        return procs[name]

    log = os.path.join(tmp, f"cycles_{k}.csv")
    try:
        start("p4_policy", "--config", cfg, "--state", state, "--log", log, "--hud", "--phone", "phone-a:20:1.0", "--once")
        p3 = start("p3_infer", "--models", MODELS, "--ctx-state", os.path.join(state, "ctx"), "--once")
        check(p3.wait_log(r"ctx_(gru|attn|xattn) model loaded", 0, 10), f"ride {k}: P3 loaded the context model")
        start("p2_feat", "--once")
        start("p1_acq", trip, "--speed", str(SPEED), "--timeout-ms", "1000", "--ring", "none")  # Mac이 바쁠 때도 (실시간 OS 아님)
        for n, pr in procs.items():
            try:
                rc = pr.p.wait(RIDE_S / SPEED + 30)
            except subprocess.TimeoutExpired:
                rc = None
            check(rc == 0, f"ride {k}: {n} exit code 0 (got {rc})")
    finally:
        for p in procs.values():
            p.kill()
    rows = list(csv.DictReader(open(log))) if os.path.exists(log) else []
    return procs, rows


def main():
    if not MODELS or not os.path.exists(os.path.join(MODELS, "models.json")):
        print("usage: ctx_sim_test.py <build> <ctx_gru / ctx_attn / ctx_xattn model dir>")
        return 2
    for b in ("p1_acq", "p2_feat", "p3_infer", "p4_policy"):
        if not os.path.exists(os.path.join(BUILD, b + "_sim")):
            print(f"missing {BUILD}/{b}_sim (cmake --build 먼저)")
            return 2
    tmp = tempfile.mkdtemp(prefix="ctxsim", dir="/tmp")  # Unix 소켓 경로 길이 제한
    env = dict(os.environ, QNXSIM_DIR=os.path.join(tmp, "ipc"))
    state = os.path.join(tmp, "state")
    os.makedirs(state)
    cfg = os.path.join(tmp, "config")
    shutil.copytree(os.path.join(REPO, "controller", "config"), cfg)
    th = json.load(open(os.path.join(cfg, "thresholds.json")))
    th.update({"kind": "response3", "base_th": {"1": 0.99, "2": 0.99, "3": 0.05}, "source": "ctx_sim_test"})
    json.dump(th, open(os.path.join(cfg, "thresholds.json"), "w"), indent=1)
    trip = os.path.join(tmp, "trip.bin")
    subprocess.run([sys.executable, os.path.join(REPO, "controller", "tests", "make_demo_trip.py"), trip,
                    "--seconds", str(RIDE_S)], check=True, capture_output=True)
    ctx_file = os.path.join(state, "ctx", "phone-a.ctx")
    try:
        # ---- 주행 1 ----
        procs, rows = ride(tmp, env, cfg, state, trip, 1)
        s3, s4 = stats_of(procs["p3_infer"], "p3"), stats_of(procs["p4_policy"], "p4")
        n_cyc = RIDE_S // 10
        check(s3.get("kind") == KIND and s3.get("cycles", 0) >= n_cyc - 1, f"ride 1: P3 cycles {s3}")
        check("risk_kind_skip" not in s4, f"ride 1: P4 used every cycle {s4}")
        check(len(rows) >= n_cyc - 1, f"ride 1: cycles in P4 log ({len(rows)})")
        bad = [r for r in rows if not all(0 <= float(r[c]) <= 1 for c in ("p2", "p3", "p4"))
               or not all(math.isnan(float(r[c])) for c in ("p5", "p6"))]
        check(not bad, f"ride 1: RISK rows are [fan, ambient, dots, NaN, NaN] ({len(bad)} bad)")
        check(re.search(r"phone-a history 0 tokens", procs["p3_infer"].text()) is not None, "ride 1: no history yet")
        n1 = s3.get("tokens_added", 0)
        check(n1 == s4.get("tokens", -1) and n1 == 5 and s4.get("tokens_pos") == 1 and s3.get("tokens_fail") == 0,
              f"ride 1: tokens P4 {s4.get('tokens')} → P3 saved {n1}, fail {s3.get('tokens_fail')}")
        c1 = read_ctx(ctx_file) if os.path.exists(ctx_file) else None
        check(c1 is not None and c1["size_ok"] and c1["rides"] == 1 and len(c1["tokens"]) == n1,
              f"ride 1: phone-a.ctx {c1 and (c1['rides'], len(c1['tokens']))}")
        if c1:
            devs = sorted({d for _, d, _ in c1["tokens"]})
            check(devs == [0, 1, 2], f"ride 1: tokens for fan, ambient and dots ({devs})")
            print(f"ride 1: {len(c1['tokens'])} tokens, positives {sum(l for _, _, l in c1['tokens'])}, "
                  f"infer max {s3.get('infer_ms_max')} ms, commands {s4.get('commands')}")

        # ---- 주행 2: 지난 토큰으로 문맥 ----
        procs, rows = ride(tmp, env, cfg, state, trip, 2)
        s3 = stats_of(procs["p3_infer"], "p3")
        m = re.search(r"phone-a history (\d+) tokens", procs["p3_infer"].text())
        check(m is not None and int(m.group(1)) == n1, f"ride 2: context from ride 1 ({m and m.group(1)} tokens)")
        c2 = read_ctx(ctx_file) if os.path.exists(ctx_file) else None
        check(c2 is not None and c2["rides"] == 2 and len(c2["tokens"]) == n1 + s3.get("tokens_added", 0)
              and {r for r, _, _ in c2["tokens"]} == {0, 1}, f"ride 2: tokens appended {c2 and (c2['rides'], len(c2['tokens']))}")
        print(f"ride 2: history {s3.get('history_ms_max')} ms, infer max {s3.get('infer_ms_max')} ms")
    finally:
        if not failures:
            shutil.rmtree(tmp, ignore_errors=True)
        else:
            print(f"logs kept in {tmp}")
    print("ctx_sim: all passed" if not failures else f"ctx_sim: {len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
