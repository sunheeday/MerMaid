# imu_calib — 실차 IMU 오프라인 보정·검증

실차 주행 기록(Pi `logs/rides/*.sqlite`)의 IMU 값을 학습 데이터(시뮬레이터, `ml/simulator/sim.py`)와 같은 정의로 맞추고,
보정 전후로 피처와 모델 출력이 어떻게 바뀌는지 확인하는 도구다. **맥에서 오프라인으로만 돈다. Pi 실행 코드는 바꾸지 않는다.**

자세한 배경·방법·결과·한계는 `docs/ml/imu_calib/`에 있다.

| 문서 | 내용 |
|---|---|
| `IMU_보정_검토요청.md` | 처음 읽는 사람용 전체 설명 (사전 지식, 단계별 식, 검증 V1~V12, 한계, 1차 검토 처리) |
| `IMU_보정_전략.md` | 계획과 결정 기록 (작업 중 계속 갱신, 시간순이 섞여 있음) |
| `6면_측정_절차.md` | 센서 축별 치우침·눈금을 재는 6면 정지 측정 방법 |
| `개인보정_첫결과.md` | 개인 보정(`ml/personal/fit_calib.py`)을 실차 2회에 처음 돌린 결과 |

문서 속 `imu_calib/`는 이 폴더, `~/SE_project/drive_logs/…`는 작성자 맥의 주행 기록 위치다(주행 원본은 저장소에 넣지 않았다).

## 파일

| 파일 | 하는 일 | 출력 |
|---|---|---|
| `imu_calib.py` | 계산 함수 (정지 구간, 기울기 회전, 요 추정, 6면 풀이, 시간축, 표시) | |
| `run_calibration.py` | 센서 오차 → 장착 기울기 → 요(추정만) → 이상값 표시 → 100 Hz 재표본화, 검증 보고 | `<주행>/calibrated/` |
| `six_face.py`, `sensor_6face.json` | 6면 측정 → 축별 치우침·눈금 (11-23 측정값, x축은 미측정이라 0·1) | json |
| `cross_slope.py` | GPS로 좌우 횡단 경사 성분 제거 (시험용) | `<주행>/calibrated_xslope2/` |
| `gps_check.py` | GPS 속도·방향과 대조 (V7) | `<주행>/calibrated/gps_check.*` |
| `check_applied.py` | 기록된 보정 값이 그대로 적용됐는지 재현 검사 | `check_applied.md` |
| `model_rerun.py` | 보정 전후 모델 재추론·정책 재생 (V12) | `model_rerun.md/json`, 그림 |
| `v11_features.py` | 실차 피처 분포 vs 학습 데이터 (V11) | `v11_분포.md`, 그림 |
| `make_plots.py` | 보정 전후 그림 | png |
| `결과_1121/`, `결과_1123/` | 11-21, 11-23 주행 결과 (MSSQ 33) | |
| `결과_1121_MSSQ26/`, `결과_1123_MSSQ26/` | 같은 주행을 MSSQ 26으로 가정한 모델 비교 | |

`model_rerun.json`은 크기 때문에 넣지 않았다. 아래 명령으로 다시 만든다.

## 준비

```bash
cd ml/realdata/imu_calib
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

`model_rerun.py`, `v11_features.py`는 저장소의 `build/featurize`(cmake 기본 빌드)와 `ml/out/runs/` 아래 데이터셋·모델을 쓴다.
`ml/out/`은 저장소에 없으므로 `ml/README.md` 순서대로 데이터셋과 모델을 먼저 만든다. 다른 저장소 사본을 쓰려면 `MERMAID_REPO`를 지정한다.

주행 폴더에는 `logs/rides/*.sqlite`와 `raw_caraxis/`(차 축으로 축 순서만 바꾼 100 Hz 원본, `samples_100hz.npy`, `transform.json`)가 있어야 한다.

## 실행 예 (11-23 주행)

```bash
R=~/SE_project/drive_logs/run_1123_033152
.venv/bin/python run_calibration.py $R --tilt-window 54 108 --yaw-window 176 1594 --drive-window 150 1600 --six-face sensor_6face.json
.venv/bin/python gps_check.py $R --drive-window 150 1600
.venv/bin/python cross_slope.py $R
.venv/bin/python check_applied.py $R
.venv/bin/python model_rerun.py $R 결과_1123_MSSQ26 --mssq 26 --still-until 150 --until 1600 --extra "F 횡단경사 뺌(직진 중앙값)=calibrated_xslope2"
.venv/bin/python v11_features.py $R 결과_1123 --drive-from 150 --until 1600 --extra "F 횡단경사 뺌(직진 중앙값)=calibrated_xslope2"
```

두 주행의 전체 명령은 `docs/ml/imu_calib/IMU_보정_검토요청.md` 12절에 있다.
