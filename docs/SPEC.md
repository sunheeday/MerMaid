# MerMaid 구현 명세 (현재 구현 기준)

- 기준일: 2026-11-11, 브랜치 `s0-protocol` (커밋 cccb945까지)
- 원본 PRD: 「MerMaid 멀미 에이전트 PRD (구현용)」(2026-11-10). 이 문서는 PRD를 대체하지 않습니다. **지금까지 구현한 범위**에서 실제로 쓰는 식, 상수, 그 출처와 PRD와 달라진 점을 적었습니다.
- 전체 구조와 단계별 입력·출력 그림은 [ARCHITECTURE.html](ARCHITECTURE.html) (브라우저로 열기).
- 다음 작업자가 할 일은 각 절의 **[TODO]** 칸과 [6장 빈칸 목록](#6-빈칸-목록-다음-작업자용)에 모았습니다. 채우면 이 문서와 해당 설정 파일을 함께 고칩니다.

## 0. 읽는 법

**상태 표시**

| 표시 | 뜻 |
|---|---|
| [구현] | 코드와 테스트가 있고 이 문서대로 동작 |
| [PRD와 다름] | PRD와 다르게 구현. 이유는 2장 |
| [미구현] | PRD에 있으나 아직 코드 없음 |
| [TODO] | 값이나 정의가 비어 있어 다음 작업자가 채울 칸 |

**출처 등급.** 아래로 갈수록 실차 데이터가 생기면 다시 맞춰야 할 가능성이 큽니다.

| 등급 | 뜻 | 실차 데이터가 생기면 |
|---|---|---|
| 표준 | ISO 같은 표준 문서나 교과서 공식 그대로 | 그대로 둠 |
| 문헌 | 논문의 식이나 값 그대로 | 대체로 둠, 조건이 다르면 재검증 |
| TU Delft 적합 | 공개 실험 데이터(ATDOMS)로 우리가 직접 계산 | 다시 맞춤 |
| PRD 설계 | PRD에 적힌 설계값 (PRD가 근거 문헌을 달지 않음) | 팀 판단으로 조정 |
| 자체 결정 | 구현하면서 정한 값. **[추측]**은 근거 없이 정한 수치 | 다시 맞춤 |

**값이 있는 곳.** 코드는 아래 파일에서 값을 읽습니다. 값을 바꿀 때는 코드가 아니라 이 파일을 고칩니다.

| 파일 | 내용 |
|---|---|
| `protocol/constants.json` | Oman, Wf, 이벤트 기준, 휴식, MSSQ, 기기 ID |
| `protocol/feature_order.json` | 피처 77개 순서, 그룹, 단위, 창, 단조, enabled, 그룹 드롭아웃 |
| `protocol/trip_format.json` | 100 Hz 주행 파일(.bin) 열 정의 |
| `ml/simulator/sim_config.json`, `sim_effects.json` | 시뮬레이터 수치 (`guess: true` = 추측) |
| `ml/train/common.py`, `ml/train/train.py` | LightGBM 파라미터, 문턱 기준 |
| `controller/config/policy.json`, `thresholds.json` | 정책 상수, Tier 문턱 |

## 1. 단계별 상태

PRD §10.2 작업 순서 기준입니다. 트랙 B(브리지, HUD, 앱)는 이 브랜치에서 손대지 않았습니다.

| 단계 | 내용 | 상태 | 합격 기준 결과 |
|---|---|---|---|
| S0 | protocol/ 계약 (스키마, UUID, feature_order 77개, policy/thresholds) | [구현] | `protocol/check.py` 통과 |
| S1 | `ml/oman.py` + TU Delft 재현 | [구현] | 필터 없이 MAE 0.97 (기준 0.97 ± 0.05). 필터 넣고 K0 = 51.4 |
| S2 | P2 피처 | [구현] [PRD와 다름] | PRD는 Python 기준 구현이었으나 C++ 하나(`controller/common/features.cpp`)로 만들고 Python은 `featurize` 도구를 부름. 단위 테스트 통과 |
| S3 | 시뮬레이터 + 데이터셋 | [구현] | 탑승 1,920회, 286,788행, 미래 5분 MISC ≥ 4 비율 20.9% (실차 보정 후, T-23) |
| S4 | 학습, 문턱, 내보내기 | [구현] [PRD와 다름] | AC-03/04/05 리포트 (`<모델 버전>/train/report.md`, 3.12). 문턱 고르는 방식 바뀜 |
| S5 | C++ 피처 + 정책 | [구현] | 골든 피처 테스트, AC-09 정책 테스트 통과 |
| S6 | Pi: P1 재생, P2, P3, P4 (BLE 없음) | [구현] | AC-01, AC-02, Q9 통과 (5장) |
| S7 | ESP32 BLE 브리지 | [미구현] | – |
| S8 | HUD | [미구현] | – |
| S9 | Android 앱 | [미구현] | – |
| S10 | P5, P6 + 실제 연결 (AC-06/07/08) | [부분] P5·P6·ESP32 브리지 구현 (PR #4), P4·P2 연결 (2026-11-18). Mac `pipeline_rf_sim`, Pi P1~P6 + 브리지 흉내(가상 터미널) 통과. 실제 ESP32·폰·HUD 연결 미확인 | `docs/RF_MESSAGES.md`, `controller/bringup/README.md` |
| S11 | 데모 시나리오 | [미구현] | – |

P1은 **재생 모드만** 있습니다 (`data/trips/*.bin`을 100 Hz로 흘림). 실제 IMU(MPU-6050)를 읽는 LIVE 모드는 없습니다 ([6장](#6-빈칸-목록-다음-작업자용) T-11).

## 2. PRD에서 바뀐 점

| # | 항목 | PRD | 현재 구현 | 이유 | 파일 |
|---|---|---|---|---|---|
| C1 | Oman 기준 이득 K0 | 48.7 (§2.1, §11 Q6) | **51.4** | Oman 입력 앞에 0.63 Hz 2차 저역통과를 넣고 같은 방식으로 다시 맞춤. 필터 없이 돌리면 48.7이 그대로 나옴. PRD Q6 끝에 "계산상 약 51 [추측]"이라고 적힌 것을 확정 | `protocol/constants.json`, `ml/verify/oman_tudelft.py` |
| C2 | Oman 이산화 | 오일러 (α = Δt/β) | 정확한 지수: α = e^(−Δt/β) | 표준 방식. 100 Hz에서는 차이가 거의 없음 | `ml/oman.py`, `controller/common/filters.hpp` |
| C3 | 라벨의 Oman 입력 | 주행의 ax, ay 그대로 (§4.1) | 중력·진동을 뺀 **운동학 수평 가속도**에 0.63 Hz 필터 | 정답이 센서 잡음과 IMU 진동 크기에 끌려가지 않게. 차량 피처는 IMU 값을 쓰므로 두 입력이 다름 | `ml/simulator/sim.py` |
| C4 | 사람별 민감도 | K = K0 × sens (sens는 MSSQ에서) | K = K0 × **true_sens** × m, true_sens = sens × e^N(0, 0.65²) | MSSQ만으로 사람 차이가 다 설명되면 모델이 MSSQ 식을 외우기만 함. 0.65는 TU Delft에서 MSSQ로 설명되지 않고 남은 퍼짐 | `sim_config.json` passenger |
| C5 | 라벨 잡음 | 10초마다 N(0, 0.5) | N(0, 0.5)를 **1분 동안 같은 값**으로 | 10초마다 따로 더하면 "앞으로 5분 최댓값"이 잡음 때문에 부풀려짐 | `sim_config.json` label |
| C6 | 라벨 y_k 시간 범위 | 앞으로 5분 최대 MISC ≥ k | 다음 10초 행부터 300초 안(현재 행 제외). 미래 5분이 다 있는 행만 학습 | "미리 예측" 뜻을 지키기 위해 | `ml/simulator/build_dataset.py` |
| C7 | IMU z축 | az = 잡음만 (§4.1 의사코드) | 중력 포함 (정지 시 +9.81) | 실제 MPU-6050과 같은 규약. 재생 파일이 실차 녹화와 같은 형식이 되도록 | `protocol/trip_format.json` |
| C8 | Tier 문턱 고르는 기준 | 행 단위: 재현율 ≥ 0.90 / 정밀도 ≥ 0.70 / ≥ 0.80 (§4.2) | **주행 재생**: 탐지율 ≥ 85% 중 가장 높은 문턱 / 시간당 오경보 ≤ 0.5 / ≤ 0.25 중 가장 낮은 문턱 | 양성 행 대부분이 이미 멀미가 온 뒤라, 행 단위 규칙은 Tier 1 문턱을 0.99로 올렸고 미리 켜진 탑승이 1%였음 | `ml/train/train.py` |
| C9 | 문턱 값 | 0.62 / 0.24 / 0.34 (§P4, 팀 사전 실험) | **0.53 / 0.51 / 0.59** (T-24 학습 설정 전 0.54 / 0.46 / 0.53, T-23 보정 전 0.63 / 0.65 / 0.77) | C8의 결과 | `controller/config/thresholds.json` |
| C10 | 문턱 고를 때 쓰는 데이터 | 명시 없음 | 학습 쪽 사람의 15%를 떼어 조기 종료와 문턱 선택에 씀 (최종 모델도 같음) | 시험 fold 누수 방지 | `ml/train/train.py` |
| C11 | 탐지 정의 | "미리 켜짐" | MISC가 k에 **처음 닿은 10초 주기나 그 전**에 ON | 같은 주기를 포함할지 PRD가 정하지 않음 | `ml/train/policy.py` |
| C12 | 빠른 끄기 3번 | "3번 끄면 비활성화" (AC-09) | 한 주행에서 같은 기기를 **켜진 뒤 30초 안에** 끈 횟수가 3이면 그 주행 동안 꺼 둠 | 그냥 끈 횟수인지 빠른 끄기 횟수인지 PRD가 모호. **[TODO] 팀 확인** | `controller/common/policy.cpp`, `POLICY.md` |
| C13 | 정책 시각 | 명시 없음 | 벽시계가 아닌 **주행 시각**(샘플 번호 / 100) | 재생 속도를 바꿔도 결과가 같게 | `controller/qnx/p4_policy.cpp` |
| C14 | 주행 시작·끝 전달 | 명시 없음 | P1 → P2 → P3 → P4로 RIDE_START / RIDE_END 메시지를 차례로 넘김 | P2 창 초기화와 P4 주행별 상태 초기화를 같은 시점에 | `controller/qnx/messages.hpp` |
| C15 | 프로필 전달 | P3가 프로필을 앎 (§P3) | P3가 매 주기 P4에 PROFILES를 요청 (P4가 프로필과 허용도의 주인) | 민감도와 허용도가 P4에서 바뀌므로 | `controller/qnx/p3_infer.cpp` |
| C16 | 프로필 파일 | `profiles/<ID>.json`에 설문 MSSQ, 설문 민감도, 현재 민감도, EV 경험, 차량 유형 (Q7) | mssq, sens_mssq, sens만 저장. **EV 경험, 차량 유형 없음** | P6(폰 연결) 전이라 받을 곳이 없음. **[TODO]** | `controller/common/store.cpp` |
| C17 | 폰이 없을 때 | 기본 프로필 MSSQ 12.90, 민감도 1.0 (§P3, Q5). EV 경험은 정하지 않음 | PRD대로 MSSQ 12.90, 민감도 1.0 (`constants.json` default_profile). **EV 경험은 NaN**(모름), 차량 유형은 그대로 | EV 경험을 0으로 두면 "EV를 안 타 봤다"고 단정하는 셈이라 모름으로 둠. 자체 결정 (2026-11-11 수정, 그 전에는 G10 전체가 NaN이었음) | `controller/qnx/p3_infer.cpp` |
| C18 | G1 svc_conflict, G8 허용도 | 피처로 사용 | v1에서 `enabled=false` (항상 NaN) | SVC 식이 정의되지 않음. 허용도는 실제 사용 기록이 있어야 의미가 있어 합성 데이터로 학습 불가 | `protocol/feature_order.json` |
| C19 | 설정 JSON 숫자 읽기 | – | `json_min.hpp`가 직접 파싱 | QNX `strtod`가 일부 소수를 1~2 ULP 다르게 읽어 Mac과 Pi 결과가 달라짐 | `controller/common/json_min.hpp` |
| C20 | P2 기준 구현 언어 | Python (`ml/features`) | C++ 하나, Python은 `featurize` 실행 파일을 부름 | 학습과 보드가 같은 코드를 써서 피처 불일치를 없앰 | `controller/common/features.cpp` |

## 3. 공식과 유도

### 3.1 Oman 멀미 모델 [구현]

쓰는 곳은 두 군데입니다. 차량 피처(P2의 `oman_score`, `oman_delta_2m`, `sensitization`)와 시뮬레이터의 정답 MISC입니다.

**식.** 매 샘플(Δt = 0.01 s)마다 S를 먼저 갱신하고, 새 S로 F를 갱신합니다.

```
ã_x, ã_y = LP_0.63Hz(a_x), LP_0.63Hz(a_y)       # 축마다 2차 버터워스
c        = sqrt(ã_x² + ã_y²)                     # 정류한 수평 크기
S        = LP2(c,         β_s = 502.4 s)          # 느린 경로 = 민감화
F        = LP2(K · S · c, β_f = 66.2 s)           # 빠른 경로
score    = (F + S)^0.4                            # MISC 척도로 봄
LP2(u, β): 1/(βs+1)²를 1차 두 번으로. y ← α·y + (1−α)·u, α = e^(−Δt/β)
```

| 기호 | 값 | 출처 |
|---|---|---|
| 식 구조, β_s = 502.4 s, β_f = 66.2 s, 지수 0.4 | – | 문헌 (Irmak 등 2022 집단 적합값) |
| 입력을 주파수 가중하지 않음 | – | 문헌 (Donohew & Griffin 2004, Irmak 2021 재인용) |
| 정류 입력 c (진폭값 아님) | – | 문헌 (Irmak 2022), PRD Q6. 진폭값을 쓰면 TU Delft MAE 2.60 |
| 0.63 Hz 2차 저역통과 | f2 = 0.63 Hz | 자체 결정. ISO Wf의 f2를 빌려 옴 |
| K0 | 51.4 | TU Delft 적합 |
| 차량 피처의 K | K0 | PRD §P2 (승객 민감도는 G10으로만 들어감) |
| 라벨의 K | K0 × true_sens × m | PRD §4.1 + C4 |

**K0를 구한 방법** (`ml/verify/oman_tudelft.py`):
1. TU Delft ATDOMS 데이터 17명의 자극(0.3 Hz 사인파, C1~C4 = 1, 1.5, 2, 2.5 m/s², 10초 램프)을 100 Hz로 만들고 위 식에 넣음.
2. 사람마다 K 하나만 바꿔 1분 MISC 기록과의 평균 절대오차가 가장 작은 K를 찾음 (시간상수는 고정).
3. 17명 K의 중앙값이 K0. 필터 없이 48.7 (MAE 0.97, 논문 1.01~1.03), 필터를 넣고 51.4.
4. 논문의 70.8은 시간상수까지 사람마다 맞춘 값이라 비교 대상이 아님 (공통 K = 70.8이면 MAE 1.58).

**0.63 Hz 필터를 넣은 이유.** 실제 IMU에는 노면 진동과 센서 잡음이 있습니다. 그대로 크기(절댓값)를 구하면 진동이 양수 치우침으로 남아 점수를 올립니다. TU Delft 자극은 깨끗한 사인파라 이 문제가 없었습니다.

**좌우 방향 확인** (`ml/verify/slalom_check.py`, 집단 수준): Irmak, Pool, Happee (2021) 슬라롬 실차 조건(0.2 Hz, 최대 0.4 g)으로 TU Delft 17명의 K를 모의실험. MISC 상승 속도 중앙값 분당 0.41 (실제 실내 시야 0.59), 평균 MISC 4.8 (4.2), 중단 비율 76% (66%). 크기는 맞지만 정밀하지 않고, 바깥을 보는 조건(실제 0.10, 2.3, 33%)은 모델에 시야 항이 없어 설명하지 못합니다.

**[TODO]** 실차 IMU로 0.63 Hz 필터 유무와 K0 재적합 (T-05). 시야 항(바깥을 봄, 화면을 봄)은 모델에 없음 (T-15).

### 3.2 ISO 2631-1 Wf 필터 [구현]

차량 피처(RMS, jerk, MSDV, 휴식 판정)에만 쓰고 Oman 입력에는 쓰지 않습니다.

```
Hh = s² / (s² + ω1 s/Q1 + ω1²)              f1 = 0.08 Hz, Q1 = 1/√2   고역: 중력, 기울기 제거
Hl = ω2² / (s² + ω2 s/Q1 + ω2²)             f2 = 0.63 Hz              저역
Ht = ω4² / (s² + ω4 s/Q4 + ω4²)             f4 = 0.25 Hz, Q4 = 0.86   가속도-속도 전환 (f3 = ∞)
Hs = (s² + ω5 s/Q5 + ω5²)/(s² + ω6 s/Q6 + ω6²)  f5 = 0.0625, Q5 = 0.80, f6 = 0.1, Q6 = 0.80
Wf = Hh · Hl · Ht · Hs,   ω = 2πf
```

- 계수: 표준 (ISO 2631-1:1997 부속서 A). PRD §P2는 "0.1~0.5 Hz"라고만 적었고 계수는 ISO 표에서 가져옴.
- 이산화: 구간마다 쌍선형 변환 s = 2fs(1 − z⁻¹)/(1 + z⁻¹), fs = 100 Hz, 사전 왜곡 없음, Hh → Hl → Ht → Hs 순서. 기법은 표준, 선택은 자체 결정. 1 Hz에서 아날로그 대비 0.12% 오차, ISO 표 값과 3% 안 (`ml/tests/test_wf.py`).
- 초기 상태: 주행 첫 샘플의 정상상태 (입력이 그 전부터 계속 그 값이었다고 봄). 0에서 시작하면 중력 9.81이 계단 입력이 되어 첫 1분 rms_z가 1 m/s² 안팎으로 튀었음 (T-22에서 고침). Oman 입력의 0.63 Hz 저역통과(P2 쪽)도 같음. 자체 결정. `controller/tests/test_features.cpp` test_start_has_no_transient
- 적용 축: x, y, z 모두. PRD §P2 ("각 가속도 축에"). **ISO의 멀미 가중은 수직(z)용**이라 수평 적용은 근거가 약함 (T-06).

### 3.3 MSDV [구현]

```
h        = sqrt(a_w,x² + a_w,y²)                 # Wf 거친 수평 크기
msdv(t)  = sqrt( Σ_{주행 시작~t} h² · Δt )        # m/s^1.5
msdv_inc_5m = msdv(t) − msdv(t − 300 s)          # 30주기 전 값과의 차
```

식은 표준 (ISO 2631-1). 수평 h를 쓰는 것은 PRD §P2 [추측] (PRD도 "논문은 축을 명시하지 않음"). ISO MSDV는 z축입니다. `msdv_inc_5m`은 PRD §11 피처 권장안의 항목, 계산 방식은 자체 결정.

### 3.4 차량 피처 계산 (P2) [구현]

전체 정의는 `controller/common/FEATURES.md`. 여기에는 식과 출처만 둡니다. 모든 피처는 100 Hz 입력으로 **10초(1,000샘플)마다** 계산합니다.

기호: a_w = Wf 거친 가속도, ã = 0.63 Hz 저역통과만 거친 가속도, g_z = 자이로 z축, N = 창 안의 샘플 수.

| 피처 (인덱스) | 식 | 상수 | 출처 |
|---|---|---|---|
| 창 | 10초, 1분, 5분, 주행 누적. 시작 직후에는 있는 샘플만 | 10/60/300 s | 창은 PRD §P2, 모자랄 때 처리는 자체 결정 |
| rms_x/y/z (0~8) | √(Σa_w² / N) | – | 표준 식, 항목은 PRD §11 |
| decel/accel_rms_x (9~12) | a_w,x < 0 (또는 > 0)인 샘플 제곱합 / 창 전체 N. 두 값 제곱합 = rms_x² | – | 항목 PRD §11, 분모 N은 자체 결정 |
| jerk_rms_x/y (13~16) | (a_w[i] − a_w[i−1]) / Δt 의 RMS | m/s³ | PRD §P2 의사코드 |
| yaw_rms (17~18) | g_z의 RMS. **자이로 오프셋 보정 없음** | rad/s | PRD §11. [TODO] T-04 |
| hard_accel_count (19~20) | ã_x가 1.5를 위로 넘는 순간 수. 1.25 아래로 돌아와야 다시 셈 | 1.5 m/s² | 기준값 PRD §P2 (He 등 2013의 **승차감 불편** 문턱과 같음, 멀미 문턱 아님) |
| hard_brake_count (21~22) | ã_x가 −0.75 아래로 내려가는 순간 수. −0.5 위로 돌아와야 다시 셈 | −0.75 m/s² | PRD §P2 (He 등 2013, 승차감 불편 문턱). 평범한 제동도 셈: BATON 사람 운전 중앙값 5분당 5회 |
| hard_turn_count (23~24) | \|ã_y\|가 2.0을 넘는 순간 수. 1.75 아래로 돌아와야 다시 셈 | 2.0 m/s² | PRD §11 [추측] (Nguyen 등 2019의 "극도로 불편" 횡가속도와 같음) |
| 이벤트 히스테리시스 | 기준에서 0.25 m/s² 되돌아와야 다음 이벤트 (`rearm_margin_mps2`, feature_order v3). 없을 때는 기준 근처에서 오르내리는 제동 하나를 여러 번 셌음 (comma2k19 5분당 7.0 → 5.3). v3 데이터셋에서 hard_brake_count_5m p90 9 → 8, BATON 12 → 10, comma2k19 중앙값 2 → 1. 모델 지표는 차이 ±0.01 안 (v003 → v007) | 0.25 m/s² | 자체 결정 |
| event_irregularity_5m (25) | 5분 안 모든 이벤트 간격의 변동계수 (모표준편차/평균). 이벤트 2개 미만이면 NaN | – | PRD §11 |
| pitch_mean_1m (26) | atan2(1분 평균 a_x, 1분 평균 a_z) (원 가속도). **평균 가감속이 섞임** | rad | 항목 PRD §P2, 식 자체 결정. [TODO] T-08 |
| msdv, msdv_inc_5m (27~28) | 3.3 | – | – |
| oman_score, oman_delta_2m, sensitization (29~31) | score, score − 12주기 전 score, S | – | PRD §P2 |
| svc_conflict (32) | **비활성 (항상 NaN)** | – | [TODO] T-01 |
| G2 속도 (33~39) | 평균, 표준편차, 최고, 정차 비율(속도 < 0.1 m/s 샘플 비율) | 0.1 m/s | 항목 PRD §11 [추측], 0.1은 자체 결정 |
| G3a 페달 (40~44) | 페달 RMS, 페달 변화율 RMS, 주행 모드, 크루즈 (마지막 값) | – | 항목 PRD §11 [추측], 식 자체 결정 |
| G3b 회생·배터리 (45~53) | 회생 감속 RMS, 회생 단계, 원페달, 회생 비율 = Σ회생 감속 / Σmax(−dv/dt, 0), SOC, 배터리 출력 평균·표준편차, 모터 토크 변화율 RMS | – | 항목 PRD §11 [추측], 식 자체 결정 |
| G4 경로 (54~58) | 다음 회전까지 거리·각도, 10초 앞 예측 가속도, 앞 곡률 (마지막 값) | – | PRD §11 [추측] |
| G5 공조, G6 좌석, G7 소음 (59~67) | 마지막 샘플 값 | – | PRD §11 [추측] |
| ride_time (68) | 주행 첫 샘플부터 경과 시간 | s | PRD §11 |
| reexposure, time_since_rest (69~70) | 휴식 뒤 다시 움직이면 1, 그때부터 지난 시간 | – | PRD §P1, §P2 |
| 휴식 판정 | 차속이 있으면 정지, 없으면 h < 0.05 m/s²가 **180초 연속** | 180 s, 0.05 | PRD §P1 |
| tol_display, tol_phone (71~72) | 기기 허용도 tol (3.9). **비활성 (항상 NaN)** | – | [TODO] T-02 |
| mssq, sensitivity, ev_experience, vehicle_type (73~76) | 승객 프로필 (3.5) | – | PRD §P3, §11 |

**단조 제약 (+1)**: rms_x_5m, rms_y_5m, msdv, oman_score, sensitization, regen_decel_rms_1m, regen_decel_rms_5m, ride_time, sensitivity (9개, PRD §4.2).

**[TODO]** 이벤트 기준 1.5 / −0.75 / 2.0은 승차감 연구(He 등 2013, Nguyen 등 2019)의 값과 같습니다. PRD가 이 값을 가져왔는지 원문 확인과, 멀미 예측용 기준을 실차 라벨로 다시 정하는 일이 남았습니다 (T-07).

### 3.5 승객 민감도 (MSSQ) [구현]

```
MSA = 아동기 9문항 합 × 9 / (9 − 경험 없음 문항 수)      # MSB도 같은 방식
MSSQ = MSA + MSB                                         # 0~54
z    = (MSSQ − 12.90) / 9.90
sens = clip( e^(0.3·z), 0.5, 2.0 )                       # z = 1이면 1.35배
```

| 항목 | 출처 |
|---|---|
| 채점식, 규준 평균 12.90, 표준편차 9.90 (n = 257) | 문헌 (Golding 2006), PRD Q5 |
| 계수 0.3, 범위 0.5~2.0 | PRD §P4 개인화 (paper_v2 FR-05). **근거 문헌 없음** |
| 기본 프로필 MSSQ 12.90, 민감도 1.0, EV 경험 NaN | PRD §P3, Q5. EV 경험 NaN은 자체 결정 (C17) |

TU Delft에서 한 사람씩 빼고 예측(LOPO)한 MISC MAE: K0만 1.32, K0 × sens 1.26. 17명이라 개선 폭은 참고 수준입니다. MSA/MSB 채점 코드는 아직 없습니다 (앱 S9에서 구현 예정, [미구현]). 저장소에는 MSSQ → sens 변환만 있습니다 (`ml/oman.py`의 `sensitivity`, C++ 쪽은 `constants.json`의 같은 상수 사용).

**[TODO]** 0.3의 출처를 paper_v2에서 확인, 실차 참가자로 재적합 (T-09).

### 3.6 시뮬레이터 (S3) [구현]

물리식은 표준이지만 들어가는 수치는 거의 모두 [추측]입니다. `sim_config.json`에서 `guess: true`로 표시했습니다.

**경로와 운전**

| 항목 | 식 / 값 | 출처 |
|---|---|---|
| 구간 종류와 비율 | 직선 0.3, 정차 0.2, 회전 0.15, S자 0.15, 회전교차로 0.1, 언덕 0.1, 고속도로 0.08 | 종류 PRD §4.1, 비율·길이·반경·속도 [추측]. 고속도로와 그 비율은 보정 T-23 (BATON-Sample 사람 운전, [REALDATA.md](REALDATA.md)) |
| 고속도로 | 2~8 km, 90~110 km/h, 직선과 반경 600~3,000 m 곡선(200~800 m)을 번갈아 | 보정 T-23 (BATON-Sample 사람 운전, [REALDATA.md](REALDATA.md)). 1분 평균 80 km/h 이상 비율 22% (실차 25%) |
| 주행 길이 | 15~40분 | PRD §4.1 [추측] |
| 목표 속도 | 구간 속도 × 운전자 성향 f (0.9~1.15) × (1 + e), e는 300 m 상관 AR(1), 표준편차 0.05 | 보정 T-23 (BATON-Sample 사람 운전, [REALDATA.md](REALDATA.md)) (전 0.08, 200 m) |
| 곡률 제한 속도 | v ≤ √(a_lat,max / κ), a_lat,max = 1.5~3.5 m/s² | 식 표준, 값 [추측] |
| 속도 프로파일 | 앞으로 v² ≤ v_prev² + 2·a_acc·ds, 뒤로 v² ≤ v_next² + 2·a_dec·ds (가속 0.7~2.0, 감속 0.5~1.6 m/s², 회생 단계마다 감속 +0.2) | 식 표준 (등가속도), 값 보정 T-23 (BATON-Sample 사람 운전, [REALDATA.md](REALDATA.md)) (전 1.0~2.5) |
| 저크 제한 | 속도에 2차 영위상 저역통과 0.3 Hz | 보정 T-23 (BATON-Sample 사람 운전, [REALDATA.md](REALDATA.md)) (전 0.5 Hz). PRD는 "jerk 한계"만 |
| 정차 | 5~120 s 대기 | 보정 T-23 (BATON-Sample 사람 운전, [REALDATA.md](REALDATA.md)) (전 5~45 s, 정차 비율 꼬리) |
| 노면 굴곡 | 경사에 거리 60 m 상관 AR(1) 잡음, 표준편차 0.005 | 보정 T-23 (BATON-Sample 사람 운전, [REALDATA.md](REALDATA.md)) (rms_z_1m) |
| 차로 유지 흔들림 | 0.05~0.5 Hz 대역 잡음, 크기 0.006·v m/s², a_lat과 yaw(= a/v)에 더함. 라벨 입력에도 들어감 | 보정 T-23 (BATON-Sample 사람 운전, [REALDATA.md](REALDATA.md)) (rms_y_1m) |
| 회생 단계 최대 감속 | 0~3단계 0.3 / 0.8 / 1.3 / 2.0 m/s², 3단계 = 원페달 | [추측] |
| 전기차 비율 | 0.6 | [추측] |

**IMU와 차량 신호**

| 항목 | 식 / 값 | 출처 |
|---|---|---|
| 가속도 | a_x = dv/dt + g·sin θ + 진동 + 잡음, a_y = v²κ + 흔들림 + 진동 + 잡음, a_z = g·cos θ + v²·dθ/ds + 진동 + 잡음 (θ = atan(경사)) | 식 표준, z 중력 포함은 C7 |
| 자이로 | g_x = 잡음, g_y = dθ/dt + 잡음, g_z = v·κ + 흔들림/v + 잡음 | 식 표준 |
| 노면 진동 | 백색 잡음을 2~15 Hz로 걸러 크기 (0.02 + 0.004·v), 수평은 0.3배 | [추측] |
| 센서 잡음 | 가속도 0.05 m/s², 자이로 0.003 rad/s | 가속도 PRD §4.1 [추측], 자이로 [추측] |
| 주행저항 | m·a + m·g·C_rr + ½ρC_dA·v² (m 1,800 kg, C_rr 0.015, C_dA 0.7 m², ρ 1.2) | 식 표준, 값 [추측] |
| 페달, SOC, 배터리 | 구동/제동력을 최대(3.5, 8.0 m/s²)로 나눈 비율, 효율 0.9 / 회생 0.7, 60 kWh | [추측] |
| 실내 소음 | 45 + 0.35 × 속도(km/h) + 3 × 창문 열림 dB | [추측] |
| 실내 온도 | 시작 15~35 °C에서 설정 20~26 °C로 시상수 600 s | [추측] |

**[TODO]** 정차가 5~120초뿐이라 휴식(180초)이 한 번도 없어 `time_since_rest`는 항상 NaN, `reexposure`는 항상 0입니다 (T-03). 실차 녹화와 분포 비교 (T-14).

### 3.7 승객과 정답 라벨 [구현] [PRD와 다름: C3~C6]

```
MSSQ       ~ clip(N(12.90, 9.90²), 0, 54)
sens       = 3.5의 식                                    # 모델 입력 G10에는 이것만
true_sens  = clip(sens · e^η, 0.1, 10),  η ~ N(0, 0.65²)  # 모델이 모르는 체질
m          = m_seat · m_temp · m_fan · m_noise           # 탑승마다 범위에서 뽑음
K          = K0 · true_sens · m
c          = |LP_0.63Hz(a_long, a_lat)|                  # 운동학 가속도 (C3)
score(t)   = Oman(c; K)
MISC(t)    = clip(round(score(t) + ε_블록), 0, 10),  ε ~ N(0, 0.5²)을 60초마다 새로 (C5)
y_k(t)     = 1 if max MISC(t+10 s … t+300 s) ≥ k,  k = 2..6   (C6)
```

| 항목 | 값 | 출처 |
|---|---|---|
| 규모 | 경로 480 × 승객 4 = 탑승 1,920회, 승객 120명 | PRD §4.1 (paper_v2 TABLE XIII) |
| η 표준편차 0.65 | TU Delft 사람별 K를 MSSQ 식으로 나눈 뒤 남은 퍼짐 (사분위 범위 기반, `ml/verify/out/K_lpf.npy`) | TU Delft 적합. 항 자체는 자체 결정 |
| m_seat | 뒷자리면 U(1.1, 1.5), 앞자리 1.0 | PRD §4.1. 방향 문헌 (Schmidt 2020, Turner & Griffin 1999, Irmak 2021), 크기 [추측] |
| m_temp | 평균 실내 온도 ≤ 24 °C면 1.0, 35 °C에서 최대값 U(1.05, 1.2)까지 선형 | 방향 문헌 (Arnold 2019), 크기 [추측] |
| m_fan | 기본 풍량 ≥ 2단계면 U(0.85, 1.0) | 방향 문헌 (D'Amour 2017), 크기 [추측]. Tier 1 송풍 효과는 넣지 않음 |
| m_noise | 1.0 (효과 없음) | PRD §4.1 (근거 부족, Gu 2025) |
| ε 표준편차 | 0.5, 60초마다 새로 | 크기 PRD Q6 [추측], 1분 묶음은 자체 결정 |

결과 (데이터셋 버전의 `dataset_report.json`): 286,788행, P(≥2..6) = 0.910 / 0.555 / 0.209 / 0.059 / 0.012, K 중앙값 55.2. 보정(T-23) 전에는 0.953 / 0.711 / 0.374 / 0.146 / 0.047이었습니다. 보정 뒤 비율은 BATON-Sample 사람 운전에 같은 라벨 식을 적용한 값(0.898 / 0.530 / 0.203 / 0.064 / 0.018)과 비슷합니다.

**주의.** 라벨이 Oman 식으로 만들어지므로, 이 데이터로 잰 성능은 "구조가 의도대로 도는가"의 근거일 뿐 실제 정확도가 아닙니다 (PRD §4.1). 모델이 G1의 Oman 피처와 G10 프로필에 거의 다 기대는 것(4장)도 이 때문입니다.

**[TODO]** 환경 효과 크기(T-10), 라벨 잡음 크기, 시야 항(T-15).

### 3.8 학습 (S4) [구현]

| 항목 | 값 | 출처 |
|---|---|---|
| 구성 | 이진 분류기 5개 (k = 2~6), 각각 P(≥k) | 표준 기법 (Frank & Hall 2001), PRD §4.2 |
| 단조 보정 | 추론 뒤 p_k ← min(p_k, p_{k−1}) | PRD §P3 |
| 라이브러리 | LightGBM 4.6.0, objective binary | PRD §P3, §4.2 |
| learning_rate, num_leaves | 0.03, 7 (T-24 전 0.05, 31) | PRD §4.2 [추측] 시작값에서 T-24로 바꿈 |
| min_data_in_leaf, lambda_l2, feature_fraction, bagging_fraction | 1,000, 10, 0.9, 0.8 (매 회). T-24 전 50, 0 | 자체 결정 (T-24: 드문 k가 검증용 사람 18명에 과적합하지 않게) |
| 회수 | 최대 2,000. 100회까지는 멈추지 않고, 그 뒤 50회 개선 없으면 멈춤 (가장 좋은 회수는 100회 이상에서 고름) | 조기 종료 PRD §4.2, 숫자 자체 결정 (최소 100회는 T-24) |
| 단조 제약 | 3.4의 9개 | PRD §4.2 |
| 그룹 드롭아웃 | G2 10%, G3a 30%, G3b 50%, G4 50%, G5 30%, G6 40%, G7 30%, G8 20%, G10 10%, G1·G9 0% | PRD §4.2 (paper_v2 TABLE VI) |
| 드롭아웃 방식 | 원본 + 복사본 1벌. 복사본에서 탑승 단위로 그룹을 통째로 NaN, 칸 단위 2% 추가. G1, G9, vehicle_type은 지우지 않음 | 방식 PRD §4.2, 1벌은 자체 결정 |
| 교차검증 | 사람 단위 5-fold | PRD §4.2 |
| 검증용 사람 | 학습 쪽 사람의 15% (조기 종료 + 문턱) | 자체 결정 (C10) |
| 최종 모델 트리 수 | k = 2..6: 190, 102, 101, 100, 102. T-24 전 65, 52, 25, 4, 1 (k5·k6는 조기 종료가 거의 바로 멈춘 빈 모델) | – |

결과는 `ml/out/runs/<버전>/models/model_2~6.txt` (LightGBM 텍스트 형식, 보드도 이 파일을 그대로 읽음). 버전 규칙은 3.12.

### 3.9 Tier 문턱과 정책 재생 [구현] [PRD와 다름: C8~C11]

**문턱 고르기** (`ml/train/train.py`): 검증용 사람의 탑승을 문턱 0.05~0.95 (0.01 간격)마다 3.10 정책으로 재생하고 아래 기준으로 고릅니다.

| Tier | 기기, 입력 | 기준 | 최종 문턱 |
|---|---|---|---|
| 1 | 송풍, P(≥3) | 탐지율 ≥ 0.85인 가장 높은 문턱 | 0.63 |
| 2 | 디스플레이, P(≥4) | 시간당 오경보 ≤ 0.5인 가장 낮은 문턱 | 0.65 |
| 3 | 폰, P(≥4) | 시간당 오경보 ≤ 0.25인 가장 낮은 문턱 | 0.77 |

목표 85% / 0.5회 / 0.25회는 자체 결정입니다. **[TODO] 팀 합의** (T-12).

**재생 지표 정의** (`ml/train/policy.py`)

| 지표 | 정의 | 출처 |
|---|---|---|
| 탐지율 | MISC가 k에 처음 닿은 주기나 그 전에 ON이 된 탑승 / k에 닿은 탑승 | 이름 PRD AC-05, 같은 주기 포함은 자체 결정 |
| 중앙 리드타임 | 도달 시각 − 그 전 첫 ON 시각의 중앙값 | 이름 PRD AC-05 |
| 오경보 | 켜진 뒤 5분 안에 MISC가 k에 닿지 않은 ON, 시간당 횟수 | PRD AC-05 |
| 도달 후 2분 안 ON | 미리 켜지진 않았지만 도달 후 2분 안에 켜진 비율 | 자체 결정 (참고용) |

재생 가정: 게이트는 항상 열림, 승객 수동 조작 없음 (쿨다운, 허용도, 비활성화가 재생에 들어가지 않음). 자체 결정.

### 3.10 대응 정책 (P4) [구현]

모든 규칙과 상수는 PRD §P4의 설계값이고 근거 문헌은 없습니다. 값은 `controller/config/policy.json`. 정의 전문은 `controller/common/POLICY.md`.

```
r_d        = Tier 1·2 (공용): 등록된 폰들의 p_{i,k} 최댓값,  Tier 3: 그 폰의 p_{i,4}
th_on(d)   = base_th[tier] + tol_d
th_off(d)  = th_on(d) − off_margin
켜기       = r_d ≥ th_on이 hold 주기 연속 + 게이트 열림 + 쿨다운 아님 + 비활성 아님
끄기       = r_d < th_off가 off_hold 주기 연속, 또는 게이트 닫힘
tol_d      = 0.4 · tanh(n_d / 6),  n_d ≥ −4
```

| 항목 | 값 | 비고 |
|---|---|---|
| hold (1주기 = 10초) | Tier 1 / 2 / 3 = 1 / 6 / 12 주기 | |
| off_margin, off_hold | 0.10, 6주기 | 켰다 꺼졌다 방지 |
| 게이트 | Tier 1 없음, Tier 2 HUD 화면 ON, Tier 3 폰 화면 ON + 응시 | `--rf`면 P6의 CTX_SCREEN(M03), 아니면 `--hud`/`--phone`으로 켜짐 고정 |
| n_d | 켜진 뒤 30초 안에 승객이 끄면 +1, 꺼진 것을 직접 켜면 −1, 최소 −4 | n = −4면 tol −0.23, n = 6이면 +0.30, 최대 +0.4 |
| 쿨다운 | 승객이 끈 기기는 300초 동안 다시 켜지지 않음 | |
| 비활성화 | 한 주행에서 빠른 끄기 3번이면 그 주행 동안 꺼 둠 | C12 [TODO] |
| 민감도 갱신 | 승객이 꺼진 큐나 앰비언트를 켜면 sens × 1.15 (최대 2.0). 송풍 켜기, HUD 버튼, 끄기는 안 바꿈 | |
| 주행 끝 | sens ← sens + 0.1 × (sens_mssq − sens) | 설문값 쪽으로 10% 복귀 |
| 안전 타임아웃 | 30 s (`safety_timeout_s`) | P5가 DEV_SYNC 없이 30초면 모두 OFF (`p5_devmgr`) |

민감도는 모델 입력 G10(sensitivity)으로만 들어가고 Oman 피처의 K는 바꾸지 않습니다.

**저장** (`controller/common/store.cpp`): 바뀔 때마다 `tolerance/<기기>.json` {"n"}, `profiles/<ID>.json` {mssq, sens_mssq, sens}을 임시 파일 → fsync → rename → 디렉터리 fsync 순서로 씀. 읽기 실패 시 기본값.

### 3.11 QNX 프로세스 (S6) [구현]

```
P1 acq ──SAMPLE 100 Hz──▶ P2 feat ──VEH 10 s──▶ P3 infer ──RISK──▶ P4 policy ──▶ CMD 로그 / CSV
  └─RIDE_START/END──▶ P2 ──▶ P3 ──▶ P4            P3 ──PROFILES 요청──▶ P4 (회신: 폰, 프로필, tol)
```

| 항목 | 구현 |
|---|---|
| IPC | `name_attach` / `name_open` + `MsgSend` / `MsgReceive` / `MsgReply`. 메시지 타입 0x8001부터 (`messages.hpp`) |
| 받는 쪽 | 받자마자 복사하고 바로 회신한 뒤 처리 (100 Hz의 P1이 막히지 않게) |
| 시작 순서 | 각 프로세스는 다음 단계에 연결된 뒤 자기 이름을 붙임 (P4 → P3 → P2 → P1) |
| 시간 제한 | `TimerTimeout`으로 보내기와 회신에 제한. 샘플 기본 5 ms, 주행 시작·끝 1 s. 상대가 죽으면 1초에 한 번까지 재연결 |
| 우선순위 (SCHED_RR) | P1 40, P2 30, P3 20, P4 15. 서버 우선순위 상속으로 pidin에서는 P2~P4도 40으로 보일 수 있음 |
| P1 주기 | `clock_nanosleep` 절대 시각. QNX 1 ms 틱 때문에 깨어남이 약 1 ms 늦음 (중앙값 953 µs, v007 실시간) |
| P3 입력 | VEH 71개 + 폰마다 G8(tol, 비활성이라 NaN) + G10 = 77개. `enabled=false` 칸은 NaN. 폰이 없으면 기본 프로필 "default" 한 행 (C17) |
| 폰 수 | 최대 9대 (`kMaxPhones`, Q9) |

실행: `controller/qnx/run_pipeline.sh <trip> <out> <speed> <vtype> [phones]`. 절차는 `controller/bringup/README.md`.

### 3.12 결과물 버전 (`ml/runs.py`) [구현]

데이터셋, 모델, 실차 데이터셋은 덮어쓰지 않습니다. 만드는 스크립트가 실행마다 `ml/out/runs/vNNN/`을 새로 만들고, `run.json`에 종류, 입력 버전, git 커밋(작업 중 변경 여부 포함), 파라미터, 설명(`--note`)을 남깁니다.

| 만드는 것 | 스크립트 | 새 버전에 들어가는 것 |
|---|---|---|
| 데이터셋 | `build_dataset.py` | `dataset.npz`, `rides.json`, `dataset_report.json`, `trips/ride_*.bin` |
| 모델 | `train.py` (기본 입력: 최신 데이터셋) | `models/`, `train/report.{md,json}`, `thresholds.json` |
| 실차 데이터셋 | `build_real.py` | `real_<source>.npz`, 보고서, `trips/real/` |

- 리포트(`compare.py`, `tudelft_replay.py`, `model_parity.py`, `export_csv.py`)는 입력 버전 폴더의 하위 폴더에 씁니다. 같은 입력이면 같은 내용이라 다시 만들어도 잃는 정보가 없습니다.
- 배포할 모델은 `python3 ml/runs.py use vNNN`으로만 정합니다. `ml/out/runs/CURRENT`를 바꾸고 그 버전의 문턱을 `controller/config/thresholds.json`에 복사합니다 (`"run"` 칸). 이 파일은 git으로 이력을 남깁니다.
- 읽는 쪽 기본값: 모델은 CURRENT, 데이터셋은 그 모델이 학습한 것, 실차 데이터셋은 그 파일이 있는 최신 버전. `run_pipeline.sh`도 CURRENT 모델을 씁니다.
- 예외: S1 Oman 적합 결과(`ml/verify/out/`)는 원자료(ATDOMS)에서 결정적으로 나오는 값이라 그대로 둡니다.
- `v001`은 채택하지 않은 피처 v2 실험(T-24)을 보관한 것입니다. 코드는 `code.patch`에 있습니다.
- 피처 v1: 데이터셋 v002, 모델 v003, 실차 v004(BATON)·v005(comma2k19). 피처 v3(이벤트 히스테리시스): 데이터셋 v006, 모델 v007(배포), 실차 v008·v009.

## 4. 피처 77개 현황

"빈 비율"은 학습 데이터(286,788행)에서 NaN 비율, "모델 기여"는 최종 모델의 분할 이득(gain) 비율입니다. 단조 +1은 3.4.

| # | 이름 | 그룹 | 단위 | 창 | 단조 | 사용 | 빈 비율 | 모델 기여 P(≥3) / P(≥4) |
|---|---|---|---|---|---|---|---|---|
| 0 | `rms_x_10s` | G1 | m/s^2 | 10s |  | 켬 | 0 | 0.0% / 0.0% |
| 1 | `rms_x_1m` | G1 | m/s^2 | 1m |  | 켬 | 0 | 0.0% / 0.0% |
| 2 | `rms_x_5m` | G1 | m/s^2 | 5m | +1 | 켬 | 0 | 0.0% / 0.0% |
| 3 | `rms_y_10s` | G1 | m/s^2 | 10s |  | 켬 | 0 | 0.0% / 0.0% |
| 4 | `rms_y_1m` | G1 | m/s^2 | 1m |  | 켬 | 0 | 0.0% / 0.0% |
| 5 | `rms_y_5m` | G1 | m/s^2 | 5m | +1 | 켬 | 0 | 0.1% / 0.0% |
| 6 | `rms_z_10s` | G1 | m/s^2 | 10s |  | 켬 | 0 | 0.0% / 0.0% |
| 7 | `rms_z_1m` | G1 | m/s^2 | 1m |  | 켬 | 0 | 0.0% / 0.0% |
| 8 | `rms_z_5m` | G1 | m/s^2 | 5m |  | 켬 | 0 | 0.0% / 0.0% |
| 9 | `decel_rms_x_1m` | G1 | m/s^2 | 1m |  | 켬 | 0 | 0.0% / 0.0% |
| 10 | `decel_rms_x_5m` | G1 | m/s^2 | 5m |  | 켬 | 0 | 0.0% / 0.1% |
| 11 | `accel_rms_x_1m` | G1 | m/s^2 | 1m |  | 켬 | 0 | 0.0% / 0.0% |
| 12 | `accel_rms_x_5m` | G1 | m/s^2 | 5m |  | 켬 | 0 | 0.1% / 0.1% |
| 13 | `jerk_rms_x_10s` | G1 | m/s^3 | 10s |  | 켬 | 0 | 0.0% / 0.0% |
| 14 | `jerk_rms_x_1m` | G1 | m/s^3 | 1m |  | 켬 | 0 | 0.0% / 0.0% |
| 15 | `jerk_rms_y_10s` | G1 | m/s^3 | 10s |  | 켬 | 0 | 0.2% / 0.0% |
| 16 | `jerk_rms_y_1m` | G1 | m/s^3 | 1m |  | 켬 | 0 | 0.0% / 0.0% |
| 17 | `yaw_rms_10s` | G1 | rad/s | 10s |  | 켬 | 0 | 0.1% / 0.0% |
| 18 | `yaw_rms_1m` | G1 | rad/s | 1m |  | 켬 | 0 | 0.0% / 0.0% |
| 19 | `hard_accel_count_1m` | G1 | count | 1m |  | 켬 | 0 | 0.0% / 0.0% |
| 20 | `hard_accel_count_5m` | G1 | count | 5m |  | 켬 | 0 | 0.0% / 0.1% |
| 21 | `hard_brake_count_1m` | G1 | count | 1m |  | 켬 | 0 | 0.0% / 0.0% |
| 22 | `hard_brake_count_5m` | G1 | count | 5m |  | 켬 | 0 | 0.0% / 0.0% |
| 23 | `hard_turn_count_1m` | G1 | count | 1m |  | 켬 | 0 | 0.0% / 0.0% |
| 24 | `hard_turn_count_5m` | G1 | count | 5m |  | 켬 | 0 | 0.0% / 0.0% |
| 25 | `event_irregularity_5m` | G1 | 1 | 5m |  | 켬 | 7.5% | 0.0% / 0.0% |
| 26 | `pitch_mean_1m` | G1 | rad | 1m |  | 켬 | 0 | 0.0% / 0.0% |
| 27 | `msdv` | G1 | m/s^1.5 | ride | +1 | 켬 | 0 | 1.8% / 2.3% |
| 28 | `msdv_inc_5m` | G1 | m/s^1.5 | 5m |  | 켬 | 0 | 0.1% / 0.0% |
| 29 | `oman_score` | G1 | MISC | ride | +1 | 켬 | 0 | 64.2% / 46.8% |
| 30 | `oman_delta_2m` | G1 | MISC | 2m |  | 켬 | 0 | 0.0% / 0.0% |
| 31 | `sensitization` | G1 | 1 | ride | +1 | 켬 | 0 | 7.3% / 6.8% |
| 32 | `svc_conflict` | G1 | 1 | ride |  | 끔 | 100% | 0.0% / 0.0% |
| 33 | `speed_mean_10s` | G2 | m/s | 10s |  | 켬 | 0 | 0.0% / 0.0% |
| 34 | `speed_mean_1m` | G2 | m/s | 1m |  | 켬 | 0 | 0.0% / 0.0% |
| 35 | `speed_mean_5m` | G2 | m/s | 5m |  | 켬 | 0 | 0.0% / 0.0% |
| 36 | `speed_std_1m` | G2 | m/s | 1m |  | 켬 | 0 | 0.0% / 0.0% |
| 37 | `speed_std_5m` | G2 | m/s | 5m |  | 켬 | 0 | 0.0% / 0.0% |
| 38 | `stop_ratio_5m` | G2 | 1 | 5m |  | 켬 | 0 | 0.0% / 0.0% |
| 39 | `speed_max_5m` | G2 | m/s | 5m |  | 켬 | 0 | 0.6% / 0.3% |
| 40 | `accel_pedal_rms_1m` | G3a | 1 | 1m |  | 켬 | 0 | 0.0% / 0.0% |
| 41 | `brake_pedal_rms_1m` | G3a | 1 | 1m |  | 켬 | 0 | 0.0% / 0.0% |
| 42 | `pedal_rate_rms_1m` | G3a | 1/s | 1m |  | 켬 | 0 | 0.0% / 0.1% |
| 43 | `drive_mode` | G3a | code | now |  | 켬 | 0 | 0.1% / 0.1% |
| 44 | `cruise_on` | G3a | bool | now |  | 켬 | 0 | 0.0% / 0.0% |
| 45 | `regen_decel_rms_1m` | G3b | m/s^2 | 1m | +1 | 켬 | 40.5% | 0.0% / 0.0% |
| 46 | `regen_decel_rms_5m` | G3b | m/s^2 | 5m | +1 | 켬 | 40.5% | 0.0% / 0.0% |
| 47 | `regen_level` | G3b | code | now |  | 켬 | 40.5% | 0.0% / 0.2% |
| 48 | `one_pedal` | G3b | bool | now |  | 켬 | 40.5% | 0.0% / 0.0% |
| 49 | `regen_share_5m` | G3b | 1 | 5m |  | 켬 | 40.9% | 0.0% / 0.0% |
| 50 | `soc` | G3b | 1 | now |  | 켬 | 40.5% | 0.2% / 0.9% |
| 51 | `batt_power_mean_1m` | G3b | kW | 1m |  | 켬 | 40.5% | 0.0% / 0.0% |
| 52 | `batt_power_std_1m` | G3b | kW | 1m |  | 켬 | 40.5% | 0.0% / 0.0% |
| 53 | `motor_torque_rate_rms_1m` | G3b | Nm/s | 1m |  | 켬 | 40.5% | 0.0% / 0.0% |
| 54 | `dist_next_turn` | G4 | m | ahead |  | 켬 | 7.5% | 0.0% / 0.0% |
| 55 | `angle_next_turn` | G4 | rad | ahead |  | 켬 | 7.5% | 0.0% / 0.1% |
| 56 | `pred_ax_10s` | G4 | m/s^2 | ahead |  | 켬 | 0 | 0.0% / 0.0% |
| 57 | `pred_ay_10s` | G4 | m/s^2 | ahead |  | 켬 | 0 | 0.0% / 0.0% |
| 58 | `curvature_ahead` | G4 | 1/m | ahead |  | 켬 | 0 | 0.0% / 0.0% |
| 59 | `cabin_temp` | G5 | degC | now |  | 켬 | 0 | 0.1% / 0.4% |
| 60 | `set_temp` | G5 | degC | now |  | 켬 | 0 | 0.3% / 0.7% |
| 61 | `fan_level` | G5 | code | now |  | 켬 | 0 | 0.2% / 0.3% |
| 62 | `ac_on` | G5 | bool | now |  | 켬 | 0 | 0.0% / 0.0% |
| 63 | `seat_row` | G6 | code | now |  | 켬 | 0 | 1.8% / 0.7% |
| 64 | `seatback_angle` | G6 | deg | now |  | 켬 | 0 | 0.6% / 0.6% |
| 65 | `window_open` | G6 | bool | now |  | 켬 | 0 | 0.0% / 0.0% |
| 66 | `noise_level` | G7 | dB | now |  | 켬 | 0 | 0.0% / 0.0% |
| 67 | `noise_lowfreq_level` | G7 | dB | now |  | 켬 | 0 | 0.0% / 0.0% |
| 68 | `ride_time` | G9 | s | ride | +1 | 켬 | 0 | 0.1% / 0.0% |
| 69 | `reexposure` | G9 | bool | ride |  | 켬 | 0 | 0.0% / 0.0% |
| 70 | `time_since_rest` | G9 | s | ride |  | 켬 | 100% | 0.0% / 0.0% |
| 71 | `tol_display` | G8 | 1 | persistent |  | 끔 | 100% | 0.0% / 0.0% |
| 72 | `tol_phone` | G8 | 1 | persistent |  | 끔 | 100% | 0.0% / 0.0% |
| 73 | `mssq` | G10 | score | persistent |  | 켬 | 0 | 14.5% / 24.9% |
| 74 | `sensitivity` | G10 | 1 | persistent | +1 | 켬 | 0 | 6.7% / 12.9% |
| 75 | `ev_experience` | G10 | bool | persistent |  | 켬 | 0 | 0.5% / 1.3% |
| 76 | `vehicle_type` | G10 | code | persistent |  | 켬 | 0 | 0.0% / 0.1% |

**비어 있는 피처 16개와 이유**

| 피처 | 이유 | 채우는 방법 |
|---|---|---|
| svc_conflict | v1에서 끔. SVC 식이 정의되지 않음 | T-01 |
| tol_display, tol_phone | v1에서 끔. 실제 사용 기록이 있어야 의미가 있음 | T-02 |
| time_since_rest (항상 NaN), reexposure (항상 0) | 시뮬레이터 정차가 5~120초라 휴식(180초)이 없음 | T-03 |
| G3b 9개 (회생, SOC, 배터리, 토크) | EV 전용 신호. 내연기관 탑승(약 40%)에는 없음. 의도된 결측 | – |
| event_irregularity_5m (7.5%) | 5분 안 이벤트 2개 미만 | 의도된 결측 |
| dist/angle_next_turn (7.5%) | 경로의 마지막 회전 뒤 (고속도로 구간이 끝에 오면 길어짐) | 의도된 결측 |

**모델이 무엇에 기대는가**
- 그룹별 이득: G1 65.2%, G10 30.5%. 나머지 그룹 합이 약 4%.
- P(≥4) 상위: oman_score 47%, mssq 25%, sensitivity 13%, sensitization 7%.
- 두 모델 모두에서 이득이 0인 피처가 26개.
- **의심 신호**: `ev_experience`(1.3%)와 `seatback_angle`(0.6%)은 시뮬레이터 라벨에 아무 효과가 없는데 모델이 씀. 우연히 맞은 분할일 가능성 (T-13).
- 라벨이 Oman 식이라 당연한 결과입니다. 다른 피처의 가치는 실차 라벨에서만 확인할 수 있습니다.

## 5. 검증 결과

| 기준 | 내용 | 결과 | 합격선 | 근거 파일 |
|---|---|---|---|---|
| S1 | TU Delft Oman 재현 (필터 없음) | MAE 0.97, K0 48.7 | 0.97 ± 0.05 | `ml/verify/out/` |
| S1 | 필터 넣고 K0 | 51.4 | – | 같음 |
| S1 | LOPO MAE | K0만 1.32, K0 × sens 1.26 | – | 같음 |
| AC-01 | Python 대비 QNX 확률 차이 (4,202행: NaN, 0, 분기 기준값 ±1e-9 포함) | 최대 2.2e-16, Tier 결정 100% 일치 (모델 v007, Pi 2026-11-17) | < 1e-6, 100% | `ml/out/runs/v007/parity/result.json` |
| AC-02 | P1 100 Hz 샘플 손실 | 186,518 → 186,518, 빠짐 0, 전송 실패 0 (실시간, 모델 v007) | 0 | `controller/bringup/README.md` |
| AC-02 | 10초 주기 P2 벡터 → P4 정책 끝 | 실시간 중앙값 0.57 ms, 최대 2.03 ms (빠른 재생 0.62 / 0.76 ms) | 10 s 안 | 같음 |
| AC-02 | Pi 파이프라인 vs Mac featurize + Python 추론 + 재생 | 확률 차이 1.1e-16, ON/OFF 불일치 0 (186주기 × 4기기, 모델 v007, Pi 2026-11-17) | – | `ml/verify/pipeline_check.py` |
| AC-03 | 사람 단위 5-fold PR-AUC (모델 / Oman만) | k3 0.860 / 0.841, k4 0.496 / 0.467, k5 0.210 / 0.202, k6 0.056 / 0.086 (모델 v007, 피처 v3). 보정 전(0.941, 0.710, 0.391, 0.200)보다 낮은 것은 주로 양성 비율이 낮아졌기 때문(k4 0.37 → 0.21). ROC-AUC는 k3 0.85, k4 0.81, k5 0.84, k6 0.88. k6는 아직 Oman만보다 낮음 (T-24) | 보고 | `ml/out/runs/v007/train/report.md` |
| AC-03 | G8 포함 / 제외 | G8이 v1에서 꺼져 있어 같은 모델 | 둘 다 보고 | 같음 |
| AC-04 | 그룹 결측 강건성 (P(≥3) / P(≥4) PR-AUC) | G2~G7 중 하나를 지워도 0.855~0.860 / 0.492~0.497. G10을 NaN으로 0.850 / 0.484, 기본 프로필(폰 없음)로 0.849 / 0.483. G1+G9로 학습한 모델 0.843 / 0.468 | G1+G9 모델 이상 | 같음 |
| AC-05 | 정책 재생 (시험 fold 평균) | Tier 1 탐지 84%, 리드 4.2분, 오경보 1.12/h. Tier 2 27%, 4.2분, 0.47/h. Tier 3 12%, 5.4분, 0.23/h | 보고 | 같음 |
| AC-09 | 개인화 4가지 경우 | 통과 | 통과 | `controller/tests/test_policy.cpp` |
| Q9 | 모델 5개 로드, 한 주기 추론 (폰 9대), 메모리 | 95 ms, 중앙값 0.85 ms (최대 0.90), 약 12.6 MB (모델 v007) | 1 s, 10 ms, 200 MB | `controller/bringup/README.md` |
| AC-06/07/08 | 다중 연결, 안전 차단, 재연결 | [미구현] (S10) | – | – |

**AC-04.** 보정(T-23) 뒤 데이터셋에서는 기본 프로필(폰 없음)도 G1+G9 모델 이상입니다 (v007: P(≥3) 0.849 대 0.843, P(≥4) 0.483 대 0.468). G10을 NaN으로 두어도 0.850 / 0.484로 기준 이상입니다 (T-24 전에는 P(≥4) 0.468로 0.004 낮았음). 보정 전에는 기본 프로필이 0.002 낮았습니다 (T-21).

**AC-05 주의.** Tier 2·3 탐지율이 낮습니다 (29%, 16%). 오경보 목표를 낮게 잡은 결과이며, 목표값 자체가 팀 합의 대상입니다 (T-12).

## 6. 빈칸 목록 (다음 작업자용)

우선순위: **높음** = 데모나 합격 기준에 영향, **중간** = 모델 품질, **낮음** = 근거 보강.

| ID | 우선 | 항목 | 지금 상태 | 해야 할 일 | 고칠 곳 |
|---|---|---|---|---|---|
| T-01 | 중간 | `svc_conflict` (감각 충돌) 정의 | 항상 NaN | SVC 식을 정의 (예: 자이로 yaw와 a_y/v의 불일치, 수직-수평 충돌 등). 시뮬레이터가 만들 수 있게 하고 `enabled=true` | `FEATURES.md`, `features.cpp`, `feature_order.json` (version 올림) |
| T-02 | 중간 | G8 허용도 피처 | 항상 NaN | 실제 사용 기록이 쌓이거나, 시뮬레이터에 승객의 끄기 행동 모델을 넣은 뒤 켬 | `feature_order.json`, `sim.py` |
| T-03 | 중간 | 휴식과 재노출 | 시뮬레이터에 휴식 없음 | 일부 주행에 180초 이상 정차(휴게소) 구간 추가, 휴식 중 S/F 감쇠가 라벨에 반영되는지 확인 | `sim_config.json` stop, `sim.py` |
| T-04 | 높음 | IMU 장착 기울기와 자이로 바이어스 | 보정 없음. 정지 녹화에서 수평 가속도가 약 8.4 m/s²로 읽힘. Oman 입력은 가중 전 값이라 기울기 중력이 점수에 그대로 들어감 | P1 LIVE에서 정지 구간으로 장착 자세(회전 행렬)와 자이로 오프셋을 추정해 빼기 | `controller/qnx/p1_acq.cpp`, `FEATURES.md` |
| T-05 | 중간 | Oman 0.63 Hz 필터와 K0 | TU Delft로만 맞춤 | 실차 IMU + MISC 기록으로 필터 유무, K0 재적합 | `constants.json`, `oman_tudelft.py` 방식 |
| T-06 | 낮음 | Wf를 수평 x, y에, MSDV를 수평 h로 | ISO는 z축용 | 수평 가중을 쓴 차량 멀미 문헌 확인, 없으면 근거 없음을 명시하거나 가중 없는 RMS로 바꿈 | 3.2, 3.3 |
| T-07 | 낮음 | 이벤트 기준 1.5 / −0.75 / 2.0 m/s² | 2026-11-16: 값은 버스 승객 승차감 연구와 같음 (1.5, −0.75: He 등 2013, 2.0: Nguyen 등 2019, 피처 근거 문서). 멀미 문턱이 아니라 승차감 불편 문턱. −0.75는 평범한 제동도 셈. 같은 제동을 여러 번 세던 것은 히스테리시스로 고침 (feature_order v3) | PRD가 이 값을 가져왔는지 원문 확인. 멀미 예측용 기준은 실차 라벨로 다시 맞추기 | `constants.json` events |
| T-08 | 낮음 | `pitch_mean_1m` | 평균 가감속이 섞임 | 속도 미분으로 가감속을 빼거나 자이로 pitch 적분 사용 | `features.cpp` |
| T-09 | 중간 | MSSQ 민감도 계수 0.3, 범위 0.5~2 | 근거 문헌 없음 | paper_v2 FR-05의 출처 확인, 실차 참가자로 재적합 | `constants.json` mssq |
| T-10 | 중간 | 사람별 퍼짐 0.65, 환경 효과 크기 (뒷자리, 온도, 풍량), 라벨 잡음 0.5 | TU Delft 17명 / [추측] | 실차 데이터로 재추정. 그 전까지 범위로 둠 | `sim_config.json`, `sim_effects.json` |
| T-11 | 높음 | P1 LIVE 모드 (MPU-6050 실시간 읽기) | 재생만 | I²C 읽기, 100 Hz 타이밍, T-04 보정, CAN 없을 때 G2~G7 NaN 처리 | `controller/qnx/p1_acq.cpp` |
| T-12 | 높음 | Tier 문턱 목표 85% / 0.5회 / 0.25회 | 자체 결정. Tier 2·3 탐지율 낮음 | 팀이 목표 합의 후 `train.py` 기준만 바꿔 재학습 | `ml/train/train.py`, `thresholds.json` |
| T-13 | 중간 | 의심 피처와 쓰지 않는 피처 | ev_experience, seatback_angle이 라벨 효과 없이 쓰임. 27개 이득 0 | 피처 순열 중요도로 확인, 필요하면 시뮬레이터에 효과를 넣거나 제외. 실차 라벨 확보 후 다시 평가 | `train.py`, `feature_order.json` |
| T-14 | 중간 | 시뮬레이터 수치 전반 | 거의 [추측] | 실차 녹화 몇 회와 가속도·속도·저크 분포 비교 후 조정 | `sim_config.json` |
| T-15 | 낮음 | 시야 항 (바깥을 봄 / 화면을 봄) | 모델에 없음. 슬라롬 확인에서 바깥 보는 조건을 설명 못 함 | 폰 화면 ON·응시 신호(P6)를 라벨과 피처에 반영할지 결정 | `sim.py`, `feature_order.json` |
| T-16 | – | 폰이 없을 때 P3 기본 프로필 (C17) | **해결 (2026-11-11)**. AC-04에 "기본 프로필" 조건 추가, Pi에서 폰 없이 돌린 결과가 Mac과 같음 | – | – |
| T-21 | 해결 | 기본 프로필일 때 AC-04 P(≥4) | T-23 보정 데이터셋에서 0.478로 G1+G9 모델(0.472) 이상. 보정 전에는 0.002 낮았음 | 데이터셋이 바뀌면 다시 확인 (train.py가 매번 보고) | `ml/out/train/report.md` |
| T-22 | 해결 | featurize 시작 1분 rms_z | 2026-11-12 해결: Wf와 0.63 Hz 저역통과를 첫 샘플의 정상상태로 시작 (C++ `Biquad::prime`, Python `wf.weight`, `oman.input_signal(prime=True)`). 시뮬레이터 첫 1분 rms_z_1m 중앙값 1.02 → 0.005. 데이터셋·모델 다시 만듦, 최종 문턱은 그대로(0.63/0.65/0.77). Mac AC-01 통과 | 2026-11-17 Pi에서 v007로 다시 빌드, AC-01·AC-02 통과 | `controller/bringup/README.md` |
| T-23 | 1차 완료 | 시뮬레이터를 실차 분포에 맞추기 | 2026-11-12: 고속도로 구간, 가감속·저크·정차 시간, 노면 굴곡, 차로 유지 흔들림을 BATON-Sample 사람 운전에 맞춤. 피처 15개 평균 KS 0.38 → 0.14, P(≥4) 0.37 → 0.21 (실차 0.20). 문턱 0.54 / 0.46 / 0.53 ([REALDATA.md](REALDATA.md) 6장) | BATON 전체(운전자 150명)와 국내 도심(KAIST, 직접 녹화)으로 다시 맞추기. 운전자 2명에 맞춘 값이라 과적합 가능 | `ml/simulator/sim_config.json`, `ml/realdata/calibrate.py` |
| T-24 | 일부 해결 | k5·k6 모델이 거의 빈 모델 | 2026-11-12: 강한 규제(잎 7, 잎당 최소 1,000행, L2 10, 학습률 0.03) + 최소 100회. 트리 4, 1개 → 100, 102개. k5 PR-AUC 0.186 → 0.203 (Oman만 0.202), k6 0.062 → 0.055 (Oman만 0.086). 문턱 0.53 / 0.51 / 0.59. 남은 문제: oman_score × 민감도 하나가 모델보다 나음 (PR k4 0.549 대 0.499, k5 0.275 대 0.203). 트리가 K·민감도 곱을 드문 k에서 못 배움 | 2026-11-16 시험(피처 v2, `ml/out/runs/v001` 보관): oman_personal 추가 + mssq·sensitivity를 모델 입력에서 끔. 시뮬레이터 PR은 k3~k6 0.867 / 0.520 / 0.241 / 0.075로 올랐지만, BATON PR k4 0.52 → 0.48, k5 0.39 → 0.26, TU Delft PR k3 0.80 → 0.76, AC-04 기본 프로필 0.481 → 0.471(기준 0.472 미달)로 실차에서는 나빠져 **채택 안 함**. 시뮬레이터 라벨이 Oman × 민감도라 곱 피처는 그 안에서만 이득. 함께 확인한 것: mssq에 제약이 없으면 트리가 사람 ID로 씀(기본 프로필이 실제 프로필보다 P(≥4) 0.508 대 0.492). k6 모델은 라벨에 효과 없는 seatback_angle 8.9%, soc 4.3%, ev_experience 3.8%에 기댐 | 개인 K 보정(승객 피드백)이 효과가 더 큼 (TU Delft 0.74). 라벨에 효과 없는 피처 정리(soc, 배터리 출력, ev_experience) 시험 | `ml/train/common.py`, `ml/verify/tudelft_replay.py` |
| T-17 | 높음 | 프로필의 EV 경험, 차량 유형 (C16) | 저장 안 함 | 프로필 파일과 PROFILES 메시지에 ev, vtype 추가 (P6에서 받음) | `store.cpp`, `messages.hpp` |
| T-18 | 높음 | 빠른 끄기 3번 규칙 (C12) | 빠른 끄기만 셈 | 팀 확인 후 필요하면 `policy.cpp`와 AC-09 테스트 수정 | `policy.cpp`, `POLICY.md` |
| T-19 | 높음 | P5 기기 관리, P6 통신, HEARTBEAT, MOT (100 ms) | [미구현] | **부분 해결 (2026-11-18)**: P5·P6 구현(PR #4), P4가 DEV_SYNC로 등록·CTX_* 처리·TIER_CMD, P2가 MOTION 100 ms. 남은 것: 실제 ESP32·폰·HUD로 Pi 시험, `RF_MESSAGES.md` §5 확정 | `controller/qnx/`, `docs/RF_MESSAGES.md` |
| T-20 | 높음 | 실데이터 검증 | 합성 데이터 성능만 있음 | 실차 주행 + 1분 MISC 기록 수집, 이 문서의 TU Delft 적합·[추측] 값을 다시 맞춤 | 전체 |

## 7. 파일 위치와 재현

**저장소 구조 (구현된 부분)**

| 경로 | 내용 |
|---|---|
| `protocol/` | 계약 파일 (0장 표), `check.py` 검증, `gen_cpp.py` → `controller/common/protocol_gen.hpp` |
| `ml/oman.py`, `ml/wf.py` | Oman, Wf Python 기준 구현 (C++와 테스트로 맞춤) |
| `ml/simulator/` | `sim.py` 주행·승객·라벨, `build_dataset.py` |
| `ml/train/` | `common.py` 파라미터, `train.py` 학습·문턱, `policy.py` 재생 |
| `ml/realdata/` | 실차 데이터 변환 (comma2k19, BATON), 축 정렬, 시뮬레이터 비교 ([REALDATA.md](REALDATA.md)) |
| `ml/verify/` | `oman_tudelft.py`, `slalom_check.py`, `model_parity.py` (AC-01), `pipeline_check.py` (AC-02), `export_csv.py` |
| `ml/runs.py`, `ml/out/runs/vNNN/` (git 제외) | 결과물 버전 폴더 (3.12). 데이터셋 버전: `dataset.npz`, `rides.json`, `trips/ride_*.bin`. 모델 버전: `models/`, `train/report.md`, `thresholds.json`, `compare/`, `verify/`, `parity/`. 실차 버전: `real_*.npz` |
| `controller/common/` | `features.cpp` (P2), `inference.cpp` (P3), `policy.cpp` (P4), `store.cpp`, `filters.hpp`, `FEATURES.md`, `POLICY.md` |
| `controller/qnx/` | `p1_acq` ~ `p4_policy`, `ipc.hpp`, `messages.hpp`, `run_pipeline.sh` |
| `controller/tests/`, `ml/tests/` | C++ / Python 단위 테스트 |
| `controller/bringup/README.md` | Pi 빌드, AC-01, AC-02 절차와 결과 |

**재현 (Mac)**

```bash
pip install -r ml/requirements.txt
```

```bash
cmake -S controller -B build && cmake --build build && ctest --test-dir build
```

```bash
python3 ml/verify/oman_tudelft.py data/raw/ATDOMS.mat
```

```bash
python3 ml/simulator/build_dataset.py
```

```bash
python3 ml/train/train.py
```

```bash
python3 ml/runs.py use vNNN
```

```bash
python3 -m pytest ml/tests
```

```bash
python3 ml/verify/export_csv.py
```

실차 비교 재현은 [REALDATA.md](REALDATA.md) 끝에 있습니다.

Pi(QNX) 빌드, AC-01, AC-02 절차는 `controller/bringup/README.md`를 따릅니다.

## 참고 자료

| 자료 | 쓴 곳 | 확인 수준 (PRD §12) |
|---|---|---|
| 「MerMaid 멀미 에이전트 PRD (구현용)」 2026-11-10 | PRD 설계 항목 전부 | 팀 문서 |
| 팀 Phase-2 논문 paper_v2 | TABLE VI, TABLE XIII, FR-05 | 팀 문서 |
| Irmak, Kotian, Happee, de Winkel, Pool (2022), Amplitude and Temporal Dynamics of Motion Sickness, Front. Syst. Neurosci. | Oman 구조, 시간상수, 지수 | 직접 확인 |
| ATDOMS 데이터셋, 4TU.ResearchData | K0, 퍼짐 0.65, MSSQ 식 확인 | 직접 확인 (데이터 분석) |
| Irmak, Pool, Happee (2021), Exp. Brain Res. 239:515–531 | 슬라롬 확인, 뒷자리(시야) 효과 방향 | 직접 확인 |
| Donohew & Griffin (2004) | Oman 입력에 가중하지 않는 근거 | 재인용 (Irmak 2021) |
| Golding (2006), MSSQ-Short | 채점식, 규준 12.90 / 9.90 | 직접 확인 |
| ISO 2631-1:1997 | Wf 계수, MSDV | 표준 (파라미터만) |
| Schmidt 등 (2020); Turner & Griffin (1999) | 뒷자리, 전방 시야 효과 방향 | 요약, 초록 |
| Arnold 등 (2019), Displays 60 | 실내 온도 효과 방향 | 초록 |
| D'Amour 등 (2017) | 풍량 효과 방향 | 초록 |
| Gu 등 (2025) | 소음 효과를 0으로 둔 이유 | 요약 (뉴스) |
| Frank & Hall (2001), ECML | 순서형을 누적 이진 분류기로 | 일반 기법 대표 문헌 |
