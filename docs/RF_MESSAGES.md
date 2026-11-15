# RF 메시지 정의 (P5·P6) v1

> 작성 정유경 · 2026-11-16 · 상태: **김승희 리뷰 필요** (메시지 번호, DEV_SYNC 방식, MOT 부호)
> 코드: `controller/qnx/rf_messages.hpp` (구조체), `controller/common/comm.{hpp,cpp}` (P6 번역 규칙)
> 기기 메시지(JSON)와 UART 프레임은 `protocol/PROTOCOL.md`가 기준. 이 문서는 Pi 안의 P5·P6 메시지를 정한다.

```
 앱·HUD ──(A) BLE JSON M00~M07──▶ ESP32 ──(B) UART 줄──▶ P6 ──(C) QNX 메시지──▶ P5 · P4
                                                           ◀── P2 MOTION
```

| 층 | 형식 | 기준 |
|---|---|---|
| (A) 기기 메시지 | JSON 한 줄 | `protocol/PROTOCOL.md` §3, `messages.schema.json` |
| (B) UART 프레임 | `<id>\|<payload>\n` | `protocol/PROTOCOL.md` §1, `bridge/` |
| (C) QNX 메시지 | 고정 크기 POD, `mm::msg::Hdr` 머리말 | 이 문서 §2 |

## 1. 프로세스

| 프로세스 | 서비스 이름 | 역할 |
|---|---|---|
| P5 기기 관리 | `mermaid_p5` | 등록 명부, P4 명령을 연결 번호로, 30초 안전 OFF |
| P6 통신 | `mermaid_p6` | UART ↔ QNX 메시지 번역, HELLO 5초 감시, HB 1초, MOT 10 Hz |

## 2. QNX 메시지 (0x8010~)

`messages.hpp`의 0x8001~0x8006에 이어지는 번호. 모두 `Hdr h{TYPE, 0, 0}`로 시작한다.

| 타입 | 번호 | 방향 | 주기 | 내용 | 회신 |
|---|---|---|---|---|---|
| DEV_HELLO | 0x8010 | P6 → P5 | 이벤트 | conn, role, ver, dev[64] | 빈 |
| DEV_BYE | 0x8011 | P6 → P5 | 이벤트 | conn | 빈 |
| DEV_SYNC | 0x8012 | P4 → P5 | 10 s | seq (하트비트 겸) | **DevSyncReply** {n, e[9]: dev, role, screen, att} |
| CTX_SCREEN | 0x8020 | P6 → P4 | 이벤트 | sender, on, att | 빈 |
| CTX_OVR | 0x8021 | P6 → P4 | 이벤트 | sender, device, on | 빈 |
| CTX_PROF | 0x8022 | P6 → P4 | 이벤트 | phone, mssq, sens | 빈 |
| (0x8023) | | | | 비워 둠 (M04 제거, 재사용 안 함) | |
| TIER_CMD | 0x8030 | P4 → P5 | 이벤트 | device[64], tier, on (= `policy.hpp`의 Command) | 빈 |
| OUT_INT | 0x8040 | P5 → P6 | 이벤트 | conn(ALL = 0xFF), tier, on | 빈 |
| OUT_STREAM | 0x8041 | P5 → P6 | 이벤트 | conn(ALL 가능), on: MOT를 보낼지 | 빈 |
| OUT_PROF | 0x8042 | P5 → P6 | 이벤트 | conn, mssq, sens | 빈 |
| OUT_DROP | 0x8043 | P5 → P6 | 이벤트 | conn | 빈 |
| MOTION | 0x8050 | P2 → P6 | 100 ms | t, ax, ay, pax, pay (없으면 NaN) | 빈 |

CTX_*의 필드는 `Policy`의 함수 인자와 그대로 맞췄다: `on_screen(sender, screen, att)`, `on_override(now, sender, device, on)`, `on_profile(phone, mssq, sens0)`. P4는 받은 값을 그대로 넘기면 된다.

### 2-1. 기기 ID (`PROTOCOL.md` §4)

- 폰: 앱이 만든 UUID. `store::safe_name`을 통과하고 63자 이하여야 한다 (P4가 `profiles/<ID>.json`으로 저장하고, 메시지의 `char[64]`에 NUL까지 들어가야 하므로). `airflow-01`, `display-01`, `hud-01`은 폰 ID로 쓸 수 없다.
- HUD: HELLO의 dev와 상관없이 **항상 `hud-01`**. HUD가 등록되면 P4는 `airflow-01`·`display-01`이 있는 것으로 본다 (`Policy::set_hud_connected`).

### 2-2. 교착 방지 (`MsgSend`는 회신까지 멈춘다)

```
P1 → P2 → P3 → P4 → P5 → P6(받기 스레드)
           P2 ─────────────▶ P6(받기 스레드)      MOTION
      P6(UART 스레드) ─▶ P5                       HELLO, BYE
      P6(UART 스레드) ─▶ P4                       SCR, OVR, PROF
```
- P5는 P4에게 먼저 보내지 않는다. 등록 목록은 P4가 10초마다 보내는 DEV_SYNC의 회신으로 준다 (P3 ↔ P4 PROFILES와 같은 방식). 등록 지연은 최대 10초로 추론 주기와 같다.
- P6은 스레드 2개다. UART 스레드는 읽고 위로 보내고, 받기 스레드는 MsgReceive 뒤 즉시 회신하고 UART에 쓴다. 받기 스레드는 MsgSend 하지 않는다. UART 쓰기는 뮤텍스로 보호한다.
- 보내기는 `ipc::Client`의 시간 제한을 쓴다. 상대가 멈춰도 P6의 UART 스레드는 계속 돈다.

## 3. P6 번역 규칙 (`comm.cpp`)

| 들어온 것 | 처리 |
|---|---|
| `id\|CONNECT` | 옛 등록이 남아 있으면 DEV_BYE, 5초 HELLO 타이머 시작 |
| `id\|DISCONNECT` | 등록돼 있었으면 DEV_BYE, 스트림 끔 |
| `id\|{HELLO}` | role `app`/`hud`, v = 1, dev 검사 → DEV_HELLO. 잘못되면 등록 안 함 (→ 5초 뒤 DROP) |
| `id\|{SCR}` | on·att 둘 다 필수. HUD는 att = 0, 화면 OFF면 att = 0 → CTX_SCREEN |
| `id\|{OVR}` | 대상이 `airflow-01`/`display-01`이면 누구나, 폰 ID면 **그 폰 자신만** → CTX_OVR |
| `id\|{PROF}` | 앱만, mssq 0~54, sens 0.5~2.0 → CTX_PROF |
| `id\|{HB}` | P6에서 끝 |
| HELLO 전의 SCR·OVR·PROF, 모르는 `t`, 깨진 JSON | 로그 후 버림 |
| 1초마다 | `ALL\|{"t":"HB","seq":N}` |
| HELLO 없이 5초 | `id\|DROP` 한 번 |
| OUT_INT / OUT_PROF / OUT_DROP | 해당 줄 |
| OUT_STREAM | 스트림 표만 바꿈. 등록 안 된 연결은 켜지 않음 |
| MOTION | 스트림이 켜진 연결마다 `id\|{"t":"MOT",...}`. ax·ay가 NaN이면 안 보냄, pax·pay는 둘 다 있을 때만 |

숫자 형식: MOT 소수 3자리, PROF mssq 2자리·sens 3자리.

## 4. P5 규칙 (구현 예정)

| 들어온 것 | 처리 |
|---|---|
| DEV_HELLO | 명부[conn] = {dev, role}. 같은 dev가 다른 conn에 있으면 옛 것 삭제 (재연결). 현재 상태 재전송: 폰 → Tier 1·2·3 INT (Tier 3 ON이면 스트림 ON) + 저장된 프로필이 있으면 OUT_PROF / HUD → Tier 1·2 INT (Tier 2 ON이면 스트림 ON) |
| DEV_BYE | 명부에서 삭제 |
| TIER_CMD | 상태 기억. Tier 1·2(공용): HUD에 INT, 모든 폰에도 INT (표시용), HUD 스트림 = Tier 2. Tier 3: 그 폰에 INT + 스트림 |
| DEV_SYNC | 마지막 시각 갱신, 명부 회신 |
| 30초 동안 DEV_SYNC 없음 | 모든 연결에 Tier 1·2·3 OFF, 스트림 모두 OFF, 로그 "safe off" (`policy.json` safety_timeout_s) |

프로필은 P4가 `state/profiles/<ID>.json`에 저장한다. P5는 OUT_PROF를 위해 읽기만 한다.

## 5. 확정 필요

| 항목 | 지금 값 | 누구 |
|---|---|---|
| 메시지 번호, DEV_SYNC 회신 방식 | 위 §2 | 김승희 |
| CTX_SCREEN을 P5에도 보낼지 (명부의 screen·att용) | 안 보냄. DevEntry의 screen·att는 비어 있음 | 김승희 |
| MOT 부호 | ax + = 앞으로 가속 (PROTOCOL.md §5), ay + = 왼쪽 가속 (ISO 8855, 임시) | 김승희·이혁진 |
| MOT 값 | P2의 Wf 가중 a_w.x, a_w.y | 김승희 |
| pax·pay | trip 열 `pred_ax_10s`, `pred_ay_10s` | 김승희 |
| HUD HELLO의 dev | 무엇을 보내든 `hud-01`로 등록 | 이준민 (HUD는 `hud-01`을 보내면 됨) |

## 6. 김승희 쪽에서 바꿀 곳 (통합 단계)

- P4: `--phone`/`--hud` 대신 DEV_SYNC 회신으로 등록 (`add_phone`, `remove_phone`, `set_hud_connected`)
- P4: CTX_SCREEN·CTX_OVR·CTX_PROF를 받아 `on_screen`·`on_override`·`on_profile`에 넘기기
- P4: `CMD` stdout과 함께 TIER_CMD를 P5로 보내기
- P2: MOTION을 100 ms마다 P6로 보내기
