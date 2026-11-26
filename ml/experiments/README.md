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

---

# 보고 차수별 추가 스크립트 (2026-11-25 묶음)

실험 보고 차수(1차 4TU 검증, 2차 합성, 3차 문맥 모델) 기준으로 받은 스크립트. 내용은 받은 그대로이고 한 폴더에 평평하게 둔다.
블로그 대응: 1차 = IV-D 실험 1 (부록 C.3), 2차 = IV-D 실험 2·3 (부록 C.4·C.5), 3차 = 문맥 모델 (부록 B.6, C.5 그림 8).

| 차수 | 스크립트 | 하는 일 |
|---|---|---|
| 1차 (4TU) | `zeroshot_4tu.py` | 라벨 0개: Oman / GBM 베이스 / FT 베이스 |
| | `base_label_shape_4tu.py` | 베이스 라벨 모양 A/B 비교 (`stop_transfer_4tu.py`를 import) |
| | `ft_pretrain_4tu.py` | FT 마스킹 사전학습 |
| 2차 (합성, 응답 라벨 3출력) | `make_log_world.py` | 바뀐 세계 생성 (운전·센서·EV 비율·온도가 다른 설정, 새 사람). 15~25분 → `ml/out/bulk_world/log_dataset.npz` |
| | `bulk_pretrain_3head.py` | 공통 모듈: 사람별 숨은 성질로 응답 라벨 생성, GBM·FT 베이스, 갱신, 채점. 약 1.5시간 |
| | `base_zeroshot_3head.py` | 갱신 0개 |
| | `make_fleet_world.py` | 차 20대 × 30탑승, 차마다 가족 3~5명 → `ml/out/bulk_world/fleet.npz` |
| | `fleet_adapt.py`, `fleet_head_variants.py`, `fleet_gbm_variants.py` | 차별 맞춤 비교 (FT 조정 범위, GBM 조정 변형) |
| | `gbm_base_variants.py` | GBM 베이스 변형 (단조 끔 / 느슨 / 3출력 쌓기) |
| 3차 (문맥 모델) | `context_body.py` | 1단계 FT 몸통(`ft_base_response_1500.pt`) 고정 + 2단계 트랜스포머(폭 64, 2층, 문맥 32개). 차별 학습 없음 |

순서 (3차까지):
```bash
python3 ml/experiments/make_log_world.py
python3 ml/experiments/bulk_pretrain_3head.py --base-dataset ml/out/runs/v002/dataset.npz --log-dataset ml/out/bulk_world/log_dataset.npz \
    --gbm stacked --base-label response --out ml/out/bulk_3head_stk
python3 ml/experiments/make_fleet_world.py
python3 ml/experiments/fleet_adapt.py --base-dataset ml/out/runs/v002/dataset.npz --fleet ml/out/bulk_world/fleet.npz --cache ml/out/bulk_3head_stk/response
python3 ml/experiments/context_body.py --base-dataset ml/out/runs/v002/dataset.npz --fleet ml/out/bulk_world/fleet.npz --cache ml/out/bulk_3head_stk/response
```
캐시 폴더는 `<--out>/<--base-label>`이라 위 명령이 `ml/out/bulk_3head_stk/response/`에 `ft_base_response_1500.pt`, `gbm_base_response_stacked.txt`를 만든다
(`bulk_pretrain_3head.py` 294·334행). 받은 README에 정확한 명령이 없어 인자는 코드에서 맞춘 것이고, 이 순서로 실제로 돌려 본 적은 아직 없다.

## 지금 main에서 그대로 돌릴 때 알려진 문제

- **피처 목록:** 이 스크립트들은 feature_order v3(77칸, 켜진 74개)에서 썼다. 바뀐 세계·플릿의 결측 패턴과 FT 스크립트가 그룹 이름 `G3a`, `G3b`, `G4`, `G7`을 부르는데, v5(52칸)에는 이 그룹이 없어 KeyError가 난다
  (`bulk_pretrain_3head.py` `log_world_missing`, `make_fleet_world.py` `MISS_P`, `zeroshot_4tu.py`, `ft_pretrain_4tu.py`, `ft_transfer_4tu*.py`).
  보고서 그대로 재현하려면 v5 이전 커밋(`77af82a^`)과 v002 데이터셋을, 52칸으로 다시 하려면 이 그룹을 빼는 수정이 필요하다.
- **`make_log_world.py --same-world` 없음:** 받은 README는 문맥 모델 학습에 "추가 사람 600명"(`--same-world`)을 쓴다고 하지만 이 파일에는 그 옵션이 없다.
  `context_body.py --extra-base`에 줄 파일은 기본 설정으로 사람만 바꿔 `build_dataset.py --out ...`을 더 돌려 대신할 수 있다.
- **PyTorch 필요:** `pip install torch --index-url https://download.pytorch.org/whl/cpu`
- 출력(`ml/out/bulk_world/`, `ml/out/bulk_3head*/`)은 커밋하지 않는다.
