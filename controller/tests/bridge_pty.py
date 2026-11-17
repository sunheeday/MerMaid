#!/usr/bin/env python3
"""ESP32 브리지 흉내 (Pi에서 ESP32·폰 없이 P1~P6을 함께 돌릴 때). 가상 터미널 master 쪽을 열고,
P6은 slave 쪽을 /dev/ser3 대신 연다 (run_pipeline.sh RF=1 SER=<slave>).

  python3 controller/tests/bridge_pty.py --master /dev/ptyp0 --log out/rf/bridge.log \
      --phone phone-0:25.81:1.479 [--phone ...] [--hud]

- P6의 첫 ALL|HB를 보면 연결 0부터 CONNECT + HELLO, SCR(켜짐, 폰은 주의 있음), 폰은 PROF를 보낸다
- 등록한 연결마다 1초에 한 번 HB (P6의 5초 무응답 DROP을 막음)
- 받은 줄은 모두 "<단조 시각> <줄>"로 --log에. SIGTERM / SIGINT이면 요약 JSON 한 줄을 stdout에 낸다
"""
import argparse
import json
import os
import re
import signal
import sys
import threading
import time


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--master", required=True)
    ap.add_argument("--log", required=True)
    ap.add_argument("--phone", action="append", default=[], help="ID:MSSQ:SENS")
    ap.add_argument("--hud", action="store_true")
    a = ap.parse_args()

    fd = os.open(a.master, os.O_RDWR | os.O_NOCTTY)
    log = open(a.log, "w")
    lock = threading.Lock()
    conns = []  # (연결 번호, 역할, dev, mssq, sens)
    for i, p in enumerate(a.phone):
        dev, mssq, sens = p.split(":")
        conns.append((i, "app", dev, float(mssq), float(sens)))
    if a.hud:
        conns.append((len(conns), "hud", "hud-01", None, None))
    stats = {"lines": 0, "hb_all": 0, "int": {}, "mot": {}, "prof": {}, "drop": []}
    up = threading.Event()
    stop = threading.Event()

    def send(line):
        with lock:
            os.write(fd, (line + "\n").encode())

    def reader():
        buf = b""
        rx_int = re.compile(r'^(\d+|ALL)\|\{"t":"INT","tier":(\d),"on":(true|false)\}$')
        while not stop.is_set():
            try:
                data = os.read(fd, 4096)
            except OSError:
                return
            if not data:
                time.sleep(0.01)
                continue
            buf += data
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                line = raw.decode(errors="replace").rstrip("\r")
                t = time.monotonic()
                log.write(f"{t:.3f} {line}\n")
                stats["lines"] += 1
                cid, _, payload = line.partition("|")
                if line.startswith("ALL|") and '"HB"' in line:
                    stats["hb_all"] += 1
                    up.set()
                m = rx_int.match(line)
                if m:
                    k = f"{m.group(1)}:{m.group(2)}:{'on' if m.group(3) == 'true' else 'off'}"
                    stats["int"][k] = stats["int"].get(k, 0) + 1
                elif '"t":"MOT"' in payload:
                    stats["mot"][cid] = stats["mot"].get(cid, 0) + 1
                elif '"t":"PROF"' in payload:
                    stats["prof"][cid] = json.loads(payload)
                elif payload == "DROP":
                    stats["drop"].append(cid)

    def heartbeat():
        seq = 0
        while not stop.is_set():
            if up.is_set():
                for c in conns:
                    send(f'{c[0]}|{{"t":"HB","seq":{seq}}}')
                seq += 1
            time.sleep(1.0)

    def finish(*_):
        stop.set()
        log.flush()
        print(json.dumps({"proc": "bridge", **stats}), flush=True)
        os._exit(0)

    signal.signal(signal.SIGTERM, finish)
    signal.signal(signal.SIGINT, finish)
    threading.Thread(target=reader, daemon=True).start()
    threading.Thread(target=heartbeat, daemon=True).start()
    print(f"bridge: waiting for P6 HB on {a.master}", flush=True)
    up.wait()
    for cid, role, dev, mssq, sens in conns:
        send(f"{cid}|CONNECT")
        send(f'{cid}|{{"t":"HELLO","role":"{role}","dev":"{dev}","v":1}}')
        send(f'{cid}|{{"t":"SCR","on":true,"att":{"true" if role == "app" else "false"}}}')
        if role == "app":
            send(f'{cid}|{{"t":"PROF","mssq":{mssq:.2f},"sens":{sens:.3f}}}')
    print(f"bridge: registered {len(conns)} connection(s)", flush=True)
    while True:
        time.sleep(1)


if __name__ == "__main__":
    sys.exit(main())
