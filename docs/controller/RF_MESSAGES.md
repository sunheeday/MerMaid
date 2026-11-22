# RF 메시지 정의 (P5·P6) v1

> 작성 정유경 · 2026-11-16 · 상태: **김승희 리뷰 필요** (메시지 번호, DEV_SYNC 방식, MOT 부호)
> 코드: `controller/qnx/rf_messages.hpp` (구조체), `controller/common/comm.*` (P6 규칙), `controller/common/devmgr.*` (P5 규칙),
> `controller/qnx/p6_comm.cpp`, `p5_devmgr.cpp` (프로세스), `rf_stub.cpp` (시험용 P4·P2 대역)
> 기기 메시지(JSON)와 UART 프레임은 `protocol/PROTOCOL.md`가 기준. 이 문서는 Pi 안의 P5·P6 메시지를 정한다.

```
 앱·HUD ──(A) BLE JSON M00~M07──▶ ESP32 ──(B) UART 줄──▶ P6 ──(C) QNX 메시지──▶ P5 · P4
                                                           ◀── P2 MOTION
```

| 층 | 형식 | 기준 |
|---|---|---|
| (A) 기기 메시지 | JSON 한 줄 | `protocol/PROTOCOL.md` §3, `messages.schema.json` |
| (B) UART 프레임 | `<id>\|<payload>\n` | `protocol/PROTOCOL.md` §1, `firmware/bridge/` |
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
| DEV_HELLO | 0x8010 | P6 → P5 | 이벤트 | conn, role, ver, dev[64], int_known, int_on (P6이 이 연결에 마지막으로 보낸 INT, bit t-1 = Tier t) | 빈 |
| DEV_BYE | 0x8011 | P6 → P5 | 이벤트 | conn (ALL = 0xFF: P6이 다시 시작해서 전부 끊음) | 빈 |
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
| OUT_RESYNC | 0x8044 | P5 → P6 | P5 시작 때 (전달될 때까지 1초마다) | 없음: 등록된 기기를 DEV_HELLO로 다시 알려 달라 | 빈 |
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
- 상대가 다시 시작하면 옛 연결로 보낸 첫 번은 실패하고 연결이 닫힌다. P5·P6·rf_stub은 이때 **바로 한 번 더** 보낸다 (새로 연결). 시간 초과는 연결이 남으므로 다시 보내지 않는다. **진짜 P4도 TIER_CMD를 이렇게 보내야** P5가 다시 시작한 직후의 명령을 잃지 않는다.

## 3. P6 번역 규칙 (`comm.cpp`)

| 들어온 것 | 처리 |
|---|---|
| `id\|CONNECT` | 옛 등록이 남아 있으면 DEV_BYE, 5초 HELLO 타이머 시작 |
| `id\|DISCONNECT` | 등록돼 있었으면 DEV_BYE, 스트림 끔 |
| `id\|{HELLO}` | role `app`/`hud`, v = 1, dev 검사 → DEV_HELLO. 잘못되면 등록 안 함 (→ 5초 뒤 DROP) |
| `id\|{SCR}` | on·att 둘 다 필수. HUD는 att = 0, 화면 OFF면 att = 0 → CTX_SCREEN |
| `id\|{OVR}` | 대상이 `airflow-01`/`display-01`이면 누구나, 폰 ID면 **그 폰 자신만** → CTX_OVR |
| `id\|{PROF}` | 앱만, mssq 0~54, sens 0.5~2.0 → CTX_PROF |
| `id\|{SYM}` | M08 멀미 점수. 앱만, `misc` 0~10 **정수** → LOG_SYM(P7, 주행 로그 `label` 표). P4·P5에는 보내지 않는다 (기록 전용) |
| `id\|{HB}` | P6에서 끝 |
| HELLO 전의 SCR·OVR·PROF·SYM, 모르는 `t`, 깨진 JSON, 규칙에 안 맞는 값 | 로그 후 버림. 주행 로그에 REJECT 사건(이유 + 원문 앞부분)으로 남김 |
| 시작할 때 한 번 | `0\|DROP` ~ `8\|DROP` (브리지는 빈 번호의 DROP을 무시) + P5에 DEV_BYE ALL. P6만 다시 시작했을 때 기기가 다시 붙어 HELLO 하게 하는 유일한 방법 |
| 1초마다 | `ALL\|{"t":"HB","seq":N}`. P5에 아직 못 알린 등록이 있으면 DEV_HELLO를 다시 보냄 (P5가 늦게 떴을 때, OUT_RESYNC 뒤) |
| SCR·PROF 재전송 | 연결마다 마지막 SCR·PROF를 기억한다 (기기는 SCR를 바뀔 때만, PROF를 연결 직후 한 번만 보내므로). P4에 보내기가 실패하면 성공할 때까지 1초마다, 아니면 10초마다 CTX_SCREEN·CTX_PROF를 다시 보냄 (P4가 늦게 떴거나 조용히 재시작했을 때). OVR는 동작이라 다시 보내지 않는다. P4는 같은 값이면 바뀌는 것이 없다 (`on_profile`은 MSSQ가 같으면 현재 민감도 유지, 저장도 안 함) |
| HELLO 없이 5초 | `id\|DROP` 한 번 |
| 등록된 기기가 5초 동안 아무것도 안 보냄 | `id\|DROP` + DEV_BYE (PROTOCOL.md §3. 브리지가 재시작되면 DISCONNECT가 안 오므로 이것으로 정리). `--no-link-timeout`으로 끔 (HB를 안 보내는 nRF Connect 시험용) |
| OUT_INT / OUT_PROF / OUT_DROP | 해당 줄. OUT_INT는 연결마다 보낸 값을 기억해 DEV_HELLO의 int_known·int_on에 싣는다 (CONNECT·다른 기기 HELLO 때 지움) |
| OUT_RESYNC | 등록된 연결을 모두 'P5에 아직 못 알림'으로 되돌림 → 다음 1초 박자에 DEV_HELLO 재전송. 받기 스레드는 표만 바꾼다 (MsgSend 안 함) |
| OUT_STREAM | 스트림 표만 바꿈. 등록 안 된 연결은 켜지 않음 |
| MOTION | 스트림이 켜진 연결마다 `id\|{"t":"MOT",...}`. ax·ay가 NaN이면 안 보냄, pax·pay는 둘 다 있을 때만 |

숫자 형식: MOT 소수 3자리, PROF mssq 2자리·sens 3자리.

## 4. P5 규칙 (`devmgr.cpp`)

| 들어온 것 | 처리 |
|---|---|
| DEV_HELLO | 명부[conn] = {dev, role}. 같은 dev가 다른 conn에 있으면 옛 것 삭제 (재연결). 현재 상태 재전송: 폰 → Tier 1·2·3 INT (Tier 3 ON이면 스트림 ON) + 저장된 프로필이 있으면 OUT_PROF / HUD → Tier 1·2 INT (Tier 2 ON이면 스트림 ON) |
| DEV_BYE | 명부에서 삭제. ALL이면 명부 전부 삭제 (Tier 기억은 남김) |
| 시작 | 1초마다 OUT_RESYNC, P6에 전달되면 멈춤. Tier 1·2·3은 '모름'으로 시작 |
| DEV_HELLO에서 '모름'인 Tier | HELLO에 실린 int_known·int_on으로 되찾음 (P5만 다시 시작했을 때. P4는 바뀐 것이 없으면 명령을 다시 보내지 않으므로). 안전 OFF 중에는 ON을 되살리지 않음. 실린 값이 없으면 OFF. 이미 아는 Tier는 P5가 기준 |
| TIER_CMD | 상태 기억. Tier 1·2(공용): HUD에 INT, 모든 폰에도 INT (표시용), HUD 스트림 = Tier 2. Tier 3: 그 폰에 INT + 스트림 |
| DEV_SYNC | 마지막 시각 갱신, 명부 회신 |
| 30초 동안 DEV_SYNC 없음 (시작부터 30초 유예) | 모든 연결에 Tier 1·2·3 OFF, 스트림 모두 OFF, 로그 "safe off" (`policy.json` safety_timeout_s). DEV_SYNC가 다시 올 때까지 ON 명령은 적용하지 않고 **기기별로 마지막 것을 잡아 둔다** (OFF는 통과, 잡아 둔 ON을 지움). DEV_SYNC로 풀리면 다음 1초 박자에 잡아 둔 ON을 적용 (P4는 TIER_CMD를 DEV_SYNC보다 먼저 보내므로, 버리면 P4가 다시 뜬 첫 ON이 사라진다. [`RF_AI_LINK.md`](RF_AI_LINK.md) §5-7) |
| Tier 3 명령인데 그 폰이 연결돼 있지 않음 | 상태만 기억. 다시 붙으면 HELLO 때 보냄 |
| 잘못된 TIER_CMD (기기와 tier가 안 맞음, `hud-01`) | 로그 후 버림 |

프로필은 P4가 `state/profiles/<ID>.json`에 저장한다. P5는 OUT_PROF를 위해 읽기만 한다.

### 4-1. 주행 로그로 보내는 것 (P6·P5 → P7)

폰·HUD와 오간 사건은 모두 P7(`mermaid_p7`)에 `LOG_CTX`(0x8062)로, 멀미 점수는 `LOG_SYM`(0x8063)으로 보낸다. 종류와 내용은
[`DATA_LOGGING.md`](DATA_LOGGING.md) §6-2 표. HB와 MOT는 보내지 않는다.

| 누가 | 무엇 |
|---|---|
| P6 | 기기가 보낸 HELLO·BYE·SCR·OVR·PROF, 링크 사건 CONNECT·DISCONNECT·DROP(+이유), Pi가 내보낸 INT·PROF 회신, 버린 메시지 REJECT(+이유·원문), SYM |
| P5 | 안전 OFF 진입(SAFE_OFF)·해제(SAFE_CLEAR), 잡아 둔 ON 적용(HELD_ON) |

- **로그는 제어를 막지 않는다.** P7에는 2 ms 제한으로 보내고, 실패하면 세기만 한다. P7은 아무에게도 MsgSend 하지 않아 교착 고리가 없다.
- P6의 받기 스레드는 여전히 MsgSend 하지 않는다(§2-2). 받기 스레드에서 생긴 사건(INT, PROF 회신, P5가 시킨 DROP)은 그 자리에서
  시각을 찍어 큐(256개)에 넣고, UART 스레드가 100 ms 안에 P7에 보낸다. 한 바퀴에 실패는 한 번만 기다린다(2 ms).
- P5는 P6에 보낼 것을 다 보내고 잠금을 푼 뒤에 P7에 보낸다.

## 5. 확정 필요

| 항목 | 지금 값 | 누구 |
|---|---|---|
| 메시지 번호, DEV_SYNC 회신 방식 | 위 §2 | 김승희 |
| CTX_SCREEN을 P5에도 보낼지 (명부의 screen·att용) | 안 보냄. DevEntry의 screen·att는 비어 있음 | 김승희 |
| MOT 부호 | ax + = 앞으로 가속 (PROTOCOL.md §5), ay + = 왼쪽 가속 (ISO 8855, 임시) | 김승희·이혁진 |
| MOT 값 | P2의 Wf 가중 a_w.x, a_w.y | 김승희 |
| pax·pay | trip 열 `pred_ax_10s`, `pred_ay_10s` | 김승희 |
| HUD HELLO의 dev | 무엇을 보내든 `hud-01`로 등록 | 이준민 (HUD는 `hud-01`을 보내면 됨) |

## 6. Pi에서 실행

시작 순서: **P6 → P5 → P4 → P3 → P2 → P1** (받는 쪽이 먼저). 모두 root, `uart3.sh` 다음에.

```
p6_comm                         # UART3. 손으로 시험할 때는 --no-link-timeout, P4가 아직 CTX_*를 못 받으면 --no-p4
p5_devmgr --state <P4와 같은 state 폴더>  # --safety-s는 시험에서만 줄임 (기본 30)
rf_stub --cycle --motion        # 진짜 P4·P2 대신 (P5·P6만 시험할 때). 진짜 P4를 띄울 때는 rf_stub을 띄우지 않는다 (같은 이름)
# 진짜 P1~P4와 함께: RF=1 controller/qnx/run_pipeline.sh ... (controller/bringup/README.md)
```

Pi 없이 시험 (Mac, Linux): `ctest`의 `rf_sim`이 위 세 프로세스를 실제로 함께 띄운다. QNX 메시지 전달은
`controller/tests/qnxsim`(Unix 소켓), `/dev/ser3`는 가상 터미널, ESP32는 `controller/tests/rf_sim_test.py`가 맡는다.
QNX 커널과 UART 드라이버 자체는 확인하지 못하므로 Pi 시험은 따로 필요하다.

```
cmake -S controller -B build && cmake --build build && ctest --test-dir build --output-on-failure
```

### 6-1. 하나만 다시 시작했을 때

| 다시 시작 | 무엇이 비나 | 복구 |
|---|---|---|
| P5만 | 명부, Tier 1·2·3 기억 | 새 P5가 OUT_RESYNC → P6이 다음 1초 박자에 등록된 기기마다 DEV_HELLO (그 기기에 마지막으로 보낸 INT를 실어서) → P5가 명부와 Tier를 되찾고 같은 상태를 다시 보냄. 기기 화면은 바뀌지 않고, P6의 스트림 표도 그대로. P4의 다음 DEV_SYNC부터 명부가 다시 보임 |
| P6만 | 연결 표 | 새 P6이 시작하자마자 `0~8\|DROP` + P5에 DEV_BYE ALL → 브리지가 모두 끊음 → 기기가 다시 붙어 HELLO → P5가 기억한 상태(Tier 3 포함)를 보내고 스트림도 다시 켬. 연결 번호는 바뀔 수 있다 |

`rf_sim`이 둘 다 실제 프로세스로 확인한다 ([11]·[12]단계).

남은 한계:
- P5가 내려가 있는 동안 P4가 보낸 TIER_CMD는 잃는다 (P4는 바뀐 것만 보냄). P4의 다음 명령부터 맞는다.
- P5가 내려가 있는 동안 끊겨 있던 폰의 Tier 3 기억은 되찾지 못한다 (P6에 기록이 없음). 다시 붙으면 OFF로 시작.
- P5 시작 직후 1초 안에 기록 없는 새 기기가 먼저 HELLO 하면 Tier 1·2를 OFF로 정한다 (드묾).
- P6을 다시 시작하면 기기가 몇 초 끊겼다 다시 붙는다 (앱·HUD의 자동 재연결에 기댐).

알려진 문제 (2026-11-18 Pi 시험에서 발견, `controller/bringup/README.md` RF 절):
- ~~P4가 없을 때 온 SCR·PROF를 P6이 버림~~ → 2026-11-18 고침: P6이 연결마다 기억했다가 다시 보낸다 (§3 표의 "SCR·PROF 재전송")
- 맥 Chrome(Web Bluetooth) ↔ ESP32: 연결은 되지만 페어링이 끝나지 않아 HELLO가 안 나감 → 원인: 브리지 펌웨어 v6이 페어링을 시작하게 하지 않았음. v7에서 고침 (`fix/rf-esp32` 브랜치의 `firmware/bridge/README.md`). 맥 bleak로 확인, Chrome·Android 앱은 다시 확인 필요

## 7. P4·P2 연결 (2026-11-18 완료, 김승희)

- P4 `--rf`: RISK마다(10초)와 RIDE_START에 DEV_SYNC를 보내고, 회신 명부로 `add_phone` / `remove_phone` / `set_hud_connected`.
  새로 붙은 폰은 저장된 프로필과 허용도를 읽어 온다. PROF가 아직 없으면 P3에 기본 프로필(MSSQ 12.90, 민감도 1.0), EV는 NaN
- P4: CTX_SCREEN·CTX_OVR·CTX_PROF를 받자마자 회신하고 `on_screen`·`on_override`·`on_profile`에 넘긴다. OVR는 마지막 RISK의 주행 시각으로 처리
- P4: `CMD` stdout과 함께 TIER_CMD를 P5로 (제한 200 ms, 실패는 `tier_cmd_fail`로 센다)
- P4: P5에 보내다 실패하고 연결이 닫혔으면 (`!connected()`) 바로 한 번 더 보낸다 (DEV_SYNC, TIER_CMD. P5가 다시 시작한 직후, §2-2)
- P2 `--motion`: 10샘플(100 ms)마다 MOTION {t, Wf 가중 ax·ay, pred_ax_10s, pred_ay_10s}를 P6로 (제한 20 ms). 빠른 재생에서 실제 시계 50 ms 미만 간격은 건너뛰고 `mot_skip`으로 센다
- `--rf`/`--motion` 없이 띄우면 예전처럼 `--phone`/`--hud` 고정 등록 (AC-02 비교용)
- 주행이 없으면 DEV_SYNC도 없으므로 30초 뒤 P5가 안전 OFF 한다. 다음 주행의 RIDE_START에서 풀린다
- 시험: Mac `ctest`의 `pipeline_rf_sim` (P1·P2·P4·P5·P6 진짜 코드 + `tests/fake_p3.cpp`), Pi는 `controller/bringup/README.md`의 RF 절
- 동작·실행·결정 사항·Pi 검증 결과(V1~V8)는 [`RF_AI_LINK.md`](RF_AI_LINK.md)
