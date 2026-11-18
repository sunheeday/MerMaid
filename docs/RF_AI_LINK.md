# RF 링크 ↔ AI(P4·P2) 연결 명세 v1

> 작성 정유경 · 2026-11-19 · 브랜치 `feat/rf-ai-link`
> 함께 읽을 것: [`RF_MESSAGES.md`](RF_MESSAGES.md) (메시지 양식, P5·P6 규칙), [`SPEC.md`](SPEC.md) §3.10·§3.11 (정책, QNX 프로세스), `controller/common/POLICY.md`
> 코드 출처: P4 `--rf`, P2 `--motion`, P6의 SCR·PROF 다시 보내기, `run_pipeline.sh RF=1`, `fake_p3`, `pipeline_rf_sim`은 김승희 님이 Pi에서 작성한 것을 가져왔다. 이 문서는 그 동작을 적고, 빠져 있던 결정(§5)을 채운다.

## 0. 한 줄 요약

**RF 링크**(ESP32 브리지 + P6 통신 + P5 기기 관리)는 폰·HUD와 Pi 사이의 길이다. 이 문서는 그 길의 Pi 안쪽 끝, 곧 **AI 쪽 프로그램(P4 정책, P2 피처)이 RF 링크와 무엇을 주고받는지**를 정한다.

```
 폰·HUD ─BLE─ ESP32 ─UART─ P6 ◀────────── MOTION 100 ms ────────── P2 ◀─ SAMPLE 100 Hz ─ P1
                            │ ▲                                    │ VEH 10 s
              HELLO·BYE     │ │ OUT_INT·STREAM·PROF·DROP           ▼
                            ▼ │                                    P3 (모델 5개)
                            P5 ◀──── DEV_SYNC(회신: 명부), TIER_CMD ──┐ │ RISK 10 s
                                                                    │ ▼
                            P6 ──── CTX_SCREEN · CTX_OVR · CTX_PROF ─▶ P4 (정책)
```

- P3는 폰마다 멀미 확률 5개 `P(MISC ≥ 2..6)`를 10초마다 P4에 준다 (`RISK`). RF 링크는 P3와 직접 말하지 않는다.
- P4가 "어느 기기의 무엇을 켜고 끌지"를 정하고, 그 결정을 P5에 `TIER_CMD`로 보낸다.
- P2는 점 찍기·앰비언트가 쓸 차량 움직임을 100 ms마다 P6에 `MOTION`으로 보낸다.

## 1. P4가 RF 링크와 주고받는 것 (`p4_policy --rf`)

| # | 방향 | 메시지 | 언제 | P4가 하는 일 |
|---|---|---|---|---|
| 1 | P4 → P5 | `DEV_SYNC` (회신 = 명부) | `RISK`를 처리한 직후(10초마다), `RIDE_START` | 명부의 폰 → `add_phone` / 사라진 폰 → `remove_phone`, HUD 유무 → `set_hud_connected`. 새 기기는 저장된 허용도·프로필을 읽어 온다. 출력 `REG phone <ID> on/off`, `REG hud on/off` |
| 2 | P6 → P4 | `CTX_SCREEN{sender, on, att}` | 기기가 SCR를 보낼 때 + P6가 10초마다 다시 | `on_screen` (Tier 2 게이트 = HUD 화면, Tier 3 게이트 = 폰 화면 + 응시) |
| 3 | P6 → P4 | `CTX_OVR{sender, device, on}` | 사용자가 수동 조작 | `on_override(마지막 RISK의 t, …)` → 나온 명령을 바로 보냄. 허용도·쿨다운·민감도 갱신 후 저장 |
| 4 | P6 → P4 | `CTX_PROF{phone, mssq, sens}` | 앱이 PROF를 보낼 때 + P6가 10초마다 다시 | `on_profile` → `state/profiles/<ID>.json` 저장. P3의 `PROFILES` 회신에 반영 |
| 5 | P4 → P5 | `TIER_CMD{device, tier, on}` | 정책이 명령을 낼 때 (`RISK`, `CTX_OVR`) | stdout `CMD <t> <device> <tier> <0\|1>`와 같은 내용 |

규칙:
- **받은 메시지는 복사 → 바로 회신 → 처리.** P4는 P5에만 보내고, P5는 P4에 먼저 보내지 않는다(회신으로만) → 서로 기다리며 멈추는 일이 없다.
- P5로 보내기는 200 ms 제한. P5가 다시 시작해 옛 연결이 끊긴 첫 보내기는 **바로 한 번 더** 보낸다.
- `--rf`와 `--phone`/`--hud`는 함께 쓸 수 있다. `--hud`를 주면 HUD 기기가 없어도 송풍·디스플레이를 등록한다(HUD 화면 켜짐으로 봄).
- RF로 온 폰은 PROF가 오기 전까지 PRD 기본 프로필(mssq 12.9, sens 1.0)로 추론한다. EV 경험은 M06에 없어서 NaN(§5-5).
- 폰은 최대 9대. 넘으면 무시하고 stderr에 남긴다.
- 주행 끝 요약에 `rf_phones`, `rf_hud`, `sync_fail`, `tier_cmd_fail`이 붙는다. `--log` CSV에는 `own_phone` 열(그 행의 폰 Tier 3 상태)이 붙는다.

## 2. P2가 RF 링크에 주는 것 (`p2_feat --motion`)

| 항목 | 값 |
|---|---|
| 메시지 | `MOTION{t, ax, ay, pax, pay}` → P6 (`mermaid_p6`) |
| 주기 | 샘플 10개마다 = 주행 시각 100 ms (`constants.json` `mot_period_ms`) |
| ax, ay | ISO 2631-1 Wf 가중 가속도 (`FeatureExtractor::weighted_ax/ay`), m/s². **ax + = 앞으로 가속, ay + = 왼쪽으로 가속** (임시, §5-6) |
| pax, pay | 주행 파일의 `pred_ax_10s`, `pred_ay_10s` 열. NaN이면 P6가 MOT에 싣지 않는다 |
| P6가 없거나 늦을 때 | 20 ms 뒤 포기하고 `mot_fail`로 센다. 피처 계산은 멈추지 않는다 |
| 빠른 재생 | 앞 MOTION에서 **실제 시계로 50 ms**가 안 지났으면 건너뛰고 `mot_skip`으로 센다 (§5-1) |

P6는 스트림이 켜진 연결(P5의 `OUT_STREAM`: 폰은 Tier 3, HUD는 Tier 2가 켜진 동안)에만 `{"t":"MOT",…}`로 내보낸다.

## 3. P6가 P4를 위해 해 주는 것 (SCR·PROF 다시 보내기)

기기는 SCR를 **바뀔 때만**, PROF를 **연결 직후 한 번만** 보낸다. 그때 P4가 없었거나 나중에 다시 떴으면 P4는 화면 상태와 프로필을 모른다. 그래서 P6가 연결마다 마지막 SCR·PROF를 기억했다가 다시 보낸다.

- P4에 보내기가 실패한 뒤에는 1초마다, 평소에는 10초마다 (`kCtxRefreshMs`).
- OVR는 "동작"이라 다시 보내지 않는다.
- 기기가 끊기면 기억을 지운다.

## 4. 실행 (수동, Pi 부팅 자동 실행은 하지 않음)

시작 순서는 **받는 쪽 먼저**: P6 → P5 → P4 → P3 → P2 → P1. UART3는 재부팅마다 root로 한 번 켠다(`controller/bringup/uart3.sh`).

한 번에 띄우기 (저장소 루트에서):

```
RF=1 RF_WAIT=10 MODELS=<모델 폴더> controller/qnx/run_pipeline.sh <trip.bin> <out_dir> 1 <vehicle_type>
```

- `RF=1`: P6(`SER`, 기본 `/dev/ser3`)와 P5를 먼저 띄우고 P4는 `--rf`, P2는 `--motion`. `RF_WAIT`초 동안 폰·HUD를 붙일 시간을 준다.
- 속도 인자는 **1(실시간)** 을 쓴다. RF 시험에서 빠른 재생은 정책 시간(10·60·120초 유지)과 BLE 속도가 맞지 않는다.
- 결과: `<out_dir>/p1~p6.out`, `cycles.csv`, `state/`.

모델 없이 Tier를 확실히 켜 보기 (시연용): `controller/bringup/demo_phone.sh [초] [확률]` — `fake_p3`가 정해진 확률을 내고, P4는 `--rf --hud`. Tier 1은 약 10초, Tier 2는 60초, Tier 3은 120초 뒤에 켜진다. **모델 판단이 아니라 고정값**이라는 점을 시연에서 밝힌다.

## 5. 이 문서에서 정한 것

| # | 항목 | 결정 | 이유 |
|---|---|---|---|
| 1 | 빠른 재생과 MOT | P2가 실제 시계 50 ms 미만 간격의 MOTION은 건너뜀. RF 시험·시연은 속도 1 | BLE는 10 Hz 넘게 못 나른다. 실시간 재생에는 영향 없음 |
| 2 | 주행이 없을 때 | P4는 DEV_SYNC를 `RISK`·`RIDE_START`에만 보낸다 → 주행이 없으면 30초 뒤 P5가 안전 OFF. 그대로 둔다 | 주행 중이 아니면 개입할 근거(위험 확률)가 없다. 주행이 시작되면 DEV_SYNC가 와서 풀린다 |
| 3 | P5 재시작 | P4의 DEV_SYNC·TIER_CMD는 실패 시 한 번 더 | `RF_MESSAGES.md` §6-1과 같은 규칙 |
| 4 | OVR의 시각 | 마지막 `RISK`의 주행 시각 t | 정책 시계가 주행 시각이라 맞춘다. 30초 빠른 끄기 판정이 최대 10초 어긋날 수 있다 |
| 5 | EV 경험·차량 유형 | RF로 온 폰은 NaN (SPEC T-17) | M06에 필드가 없다. 추가하려면 프로토콜부터 |
| 6 | MOT 부호 | ax + 앞 가속, ay + 왼쪽 가속 (임시) | 시뮬레이터 좌표계(이혁진)와 앱 그리기 방향을 끝에서 끝까지 확인해야 확정 |
| 7 | 안전 OFF 뒤 어긋남 | P4가 30초 넘게 멈췄다 **재시작 없이** 살아나면 P4는 켜져 있다고 믿고 P5는 꺼 둔 상태. 다음 ON/OFF 변화 때 맞춰진다. 이번엔 고치지 않음 | P4가 죽었다 다시 뜨는 흔한 경우는 문제없다 (상태가 처음부터) |

## 6. 시험

**맥** (`ctest`, 8개): 기존 7개 + `pipeline_rf_sim` — 진짜 P1·P2·P4·P5·P6 코드 + `fake_p3` + 가상 UART로 폰 등록 → Tier 3 → MOT → OVR → INT off → 프로필 저장까지.

**Pi 검증 항목** (진짜 P1~P6 + 실제 ESP32, 폰 역할은 맥 `bridge/tools/ble_check.py`):

| # | 확인 | 보는 곳 | 결과 |
|---|---|---|---|
| V1 | 폰 HELLO → P4 `REG phone <ID> on`, P3 추론에 그 ID (`--phone` 없이) | `p4.out`, `cycles.csv` | |
| V2 | P4 `CMD` 한 줄마다 폰에 같은 INT (빠짐·순서 바뀜 0) | `p4.out` ↔ 맥 수신 | |
| V3 | Tier 3 동안 MOT 10 Hz(실제 시계), `mot_fail` 0 | 맥, `p2.out` | |
| V4 | 폰 SCR off → Tier 3 게이트 닫힘 → INT tier 3 off | `p4.out`, 맥 | |
| V5 | 폰 OVR off → INT off, `state/tolerance/<ID>.json` 변경, 5분 쿨다운 | `state/` | |
| V6 | 폰 PROF → `state/profiles/<ID>.json`, 재연결 시 PROF 회신 | `state/`, 맥 | |
| V7 | P4 종료 → 30초 뒤 P5 안전 OFF → P4 다시 → 복구 | `p5.out`, 맥 | |
| V8 | 지연: P4 `CMD` → 폰 INT 도착 (ms) | 로그 시각 | |

결과 칸은 Pi에서 돌린 뒤 채운다. **2026-11-19 현재 이 브랜치는 Pi에서 돌리지 않았다** (김승희 님의 Pi 작업본은 2026-11-17에 `demo_phone.sh`로 Tier 1·2·3과 MOT 4,200줄을 확인한 로그가 있다. 재동기와 합친 상태는 아직).

## 7. 남은 것

- MOT 부호 확정(§5-6), EV·차량 유형(§5-5), 안전 OFF 뒤 어긋남(§5-7)
- 실제 IMU(P1 LIVE, SPEC T-11): 지금 P1은 녹화 재생만 한다
- Tier 문턱·모델 성능(SPEC T-12, T-20): 연결과 별개
- HUD(Chrome Web Bluetooth) + 폰 동시 연결
