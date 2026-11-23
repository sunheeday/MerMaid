# MerMaid 프로토콜 v1

QNX 컨트롤러, ESP32-S3 브리지, Android 앱, HUD 시뮬레이터가 공유하는 유일한 계약입니다. 바꿀 때는 이 폴더를 먼저 고치고 네 컴포넌트에 함께 반영합니다.

| 파일 | 내용 |
|---|---|
| `messages.schema.json` | 메시지 M00~M07의 JSON Schema |
| `uuids.json` | BLE 광고 이름, 서비스와 characteristic UUID |
| `constants.json` | 공용 상수: Oman, 이벤트, 휴식, MSSQ 규준, 고정 기기 ID |
| `feature_order.json` | 피처 52개의 순서, 그룹, 단위, 창, 단조 방향, enabled (v5) |
| `check.py` | 위 파일 검증: `python3 protocol/check.py` |

## 1. UART (Raspberry Pi ↔ ESP32-S3)

- Pi UART3 (PL011) `/dev/ser3`, GPIO4 핀 7 (TX) → ESP32 GPIO18 / GPIO5 핀 29 (RX) ← ESP32 GPIO17 / 핀 9 GND, 3.3 V, 115,200 bps, 8N1. mini UART `/dev/ser1`은 QNX 콘솔이라 쓰지 않음 (켜는 법: `firmware/bridge/README.md`)
- 프레임: `<id>|<payload>\n`. `id`는 브리지 연결 인덱스 `0`~`8` 또는 `ALL`. 프레임 최대 512 바이트
- `payload`는 JSON 한 줄 또는 제어 토큰

| 방향 | 토큰 | 뜻 |
|---|---|---|
| ESP32 → Pi | `CONNECT` | 연결 `id`가 BLE로 연결됨 |
| ESP32 → Pi | `DISCONNECT` | 연결 `id`가 끊김 |
| Pi → ESP32 | `DROP` | 연결 `id`를 끊을 것 |

## 2. BLE GATT (ESP32-S3 = Peripheral)

| 항목 | 값 |
|---|---|
| 역할 | ESP32-S3 Peripheral, 폰과 HUD는 Central |
| 광고 이름 | `MSAgent-xxxx` (MAC 마지막 2바이트) |
| 서비스 | `cd820001-2f55-4bae-8e0f-8474006c441e` |
| RX | `cd820002-…`, Write Without Response, 암호화 필수. 기기가 JSON 한 줄을 씀 |
| TX | `cd820003-…`, Notify, 암호화 필수. notification 하나에 JSON 한 줄 |
| MTU | 247 요청 (payload 244). 더 긴 줄은 송신측이 나누고 수신측이 `\n`에서 합침 |
| 최대 메시지 | 512 바이트 |
| 동시 연결 | 최대 9 |
| 보안 | LE Secure Connections, Just Works, 본딩을 플래시에 저장 |
| 핸드셰이크 | 연결 후 5초 안에 HELLO가 없으면 끊음 |

## 3. 메시지 (`t` 필드로 구분)

| ID | t | 방향 | 내용 |
|---|---|---|---|
| M00 | `HELLO` | 기기 → C | 역할(`app` / `hud`), 영구 기기 ID, 프로토콜 버전 |
| M01 | `INT` | C → 기기 | Tier, ON/OFF |
| M02 | `MOT` | C → 기기 | 가중 가속도 ax, ay (10 Hz, m/s²). 경로가 있으면 예측값 pax, pay |
| M03 | `SCR` | 기기 → C | 화면 ON/OFF, 응시 (HUD는 화면만, `att`는 항상 false) |
| M04 | | | 비어 있음 (음성 MISC 보고 제거). 번호를 재사용하지 않음 |
| M05 | `OVR` | 기기 → C | 대상 기기 ID, 수동 ON/OFF. 보낸 쪽은 연결로 판별 |
| M06 | `PROF` | 앱 ↔ C | MSSQ 원점수, 설문 민감도 (허용도는 컨트롤러에만) |
| M07 | `HB` | 기기 ↔ C | 일련번호, 1초마다 |

```
{"t":"HELLO","role":"app","dev":"5f3a9c1e","v":1}
{"t":"INT","tier":3,"on":true}
{"t":"MOT","ax":0.12,"ay":-0.31,"pax":0.3,"pay":-0.5}
{"t":"SCR","on":true,"att":false}
{"t":"OVR","dev":"display-01","on":false}
{"t":"PROF","mssq":12.5,"sens":1.2}
{"t":"HB","seq":231}
```

- 프로토콜 버전은 HELLO에서 한 번만 보냅니다.
- 양쪽 모두 M07을 1초마다 보내고, 5초 동안 아무 메시지도 못 받으면 연결이 끊긴 것으로 봅니다.

## 4. 기기 ID

| 기기 | ID | Tier |
|---|---|---|
| 송풍 (HUD 화면, 공용) | `airflow-01` | 1 |
| 앰비언트 glow (HUD 화면, 공용) | `display-01` | 2 |
| HUD 연결 | `hud-01` | 위 두 기기를 한 연결에 함께 등록 |
| 각 폰 | 앱이 처음 만든 UUID | 3 |

`OVR.dev`는 이 표의 대상 기기 ID입니다. PRD 5.3 예시의 `"dev":"display"`는 `"display-01"`로 맞췄습니다.

## 5. 피처 벡터

- 52칸 고정(`n_features`), float64. 인덱스 0~45는 차량 피처(한 번 계산, `vehicle_range`), 46~51은 승객별(G8 + G10, 폰마다 바꿔 끼움, `passenger_range`)
- 없는 그룹이나 `enabled=false` 피처는 0이 아니라 NaN
- 모델 파일에는 학습에 쓴 `feature_order.json`의 `version`을 기록하고, P3는 다르면 시작을 거부합니다.
- 가속도 축: x 전후(앞 +), y 좌우, z 상하. 피처 가속도는 ISO 2631-1 Wf 가중 후 값, Oman 입력은 가중 전 값(`constants.json`)
