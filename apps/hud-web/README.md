# MerMaid 화면 UI (웹) — 차량 디스플레이 · HUD · 탑승객 앱 v0.2 (PRD 단계 S8)

기준: 이 저장소의 `protocol/` (PROTOCOL.md · uuids.json · constants.json · messages.schema.json)과 `docs/controller/RF_MESSAGES.md` (RF P5·P6, `feat/rf-p5-p6`).
화면 규격: [`docs/proposal/ui/MerMaid_화면정의서_v0.2.pdf`](../../docs/proposal/ui/MerMaid_화면정의서_v0.2.pdf) · 요구사항: [`docs/proposal/ui/MerMaid_요구사항정의서_v0.1.pdf`](../../docs/proposal/ui/MerMaid_요구사항정의서_v0.1.pdf)
실제 탑승객 앱(Android, 타 앱 위 오버레이·백그라운드)은 [`apps/android/`](../android/README.md).

## 실행
ES 모듈이라 파일을 바로 열면(file://) 안 되고, 아무 정적 서버로 열면 됩니다.
```
cd apps/hud-web
python -m http.server 8765
```
| 연결 방식 | 주소 | 설명 |
|---|---|---|
| 데모 (브라우저 단독) | `http://localhost:8765/display.html?demo` | 가짜 컨트롤러 `js/demo.js` (P5 규칙, 90초 반복). `hud.html?demo`, `app.html?screen=home&demo` |
| 실제 브리지 (BLE) | `http://localhost:8765/display.html?manual` → **BLE 브리지 연결** | Chrome/Edge Web Bluetooth로 ESP32 `MSAgent-xxxx`에 직접 연결 (role `hud`). Pi에서 `p6_comm` · `p5_devmgr` · `rf_stub --cycle --motion` |
| WebSocket 중계 | `?ws=ws://주소/ws` | 같은 한 줄 JSON을 WebSocket으로 주고받는 중계 서버가 있을 때 (팀 시연용 로컬 시뮬레이터. 이 저장소에는 없음) |

`?manual` → 자동 연결 안 함. 주소에 아무것도 없으면 같은 호스트의 `/ws`에 자동 연결을 시도합니다(정적 서버에서는 2초마다 재시도만 하고 무해).

| 화면 | 파일 | RF 역할 | 받는 메시지 | 보내는 메시지 |
|---|---|---|---|---|
| 차량 디스플레이 (1920×720) | `display.html` | `hud`, dev `hud-01` | INT tier 1·2, MOT (Tier 2 ON 동안), HB, (로컬 백엔드만) DBG | HELLO, SCR(on, att=false), OVR(`airflow-01`·`display-01`), HB |
| HUD (검은 바탕, 반사 모드) | `hud.html` | 디스플레이 연결 공유 (없으면 직접 `hud`) | 위와 같음 | 위와 같음 (직접 연결일 때) |
| 탑승객 앱 (폰 세로) | `app.html` | `app`, dev = 폰 UUID | INT tier 1·2·3, MOT (Tier 3 ON 동안), PROF, HB | HELLO, SCR(on, att), OVR(공용 2개 + 내 ID), PROF, HB |

### 차량 디스플레이 · HUD v0.2 (화면정의서 DSP-01 · HUD-01 기준)
- **화면 OFF는 1.2초 길게 눌러야** 꺼짐 (DSP-F-07, 시연 중 실수 방지). 짧게 누르면 "길게 눌러 끄기" 안내, 꺼진 화면은 한 번 탭하면 켜짐
- 연결 전·끊김: 송풍·앰비언트는 **"알 수 없음"** + 스위치 비활성 (앱 APP-05 ⑦과 같은 규칙)
- 수동 조작 시 **LEARN 안내**: 켜진 지 30초 안에 끄면 "다음부터 덜 켜지고 5분 동안 다시 켜지지 않아요", 직접 켜면 "다음부터 조금 더 일찍 켜져요" (POLICY.md 규칙 설명, 판단은 컨트롤러)
- RF 로그에서 HB 줄은 빼고 개수만 (로그 상단 HB↑/↓)
- HUD: 디스플레이 화면을 끄면 HUD도 함께 꺼짐(같은 차량 화면 앱), 연결이 끊기면 페이드 없이 즉시 꺼짐 (NFR-01)

### 탑승객 앱 웹 시연판 (`app.html`, v0.2)
화면정의서 MM-UI-001과 같은 규격(Galaxy S22+ 384 × 832 dp)·디자인. PC에서는 폰 틀 안에 같은 비율로, 폰(가로 520 미만)에서는 화면을 꽉 채워 1 dp = 1 px로 그립니다.
- 화면: APP-01 시작 → 02 권한(웹은 흐름만) → 03 차량 등록(시스템 창 모양: 서버 · 데모 · 실제 BLE 중 선택) → 04 설문·결과 → 05 홈 · 06 설정 · 07 RF 진단
- 시뮬레이션: **OVL-01** 다른 앱(동영상 앱) 위 모션 큐, **OVL-02** 가로 전체 화면(⛶), **SYS-01** 알림창(상태 표시줄 탭)·빠른 설정 타일
- PC 하단 시연 도구: 다른 앱으로 · 알림창 · 처음부터
- 바로가기: `?screen=home&demo` (데모 차량으로 홈), `?screen=app2&demo`, `?screen=land&demo`, `&shade` 알림창 열기, `?reset` 저장값 지우기
- 실제 앱(타 앱 위 오버레이·백그라운드)은 `apps/android/`

키: 디스플레이 `L` 로그 · `H` HUD 창 · `F` 전체 화면 / HUD `M` 반사 모드 · `F` 전체 화면

## 파일
```
apps/hud-web/
  index.html        화면 고르기
  display.html      차량 디스플레이     js/display.js  css/display.css
  hud.html          HUD                js/hud.js      css/hud.css
  app.html          탑승객 앱          js/app.js      css/app.css
  js/protocol.js    UUID·상수·메시지 생성/검사 (protocol/ 값 그대로)
  js/rflink.js      RF 연결: BLE(Web Bluetooth) · WebSocket · 데모, HELLO·HB 1초·5초 감시
  js/motiondots.js  시각 모션 큐(점)
  js/demo.js        브라우저 단독 가짜 컨트롤러 (P5 규칙대로 INT·MOT 보냄)
  js/common.js      로그, 연결 칩, 수동 조작 대기(낙관적 UI → INT 회신으로 확정, 3초 뒤 되돌림)
  css/common.css    색·글꼴 토큰 (LG 레드 #A50034, 웜 차콜, 웜 오프화이트)
```

## 지킨 규칙
- **fail-safe (NFR-01)**: 5초 동안 아무것도 못 받으면 `lost` → 모든 개입 표시 OFF. 다시 받으면 컨트롤러가 재전송한 상태로 복구
- 모르는 `t`는 무시, 512 B 넘는 줄은 안 보냄, MOT가 0.5초 안 오면 0으로
- **BLE 쓰기**: 브리지(v6)는 Write 하나를 완결된 줄로 처리 → 메시지를 나누지 않고 `writeValueWithoutResponse` 한 번에 (RX = Write Without Response)
- HUD HELLO의 dev는 `hud-01` (RF_MESSAGES.md §5). **hud-01은 한 연결만 등록**되므로 HUD 창은 디스플레이의 연결을 BroadcastChannel로 함께 씀
- 축 규약: ax + = 앞 가속, ay + = 왼쪽 가속 (ISO 8855, **임시** — 김승희·이혁진 확정 필요). 앰비언트 빛은 가속 방향 쪽, 점은 반대(관성) 쪽으로 움직임. 예측값 pax·pay가 있으면 빛·점이 먼저 움직임
- 앱 프로필: MSSQ-Short(9개 탈것 × 어릴 때/최근 10년, 타 본 적 없음 보정) → `sens = clip(exp(0.3·(mssq−12.9)/9.9), 0.5, 2.0)` → PROF. 설문 전이면 기본값 12.90 / 1.0

## 확인 필요
- 수동 조작 OVR의 `dev`는 프로토콜 v1 기기 ID(`airflow-01`, `display-01`, 폰 자신의 ID)를 보냄 (RF_MESSAGES.md §3)
- HUD(Chrome Web Bluetooth) ↔ 브리지 실기 호환은 브리지 README의 "남은 것" — 미검증. 브리지 MTU가 작게 잡히면(23) HELLO(약 70 B)가 한 번에 안 들어감
- 폰에서 PC 주소(`http://192.168…`)로 열면 보안 컨텍스트가 아니라 BLE가 막힘 → 서버 연결만 가능. 실제 BLE는 `localhost`/https 또는 Android 앱(S9)
- 차량 디스플레이의 Tier 3 좌석 표시(A/B)는 시뮬레이터가 보내는 확장 메시지(DBG)가 있을 때만. 실제 Pi는 HUD에 Tier 3을 보내지 않음
- 시간·가속도: 데모 모드는 합성 시나리오(사인파, 정해진 시각에 Tier ON/OFF)이고 AI 판단이 아님. 실제 판단은 Pi의 P3·P4 → P5 → 이 화면
