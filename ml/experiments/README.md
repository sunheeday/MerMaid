# ml/experiments — 합성 베이스 전이 실험 (4TU)

실제 사람 데이터(TU Delft ATDOMS, 17명)로 아키텍처 v3의 세 가정을 확인한 실험 코드.
결과 정리는 `소공 모델 전이 실험 보고` 문서 참고.

| 스크립트 | 질문 | 비교 | 시간 |
|---|---|---|---|
| `ft_transfer_4tu.py` | (30분판) 라벨 없이 시뮬레이터로 사전학습한 FT가 4TU 사람 1~2명으로 바로 맞히나 | Oman 규칙 / GBM / FT 처음부터 / FT 사전학습 | ~30분 CPU |
| `ft_transfer_4tu_v2.py` | 실험 1. 같은 라벨(MISC≥k)에서의 전이 | + 합성 GBM 베이스 그대로·이어학습, FT 몸통 2종(결측 전부/표 확률), 탐침·전체, 열화 검사 | ~2시간 CPU (`--reuse-bodies`로 사전학습 생략 가능) |
| `stop_transfer_4tu.py` | 실험 2. 라벨이 행동(멈춤)으로 바뀌어도 합성 베이스가 도움이 되나 | Oman 규칙 / 처음부터 / 베이스(k=4) 그대로 / 베이스(k=3,4)+이어학습 | ~1분 |
| `unlabeled_logs_4tu.py` | 실험 3. 빈 라벨 로그를 먹은 FT가 GBM을 넘나 (합성 없음) | GBM 베이스+이어학습 / FT 베이스+빈라벨+미세조정 / (`--ablation`) FT 빈라벨 없음 | ~70분 CPU |

## 준비

```bash
pip install -r ml/requirements.txt
pip install torch --index-url https://download.pytorch.org/whl/cpu      # FT용 (CPU)
cmake -S controller -B build && cmake --build build                      # featurize 바이너리
python3 ml/simulator/build_dataset.py                                    # ml/out/runs/vNNN/dataset.npz
# ATDOMS.mat: https://data.4tu.nl/datasets/24d81854-b10b-446b-8798-ae31fb8782d0 → ml/data/raw/ATDOMS.mat
```

## 실행 (repo 루트에서)

```bash
DS=ml/out/runs/v002/dataset.npz          # 자기 버전으로
python3 ml/experiments/ft_transfer_4tu.py    ml/data/raw/ATDOMS.mat --dataset $DS
python3 ml/experiments/ft_transfer_4tu_v2.py ml/data/raw/ATDOMS.mat --dataset $DS            # 처음
python3 ml/experiments/ft_transfer_4tu_v2.py ml/data/raw/ATDOMS.mat --dataset $DS --reuse-bodies   # 몸통 재사용
python3 ml/experiments/stop_transfer_4tu.py  ml/data/raw/ATDOMS.mat --dataset $DS
python3 ml/experiments/unlabeled_logs_4tu.py --ablation                                      # stop_transfer 캐시 사용
```

## 출력

각각 `ml/out/<실험명>/`에 `summary.md`(표 + 읽는 법), `results.csv`(전체 행), `fig*.png`, `log.txt`.
캐시: `ml/out/ft_transfer/tudelft_rows.npz`, `ml/out/stop_transfer/tudelft_stop_rows.npz`(4TU → 74열 변환, 첫 실행만 2분),
`ml/out/ft_transfer_v2/body_*.pt`(FT 몸통), `ml/out/stop_transfer/gbm_base_k*.txt`(합성 GBM 베이스).
`ml/out/`은 커밋하지 않는다.

## 공통 사항

- 피처는 `protocol/feature_order.json`의 `enabled: true` 74개. `common.py`의 GROUPS/DROPPABLE/NEVER/MONOTONE/PARAMS를 그대로 쓴다.
- 4TU → 가상 주행 변환은 `ml/verify/tudelft_replay.py`의 `session / stimulus / make_trip`을 import해서 쓴다. 변환 규칙을 바꾸면 캐시를 지워야 한다.
- 라벨: 실험 1은 "다음 300초 최대 MISC ≥ k", 실험 2·3은 "다음 300초 안 멈춤(MISC 6 도달)". 멈춤 라벨은 자극 구간 안의 행만 쓰고 멈춘 뒤의 행은 제외한다.
- 평가는 사람 단위 5-fold. n = 라벨을 쓴 사람 수, m = 라벨을 쓴 세션 수(실험 3).
- GBM은 repo `PARAMS`에서 `min_data_in_leaf`만 데이터 크기에 맞춰 줄인다(사람 1명 ≈ 140행).
- 알려진 한계: `stop_transfer_4tu.py`의 "사건 적중률" 지표는 문턱을 시험 집합에서 고르므로 포화된다. AUC 두 표로 읽는다.
- FT 사전학습 표준화 통계: v1·v2는 시뮬레이터, 실험 3은 4TU 입력 전부(라벨 없음).

## 결과 요약 (2026-11-24 실행 기준)

- 실험 1 (k=4, ROC): Oman 규칙 0.67 · 합성 GBM 그대로 0.71 · +이어학습 0.69→0.72 · 처음부터 0.63→0.66 · FT 사전학습 ≤ 처음부터.
- 실험 2 (멈춤, ROC): Oman 규칙 0.58 · 합성 GBM(≥4) 그대로 0.62 · +이어학습 0.62→0.65 · 처음부터 0.56→0.62 · ≥4 베이스 > ≥3 베이스.
- 실험 3 (멈춤, ROC): GBM 베이스+이어 0.68 평평 · FT 빈라벨+미세조정 0.61→0.67 · FT가 GBM을 이긴 쌍 8/30, 6/30, 11/30, 5/15 · 빈라벨 있음>없음 18/30(m=2)→6/15(전부).
