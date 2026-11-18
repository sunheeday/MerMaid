#!/usr/bin/env python3
"""맥을 폰 대신 BLE Central로 써서 Pi(P6·P5)와 ESP32 브리지를 끝에서 끝까지 확인하는 시험.

  맥 → BLE → ESP32-S3 (v7) → UART3 → Pi P6 → P5 → (rf_stub = P4·P2 대역)

손으로 본딩하지 않고 붙기 때문에 펌웨어 v7의 페어링 요청(브리지가 먼저 보안 요청)도 함께 확인된다.

준비 (맥, 레포 폴더에서 한 번):
  python3 -m venv ~/SE_project/venv-ble && ~/SE_project/venv-ble/bin/pip install bleak

실행:
  ~/SE_project/venv-ble/bin/python bridge/tools/ble_check.py p6     # 시험 C: Pi에서 p6_comm --no-p4 만
  ~/SE_project/venv-ble/bin/python bridge/tools/ble_check.py full   # 시험 D: p6_comm → p5_devmgr → rf_stub --cycle --motion

옵션: --name MSAgent-xxxx (브리지가 여러 개일 때), --dev ID (이 맥이 쓸 폰 ID),
      --with-response (응답 있는 쓰기), --skip-link-timeout (p6_comm을 --no-link-timeout으로 띄웠을 때),
      --verbose (HB·MOT까지 모두 출력)

처음 실행하면 macOS가 블루투스 권한(터미널 앱)이나 "연결 요청" 창을 띄울 수 있다 → 허용.
"""
import argparse
import asyncio
import json
import math
import sys
import time
from pathlib import Path

try:
    from bleak import BleakClient, BleakScanner
except ImportError:
    sys.exit("bleak가 없습니다: ~/SE_project/venv-ble/bin/pip install bleak 후 그 파이썬으로 실행하세요")

UUIDS_FILE = Path(__file__).resolve().parents[2] / "protocol" / "uuids.json"
_u = json.loads(UUIDS_FILE.read_text())
NAME_PREFIX, SVC, RX, TX = _u["name_prefix"], _u["service"], _u["rx"], _u["tx"]

T0 = time.monotonic()
failures = []


def now():
    return time.monotonic() - T0


def say(text):
    print(f"{now():8.2f}  {text}", flush=True)


def check(cond, what):
    print(f"{now():8.2f}  " + ("  ok   " if cond else "  FAIL ") + what, flush=True)
    if not cond:
        failures.append(what)
    return cond


def step(title, pi_hint=None):
    print(f"\n{now():8.2f}  ── {title}", flush=True)
    if pi_hint:
        print(f"{'':10}Pi에서 볼 것: {pi_hint}", flush=True)


def is_int(m, tier=None, on=None):
    return (m.get("t") == "INT" and (tier is None or m.get("tier") == tier)
            and (on is None or m.get("on") is on))


class Phone:
    """폰 흉내: 브리지에 붙어 줄을 쓰고, Notify를 '\\n'에서 합쳐 JSON으로 모은다."""

    def __init__(self, args):
        self.args = args
        self.client = None
        self.msgs = []          # (시각, dict)
        self.buf = b""
        self.connected = False
        self.disc_at = None
        self.hb_on = False
        self.hb_seq = 0
        self.hb_task = None

    # ---- 연결 ----
    async def connect(self):
        say("브리지 찾는 중 (" + (self.args.name or NAME_PREFIX + "*") + ")")

        def match(d, ad):
            name = d.name or ad.local_name or ""
            return name == self.args.name if self.args.name else name.startswith(NAME_PREFIX)

        dev = await BleakScanner.find_device_by_filter(match, timeout=20)
        if dev is None:
            raise RuntimeError("브리지를 못 찾았습니다 (ESP32 전원·광고, 맥 블루투스 확인)")
        say(f"찾음: {dev.name}")
        self.client = BleakClient(dev, disconnected_callback=self._on_disc, timeout=20)
        await self.client.connect()
        self.connected, self.disc_at, self.buf = True, None, b""
        self.t_conn = now()
        say("BLE 연결됨 → 브리지가 페어링 요청, 암호화 후 Pi에 CONNECT")
        for i in range(6):  # 암호화 전이면 Notify 켜기가 실패할 수 있어 몇 번 재시도
            try:
                await self.client.start_notify(TX, self._on_notify)
                return
            except Exception as e:  # noqa: BLE001
                say(f"Notify 켜기 재시도 ({i + 1}): {e}")
                await asyncio.sleep(2)
        raise RuntimeError("TX Notify를 켜지 못했습니다 (페어링 실패?)")

    async def disconnect(self):
        self.hb_on = False
        if self.client and self.connected:
            try:
                await self.client.disconnect()
            except Exception:  # noqa: BLE001
                pass
        await self.wait_disconnect(5)

    def _on_disc(self, _client):
        self.connected = False
        self.disc_at = now()
        self.hb_on = False
        say("BLE 끊김")

    # ---- 받기 ----
    def _on_notify(self, _char, data):
        self.buf += bytes(data)
        while b"\n" in self.buf:
            line, self.buf = self.buf.split(b"\n", 1)
            text = line.decode("utf-8", "replace").strip()
            if not text:
                continue
            try:
                m = json.loads(text)
            except ValueError:
                say(f"JSON 아님: {text!r}")
                continue
            self.msgs.append((now(), m))
            if self.args.verbose or m.get("t") not in ("HB", "MOT"):
                say(f"<< {text}")

    async def wait_msg(self, pred, timeout, since=0):
        """since 번째 이후로 pred에 맞는 메시지를 기다린다. (인덱스, 시각, 메시지) 또는 None"""
        end = time.monotonic() + timeout
        i = since
        while True:
            while i < len(self.msgs):
                t, m = self.msgs[i]
                if pred(m):
                    return i, t, m
                i += 1
            if time.monotonic() >= end or not self.connected:
                return None
            await asyncio.sleep(0.05)

    def count(self, pred, t_from, t_to):
        return [m for t, m in self.msgs if t_from <= t < t_to and pred(m)]

    async def wait_disconnect(self, timeout):
        end = time.monotonic() + timeout
        while self.connected and time.monotonic() < end:
            await asyncio.sleep(0.05)
        return not self.connected

    # ---- 보내기 ----
    async def send(self, obj):
        line = json.dumps(obj, separators=(",", ":"))
        if obj.get("t") != "HB" or self.args.verbose:
            say(f">> {line}")
        await self.client.write_gatt_char(RX, (line + "\n").encode(), response=self.args.with_response)

    def start_hb(self):
        self.hb_on = True
        if self.hb_task is None or self.hb_task.done():
            self.hb_task = asyncio.create_task(self._hb_loop())

    async def _hb_loop(self):
        while self.hb_on and self.connected:
            try:
                await self.send({"t": "HB", "seq": self.hb_seq})
                self.hb_seq += 1
            except Exception as e:  # noqa: BLE001
                say(f"HB 쓰기 실패: {e}")
            await asyncio.sleep(1.0)

    async def hello(self):
        await self.send({"t": "HELLO", "role": "app", "dev": self.args.dev, "v": 1})


# ---------------------------------------------------------------------------
async def check_connect_and_hb(ph, label):
    step(f"{label}: 연결, 페어링, Pi HB", "P6 창에 'RX <n>|CONNECT' (암호화 끝난 뒤)")
    n_start = len(ph.msgs)  # 이전 연결에서 받은 HB를 다시 세지 않도록 이번 연결 이후만 본다
    await ph.connect()
    r = await ph.wait_msg(lambda m: m.get("t") == "HB", 20, n_start)
    if not check(r is not None, "암호화 후 Pi(P6)의 HB 수신 → 페어링·UART 양방향 OK"):
        say("HB가 20초 동안 없음. 원인 후보 (위에서부터 확인):")
        say("  1) Pi에서 p6_comm이 안 떠 있음 / uart3.sh를 안 돌림")
        say("  2) Pi 핀 7 → ESP32 GPIO18 선 (Pi→ESP32 방향)")
        say("  3) 페어링 실패: ESP32 USB 로그에 'security: encrypted=1'이 있는지 (screen /dev/cu.usbserial-* 115200)")
        return False
    _, t_first, _ = r
    say(f"첫 HB까지 {t_first - ph.t_conn:.1f}초 (BLE 연결부터)")
    await asyncio.sleep(3.5)
    hbs = ph.count(lambda m: m.get("t") == "HB", t_first, t_first + 3.5)
    seqs = [m.get("seq") for m in hbs]
    check(3 <= len(hbs) <= 5, f"HB 약 1초마다 (3.5초에 {len(hbs)}번)")
    check(all(isinstance(s, int) for s in seqs) and seqs == sorted(seqs), f"HB seq 증가 {seqs}")
    return t_first


async def run_p6(ph, args):
    print("시험 C: Pi에서 P6 혼자 (root, uart3.sh 다음):  /tmp/p6_comm --no-p4")
    t_first = await check_connect_and_hb(ph, "[1]")
    if not t_first:
        return

    step("[2] HELLO 없이 5초 → P6가 DROP", "P6 창에 'TX <n>|DROP'")
    gone = await ph.wait_disconnect(8)
    check(gone, "HELLO가 없으니 끊김")
    if gone:
        # CONNECT는 첫 HB 직전(최대 1초 앞)이므로 끊긴 시각 - 첫 HB ≈ 4~6초
        dt = ph.disc_at - t_first
        check(3.5 <= dt <= 7.5, f"끊긴 시각이 CONNECT 후 약 5초 (첫 HB 후 {dt:.1f}초)")

    await asyncio.sleep(2)
    t_first = await check_connect_and_hb(ph, "[3] 다시 연결")
    if not t_first:
        return
    step("[4] HELLO·SCR·OVR 보내기 (P5가 없으니 폰에 오는 건 HB뿐)",
         "P6 창에 'ERR cannot send to P5'(정상), 'P4 SCR <dev> on=1 att=1', 'P4 OVR ...'")
    n0 = len(ph.msgs)
    await ph.hello()
    ph.start_hb()
    await asyncio.sleep(0.5)
    await ph.send({"t": "SCR", "on": True, "att": True})
    await ph.send({"t": "OVR", "dev": "display-01", "on": False})
    await asyncio.sleep(7)
    check(ph.connected, "HELLO 뒤에는 7초 넘게 연결 유지")
    others = [m for _, m in ph.msgs[n0:] if m.get("t") not in ("HB",)]
    check(not others, f"P5가 없으니 HB 말고 받은 것 없음 ({len(others)}개)")


async def run_full(ph, args):
    print("시험 D: Pi에서 (root) 이 순서로:  /tmp/p6_comm  →  /tmp/p5_devmgr --state /tmp/rfstate"
          "  →  /tmp/rf_stub --cycle --motion")
    t_first = await check_connect_and_hb(ph, "[1]")
    if not t_first:
        return

    step("[2] HELLO → P5 등록, 지금 상태(INT tier 1·2·3) 받기",
         "P5 창에 hello 로그, rf_stub 창에 10초 안에 'registry: 1 device(s) " + args.dev + "'")
    n0 = len(ph.msgs)
    await ph.hello()
    ph.start_hb()
    got = {}
    for tier in (1, 2, 3):
        r = await ph.wait_msg(lambda m, k=tier: is_int(m, k), 4, n0)
        if r:
            got[tier] = r[2].get("on")
    check(set(got) == {1, 2, 3}, f"HELLO 후 INT tier 1·2·3 상태 수신 {got}")

    step("[3] SCR 보내기", "rf_stub 창에 'SCR " + args.dev + " on=1 att=1'")
    await ph.send({"t": "SCR", "on": True, "att": True})

    step("[4] rf_stub 주기(10초씩: T1 ON → T2 ON → T3 ON → 모두 OFF)에서 Tier 3 ON 기다리기 (최대 45초)")
    r = await ph.wait_msg(lambda m: is_int(m, 3, True), 45, n0)
    if check(r is not None, "INT tier 3 on (이 폰의 점 찍기)"):
        idx3, t3, _ = r
        await asyncio.sleep(2.5)
        mots = ph.count(lambda m: m.get("t") == "MOT", t3 + 0.5, t3 + 2.5)
        check(15 <= len(mots) <= 25, f"MOT 약 10 Hz (2초에 {len(mots)}줄)")
        ok_fields = bool(mots) and all(
            isinstance(m.get("ax"), (int, float)) and isinstance(m.get("ay"), (int, float))
            and math.isfinite(m["ax"]) and math.isfinite(m["ay"]) for m in mots)
        check(ok_fields, "MOT에 ax·ay 숫자")
        check(any("pax" in m and "pay" in m for m in mots), "MOT에 예측값 pax·pay (rf_stub --motion)")

        step("[5] 수동 끄기: 내 점 찍기 OVR off", "rf_stub 창에 'OVR', P5 창에 tier 3 off")
        n5 = len(ph.msgs)
        t5 = now()
        await ph.send({"t": "OVR", "dev": args.dev, "on": False})
        r = await ph.wait_msg(lambda m: is_int(m, 3, False), 3, n5)
        check(r is not None, "INT tier 3 off")
        await asyncio.sleep(1.5)
        late = ph.count(lambda m: m.get("t") == "MOT", (r[1] if r else t5) + 0.5, now())
        check(not late, f"OFF 뒤 MOT 멈춤 ({len(late)}줄)")

        step("[6] 남의 폰 OVR on (거부돼야 함)", "P6 창에 거부 로그, rf_stub 창에 OVR 없음")
        n6 = len(ph.msgs)
        await ph.send({"t": "OVR", "dev": "someone-else-phone", "on": True})
        await asyncio.sleep(2)
        check(not any(is_int(m, 3, True) for _, m in ph.msgs[n6:]), "내 Tier 3가 켜지지 않음")

    step("[7] 공용 기기 Tier 1·2 ON 받기 (최대 45초)")
    r1 = await ph.wait_msg(lambda m: is_int(m, 1, True), 45, n0)
    check(r1 is not None, "INT tier 1 on (송풍 airflow-01, 모든 폰에 표시용)")
    r2 = await ph.wait_msg(lambda m: is_int(m, 2, True), 25, n0)
    check(r2 is not None, "INT tier 2 on (차량 화면 display-01)")
    t2 = now()
    phone_mot = [m for t, m in ph.msgs if t > t2 - 0.5 and m.get("t") == "MOT"]
    say(f"(참고) Tier 2만 켜진 동안 폰에 온 MOT {len(phone_mot)}줄 — 폰은 Tier 3일 때만 받는 것이 정상")

    step("[8] 끊고 다시 연결 + HELLO → 지금 상태 다시 받기", "P5 창에 bye → hello, 같은 dev")
    await ph.disconnect()
    await asyncio.sleep(2)
    if not await check_connect_and_hb(ph, "[8] 다시 연결"):
        return
    n8 = len(ph.msgs)
    await ph.hello()
    ph.start_hb()
    got = {}
    for tier in (1, 2, 3):
        r = await ph.wait_msg(lambda m, k=tier: is_int(m, k), 4, n8)
        if r:
            got[tier] = r[2].get("on")
    check(set(got) == {1, 2, 3}, f"재연결 후 INT tier 1·2·3 다시 수신 {got}")

    if args.skip_link_timeout:
        say("(--skip-link-timeout: 무응답 DROP 확인 건너뜀)")
        return
    step("[9] 폰 HB를 멈추면 5초 뒤 P6가 DROP", "P6 창에 'TX <n>|DROP', P5 창에 bye")
    ph.hb_on = False
    t9 = now()
    gone = await ph.wait_disconnect(9)
    check(gone, "무응답이라 끊김 (p6_comm을 --no-link-timeout 없이 띄웠을 때)")
    if gone:
        check(3.5 <= ph.disc_at - t9 <= 7.5, f"약 5초 뒤 ({ph.disc_at - t9:.1f}초)")


async def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["p6", "full"], help="p6 = 시험 C (P6 혼자), full = 시험 D (P6+P5+rf_stub)")
    ap.add_argument("--name", help="브리지 광고 이름 (예: MSAgent-1A2B). 없으면 MSAgent-로 시작하는 첫 기기")
    ap.add_argument("--dev", default="mac-check-01", help="이 맥이 HELLO에 쓸 폰 ID")
    ap.add_argument("--with-response", action="store_true", help="RX에 응답 있는 쓰기 (기본: 앱처럼 응답 없는 쓰기)")
    ap.add_argument("--skip-link-timeout", action="store_true", help="무응답 DROP 확인 건너뛰기")
    ap.add_argument("--verbose", action="store_true", help="HB·MOT까지 출력")
    args = ap.parse_args()

    ph = Phone(args)
    try:
        await (run_p6 if args.mode == "p6" else run_full)(ph, args)
    except Exception as e:  # noqa: BLE001
        check(False, f"중단: {e}")
    finally:
        await ph.disconnect()

    print()
    if failures:
        print(f"실패 {len(failures)}개:")
        for f in failures:
            print("  - " + f)
        sys.exit(1)
    print("모두 통과")


if __name__ == "__main__":
    asyncio.run(main())
