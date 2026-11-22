# ml

개발 PC(Python 3.12, `pip install -r ml/requirements.txt`)에서 돌리는 학습 파이프라인입니다. 피처 계산은 C++ `featurize`를 호출하므로 먼저 빌드합니다.

```bash
cmake -S controller -B build && cmake --build build
```

```bash
python3 ml/verify/oman_tudelft.py ml/data/raw/ATDOMS.mat
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
| S3 합성 데이터 | `simulator/sim.py`, `simulator/build_dataset.py` | 새 버전 `out/runs/vNNN/`: `dataset.npz`, `dataset_report.json`, `trips/ride_*.bin` |
| S4 학습 | `train/train.py`, `train/policy.py` | 새 버전 `out/runs/vNNN/`: `train/report.md`, `models/model_2~6.txt`, `thresholds.json`. 배포는 `runs.py use vNNN` (`controller/config/thresholds.json`) |
| 시뮬레이터 보정 루프 | `synth/loop.py` (Claude API), `synth/bounds.json` | 새 버전 `out/runs/vNNN/` (kind synth): `synth.json`, 라운드별 요청·제안·점수, `best/` 설정. 적용은 `synth/loop.py adopt vNNN` |
| 실차 비교 | `realdata/` (comma2k19, BATON 변환) | 새 버전에 `real_*.npz`, 모델 버전에 `compare/*.md`. 설명은 `docs/ml/REALDATA.md` |
| S6 AC-01 모델 동등성 | `verify/model_parity.py` (+ 보드의 `verify_model`) | 모델 버전의 `parity/result.json`. 절차는 `controller/bringup/README.md` |
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

## 시뮬레이터 보정 루프 (`synth/`, Claude API)

Claude는 샘플을 만들지 않습니다. 실차와 시뮬레이터의 분포 요약을 보고 `sim_config.json` 값을 바꾸는 후보를 JSON으로 내고, 데이터는 지금처럼 `sim.py`가 만듭니다.

```bash
export ANTHROPIC_API_KEY=...   # 또는 ant auth login. 저장소에 두지 않습니다
```

```bash
python3 ml/synth/loop.py run --dry-run
```

```bash
python3 ml/synth/loop.py run --rounds 2 --candidates 4 --trips 40
```

```bash
python3 ml/synth/loop.py adopt vNNN
```

- 라운드: 지금 설정으로 작은 데이터셋(`--trips`, 시드 고정) → `realdata/calibrate.py`의 KS → Claude(`claude-opus-5-5`, 구조화 출력)가 후보 N개 → `bounds.json` 검사 → 후보마다 같은 크기·시드로 데이터셋 → KS.
- 채택: BATON KS 평균이 `--min-gain`(0.005) 이상 줄고 comma KS 평균이 `--comma-guard`(0.02)보다 더 나빠지지 않은 후보 중 가장 좋은 것. 다음 라운드의 기준이 됩니다.
- 바꿀 수 있는 값은 `synth/bounds.json`에 있는 것뿐입니다(구간 비율, 구간 속도·길이, 운전자, 노면, 흔들림, 진동). 승객, 라벨, `sim_effects.json`은 잠겨 있습니다. 라벨 효과는 MerMaid 실제 MISC 라벨이 생기면 엽니다.
- 보내는 것은 분포 요약(분위수, KS, 비율)과 설정값뿐입니다. `summarize.assert_aggregate`가 보내기 전에 검사합니다.
- `ml/simulator/`는 `adopt`로만 바뀝니다. 그 뒤 `git diff`로 검토하고 전체 `build_dataset.py` → `train.py` → `compare` → `runs.py use`는 지금처럼 사람이 합니다.
- 한 라운드는 후보 4개 기준으로 시뮬레이터 5번(40경로마다 약 1분)과 Claude 호출 1번입니다.
