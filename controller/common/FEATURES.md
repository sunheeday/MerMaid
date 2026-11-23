# 피처 계산 정의 (feature_order.json v5)

`features.cpp`가 구현하는 정의입니다. PRD에 계산식이 없던 부분은 여기서 정했고, 바꾸면 `feature_order.json`의 `version`을 올립니다.

## 입력과 주기

- 입력: `protocol/trip_format.json`의 한 행 (100 Hz). 가속도계는 중력 포함 값(평평하고 정지하면 z = +9.81), x 전방, y 왼쪽, z 위
- 10초(1,000샘플)마다 차량 피처 46개(인덱스 0~45)를 냅니다. 첫 벡터는 1,000번째 샘플에서
- 창(10 s, 1 min, 5 min)은 주행 시작 직후 샘플이 모자라면 있는 샘플만 씁니다
- 차량 신호가 NaN이면 그 신호로 만든 피처는 NaN. `enabled=false` 피처는 항상 NaN

## G1 (0~32)

| 피처 | 정의 |
|---|---|
| `rms_*` | Wf 가중 가속도의 창 RMS. Wf의 0.08 Hz 고역통과가 중력과 기울기를 지움 |
| `decel_rms_x_*`, `accel_rms_x_*` | 가중 x가 음수(감속) / 양수(가속)인 샘플의 제곱합을 **창 전체 샘플 수**로 나눈 RMS. 두 값의 제곱합 = `rms_x`² |
| `jerk_rms_*` | 가중 가속도의 샘플 차분 / dt 의 RMS |
| `yaw_rms_*` | 자이로 gz(rad/s) RMS. 바이어스 보정은 P1에서 할 일 |
| `hard_*_count_*` | Oman 입력과 같은 0.63 Hz 저역통과 가속도가 기준(1.5, −0.75, \|y\| 2.0 m/s²)을 넘는 순간(상승 에지) 수 |
| `event_irregularity_5m` | 5분 안 모든 이벤트 시각 간격의 변동계수 (모표준편차 / 평균). 2개 미만이면 NaN |
| `pitch_mean_1m` | atan2(1분 평균 원 ax, 1분 평균 원 az). 1분 동안의 평균 앞뒤 가속도가 섞임 |
| `msdv`, `msdv_inc_5m` | √(Σ h² dt), h = 가중 \|(x, y)\|. 증가량은 30개 전(5분 전) 값과의 차, 5분 전이면 0과의 차 |
| `oman_score`, `oman_delta_2m`, `sensitization` | `ml/oman.py`와 같은 식, K = K0. 변화량은 12개 전(2분 전) 값과의 차. 민감화 = 느린 상태 S |
| `svc_conflict` | v1 비활성 |

## G2, G5, G6 (33~42)

| 피처 | 정의 |
|---|---|
| 속도 (33~39) | 평균, 모표준편차, 최고. 정차 비율 = 속도 < 0.1 m/s 인 샘플 비율 |
| `cabin_temp`, `fan_level`, `seat_row` (40~42) | 마지막 샘플 값 |

v5에서 차에 들어올 출처도 없고 라벨에 닿는 경로도 없는 25개를 지웠습니다: 페달·주행 모드·크루즈(G3a), 회생·배터리·토크(G3b),
내비(G4), 소음(G7), 설정 온도, 에어컨, 등받이 각도, 창문. 원시 칸(`trip_format.json`)은 그대로 둡니다
(P6 MOTION이 `pred_ax/ay_10s` 칸을 씀).

## G9 (43~45)

- `ride_time`: 주행 첫 샘플부터의 시간
- 휴식: 차속이 있으면 속도 < 0.1 m/s, 없으면 h < 0.05 m/s²인 상태가 180초 연속. 다시 움직이면 `reexposure` = 1, `time_since_rest`는 그때부터의 시간 (휴식이 없었으면 NaN)
- 차속이 없으면 매끄러운 도로의 정속 주행도 휴식으로 볼 수 있습니다 (IMU만으로는 구분 불가)

## 확인 방법

- `ctest --test-dir build`: 0.2 Hz 사인파 RMS, 상수 입력 MSDV, 휴식 3분, 이벤트, 경사, 결측 그룹
- `python3 -m pytest ml/tests`: Oman, 가중 RMS, MSDV가 Python 기준 구현과 1e-9 안에서 같은지
