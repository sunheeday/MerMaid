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
python3 -m pytest ml/tests
```

| 단계 | 파일 | 출력 |
|---|---|---|
| S1 Oman 검증 | `oman.py`, `verify/oman_tudelft.py` | K0, MAE, LOPO 기준선 (`verify/out/`) |
| S3 합성 데이터 | `simulator/sim.py`, `simulator/build_dataset.py` | `out/dataset.npz`, `out/dataset_report.json`, `data/trips/ride_*.bin` |

## 시뮬레이터 요약

- 경로: 직선, 정차, 회전, S자, 회전교차로, 언덕을 15~40분 분량으로 잇습니다. 경로마다 승객 4명이 타고, 탑승마다 좌석, 회생 단계, 공조, 잡음이 다릅니다.
- 운전: 거리 격자에서 곡선 횡가속도와 가감속 한계로 속도 프로파일을 만들고(앞뒤 두 번 훑기), 0.5 Hz 영위상 필터로 저크를 제한합니다. 회생 단계가 높을수록 감속이 셉니다.
- IMU: 운동학 가속도 + 경사의 중력 성분 + 노면 진동(2~15 Hz, 속도 비례) + 센서 잡음. z는 중력 포함 +9.81
- 라벨: `K = K0 x 실제 민감도 x m`. 실제 민감도 = MSSQ 민감도 x exp(N(0, 0.65)). 0.65는 TU Delft 개인별 K를 MSSQ로 설명하고 남은 퍼짐입니다. 모델 입력 G10에는 MSSQ 민감도만 들어갑니다.
- Oman 라벨 입력은 중력과 진동을 뺀 운동학 수평 가속도입니다. P2는 IMU 값을 쓰므로 둘이 다릅니다.
- 잡음 σ 0.5를 1분 단위로 묶어 더한 뒤 반올림합니다. 미래 5분이 다 있는 시점만 학습 행이 됩니다.
- 모든 수치는 `simulator/sim_config.json`, `simulator/sim_effects.json`에 있고 근거가 없는 값은 `guess`로 표시했습니다.
