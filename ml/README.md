# ml

개발 PC(Python 3.12, `pip install -r ml/requirements.txt`)에서 돌리는 학습 파이프라인입니다. 피처 계산은 C++ `featurize`를 호출하므로 먼저 빌드합니다.

```bash
cmake -S controller -B build && cmake --build build
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
python3 -m pytest ml/tests
```

| 단계 | 파일 | 출력 |
|---|---|---|
| S1 Oman 검증 | `oman.py`, `verify/oman_tudelft.py` | K0, MAE, LOPO 기준선 (`verify/out/`) |
| S3 합성 데이터 | `simulator/sim.py`, `simulator/build_dataset.py` | `out/dataset.npz`, `out/dataset_report.json`, `data/trips/ride_*.bin` |
| S4 학습 | `train/train.py`, `train/policy.py` | `out/train/report.md`, `out/models/model_2~6.txt`, `controller/config/thresholds.json` |
| S6 AC-01 모델 동등성 | `verify/model_parity.py` (+ 보드의 `verify_model`) | `verify/out/parity/result.json`. 절차는 `controller/bringup/README.md` |
| S6 P1→P4 파이프라인 | `verify/pipeline_check.py` (+ 보드의 `controller/qnx/run_pipeline.sh`) | Pi 결과와 Mac featurize + Python 비교. 절차는 같은 문서 |

## 학습 요약

- 사람 단위 5-fold. 학습 쪽 사람의 15%를 떼어 조기 종료와 문턱 선택에 씁니다. 학습에 쓴 행으로 문턱을 고르면 확률이 과하게 높게 나와서입니다.
- 학습 데이터 = 원본 + 그룹 드롭아웃 복사본 1벌 (탑승 단위로 그룹을 통째로, 그 위에 칸 단위 2%). G1, G9, 차량 유형은 지우지 않습니다.
- 정책 재생(`train/policy.py`)은 P4 규칙을 Python으로 옮긴 것으로, 게이트는 항상 열리고 수동 조작은 없다고 봅니다.
- 문턱은 PRD의 행 단위 재현율/정밀도 규칙 대신 문턱 고르기용 사람의 탑승을 0.05~0.95(0.01 간격)로 재생해 고릅니다. Tier 1은 85% 이상 미리 켜지는 가장 높은 문턱, Tier 2·3은 시간당 오경보 0.5회·0.25회 이하인 가장 낮은 문턱입니다. 양성 행 대부분이 이미 멀미가 온 뒤라 행 규칙은 문턱을 0.99 근처로 올려 증상 전에 켜지지 않게 만들기 때문입니다.

## 시뮬레이터 요약

- 경로: 직선, 정차, 회전, S자, 회전교차로, 언덕을 15~40분 분량으로 잇습니다. 경로마다 승객 4명이 타고, 탑승마다 좌석, 회생 단계, 공조, 잡음이 다릅니다.
- 운전: 거리 격자에서 곡선 횡가속도와 가감속 한계로 속도 프로파일을 만들고(앞뒤 두 번 훑기), 0.5 Hz 영위상 필터로 저크를 제한합니다. 회생 단계가 높을수록 감속이 셉니다.
- IMU: 운동학 가속도 + 경사의 중력 성분 + 노면 진동(2~15 Hz, 속도 비례) + 센서 잡음. z는 중력 포함 +9.81
- 라벨: `K = K0 x 실제 민감도 x m`. 실제 민감도 = MSSQ 민감도 x exp(N(0, 0.65)). 0.65는 TU Delft 개인별 K를 MSSQ로 설명하고 남은 퍼짐입니다. 모델 입력 G10에는 MSSQ 민감도만 들어갑니다.
- Oman 라벨 입력은 중력과 진동을 뺀 운동학 수평 가속도입니다. P2는 IMU 값을 쓰므로 둘이 다릅니다.
- 잡음 σ 0.5를 1분 단위로 묶어 더한 뒤 반올림합니다. 미래 5분이 다 있는 시점만 학습 행이 됩니다.
- 모든 수치는 `simulator/sim_config.json`, `simulator/sim_effects.json`에 있고 근거가 없는 값은 `guess`로 표시했습니다.
