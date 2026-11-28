# ml/experiments/v6 — 5차 실험: v6 피처 세계에서 1단계·2단계 어텐션의 도입 당위성

설계 문서 `5차_실험_설계_검토요청_v6세계` 의 구현. **v5 저장소(protocol/feature_order.json version 5)** 위에서 돈다.
저장소 코드는 건드리지 않고 이 폴더만 추가한다 (피처 계약 v6 은 실험용 `6exp` 로만 쓰고 `protocol/` 은 그대로).

| 파일 | 역할 |
|---|---|
| `world6.py` | 세계 라이브러리: 시각 과제 5상태, K(t) 가변 Oman 라벨(개입 효과 다중 패스), 상황 의존 습관, 일정, EV lift-off, 새 피처 16개, 반응 라벨 Y/W |
| `make_world6.py` | 세계 생성 CLI (`base` 사람 단위 병렬 / `fleet` 차 단위 병렬) |
| `check_world6.py` | 세계 점검 그림 (피처 분포·사건·효과) |
| `common6.py` | 입력 집합(v6/v5/그룹 제거), 공통 모델 3종(GBM 쌓기 / MLP / FT), 채점 |
| `axis_a.py` | 축 A: 공통 모델 × 입력 집합 → 1단계 어텐션 판정 (FT − MLP, 윌콕슨) |
| `context6.py` | 축 B/C: 문맥 모델 (1단계 ft/mlp/linear × 2단계 attn/gru/gru_qfirst/mean/mean_dev × k), 진단 4종 (empty/swap/nolabel/**nosit**) |
| `summarize6.py` | 축 B/C 요약표 (요인표, 시드 띠, 짝지은 비교, 진단, 탑승 수별, 축 C) |
| `run_v6.sh` | 전체 러너 (단계별, 재실행 시 건너뜀) |

## 피처 v6 (실험판 `6exp`, 66개)

v5 52개 − G8(허용도 2, 꺼져 있던 칸) + 새 16개. 새 그룹:
G11 폰(화면 켜짐·앱 전면·수신 속도·미디어·이어폰·가로) 6 · G12 장치 켜진 분 3 · G13 조작 횟수·마지막 조작 뒤 분 2 · G14 시각 sin/cos·탑승 간격·누적 탑승 4 · G15 가속→감속 직행 1.
전체 목록은 `python3 ml/experiments/v6/world6.py` 또는 세계 폴더의 `feature_order_v6exp.json`.

## 준비 (한 번)

```bash
# 1) v5 저장소를 새 폴더에 (기존 v3 폴더는 그대로 둠)
cd ~/다운로드/mermaid_dir && curl -L -o MerMaid-v5.zip https://github.com/sunheeday/MerMaid/archive/refs/heads/main.zip && unzip -q MerMaid-v5.zip -d v5 && cd v5/MerMaid-main
python -c "import json; print(json.load(open('protocol/feature_order.json'))['version'])"   # 5 가 나와야 함
# 2) 이 폴더 복사 (zip 을 푼 곳에서)
cp -r ~/다운로드/v6_experiments/v6 ml/experiments/
# 3) featurize 빌드 + venv
cmake -S controller -B build && cmake --build build -j
source ~/다운로드/mermaid_dir/MerMaid-main/attatt/bin/activate      # 기존 venv 재사용 (torch, lightgbm, scipy, sklearn, pandas, matplotlib 있음)
pip install -r ml/requirements.txt
```

## 실행

```bash
# 연습 (10분): 작은 세계로 끝까지 한 번
OUT=ml/out/v6_try PERSONS=40 RIDES=6 VEHICLES=4 TRIPS=24 STEPS_A=600 STEPS_B=1000 ADAPT=12 PHASES="worlds a b1 sum" bash ml/experiments/v6/run_v6.sh
# 본 실험 (단계별 예상 시간은 run_v6.sh 머리글). 전부:
nohup bash ml/experiments/v6/run_v6.sh > ml/out/v6/nohup.log 2>&1 &
# 또는 단계를 골라서
PHASES="worlds a" bash ml/experiments/v6/run_v6.sh
PHASES="b1 b2 sum" bash ml/experiments/v6/run_v6.sh
```

세계가 만들어지면 먼저 `ml/out/v6/weak/base_check.png` 를 열어 분포가 그럴듯한지 본다.

## 결과 보낼 것

`ml/out/v6/summary_bc.md`, `ml/out/v6/{weak,strong}/axis_a/summary.md`, 그리고 작은 파일들만 묶어서:

```bash
cd ml/out && zip -r v6_results.zip v6 -i '*.md' '*.csv' '*.json' '*.log' '*.png' -x '*/cache/*'
```

## 학습한 문맥 모델을 Pi(P3)에 올리기

컨트롤러 P3는 `models.json`의 `kind`가 `ctx_gru`(2단계 GRU)나 `ctx_attn`(2단계 어텐션)이면 V3 경로를 탄다. 1단계는 `mlp`나 `ft`(피처 토큰 트랜스포머) 둘 다 된다.
C++ 구현은 `controller/common/ctx_model.cpp` (외부 라이브러리 없음, PyTorch 기본 `TransformerEncoderLayer`와 같은 계산).
Pi에는 학습 결과 `.pt` 두 개를 내보낸 폴더(`models/`)만 올린다. 학습은 Mac이나 GPU 서버에서 한다.

GPU 서버(Nebius VM)에서는 `nebius_attn.sh`가 이 순서를 묶어 둔다 (기본: 데이터 v023, 새 run v025, ft + attn):

```bash
NEBIUS=user@host bash ml/experiments/v6/nebius_attn.sh push      # Mac → VM ~/mermaid (코드 + npz 약 240 MB)
ssh user@host 'cd ~/mermaid && bash ml/experiments/v6/nebius_attn.sh setup && bash ml/experiments/v6/nebius_attn.sh train'
ssh user@host 'cd ~/mermaid && bash ml/experiments/v6/nebius_attn.sh status'   # "train rc=0 / done"이면 끝
NEBIUS=user@host bash ml/experiments/v6/nebius_attn.sh fetch     # 결과 .pt 2개 + fleet_preds.npz + 로그 → Mac의 v025
bash ml/experiments/v6/nebius_attn.sh finish                      # 내보내기 → ctx_check → 임계값 → run.json, 끝에 Pi로 보낼 명령
```

학습은 VM에서 nohup으로 돌아 ssh가 끊겨도 계속된다. 데이터·run 번호·구조는 `D=… V=… STAGE1=… STAGE2=…`로 바꾼다 (머리글 참고).
손으로 할 때의 같은 순서:

```bash
# 1) 학습 (GPU 서버: Nebius VM에서 ml/experiments/v7/gpu_setup.sh 한 번, 데이터 npz는 scp로)
python3 ml/experiments/v6/context6.py --base $D/base.npz --fleet $D/fleet.npz --cache $V/cache --input v5 \
    --stage1 ft --stage2 attn --k 128 --device cuda --out $V/train           # GRU는 --stage1 mlp --stage2 gru
# 2) 서버에서 가져올 것: $V/cache/body_ft_v5.pt, $V/train/ctx_attn_k128.pt, $V/train/fleet_preds.npz
# 3) 내보내기 + C++ 패리티 + 임계값 (Mac)
python3 ml/experiments/v6/export_ctx.py --base $D/base.npz --body $V/cache/body_ft_v5.pt --ctx $V/train/ctx_attn_k128.pt \
    --out $V/models --input v5 --stage1 ft --stage2 attn
build/ctx_check $V/models                                                   # PASS여야 함 (차이 < 1e-5)
python3 ml/experiments/v6/pick_thresholds_ctx.py --preds $V/train/fleet_preds.npz --out $V/thresholds.json --run $(basename $V)
# 4) Pi: 보내고 빌드, 패리티·시간, 두 주행 (state/ctx에 토큰이 쌓임)
COPYFILE_DISABLE=1 tar --no-xattrs --no-mac-metadata -czf - controller protocol $V/models $V/thresholds.json | ssh qnxuser@192.168.2.2 'cd ~/mermaid/repo && tar xzf - && cmake --build build'
ssh qnxuser@192.168.2.2 "cd ~/mermaid/repo && ./build/ctx_check $V/models"
```

정책 설정은 `ml/runs.py use $V`(배포: thresholds.json을 controller/config로 복사)나, 배포 없이 시험할 때는 controller/config를 복사한 폴더에
그 thresholds.json을 넣고 `CONFIG=<폴더> MODELS=$V/models KEEP_STATE=1 controller/qnx/run_pipeline.sh ...`.

Pi 4B (QNX 8.0) 측정, 승객 1명·문맥 토큰 128개 (`ctx_check`):

| 모델 | 10초 주기 (요약 + 2단계) | 탑승 시작 (문맥 준비) |
|---|---|---|
| mlp + gru | 43 µs | 4.7 ms (GRU 128 스텝) |
| ft + attn | 9.5 ms (어텐션은 지금 상황이 과거 토큰과 서로 보므로 매 주기 인코더) | 1.9 ms (토큰 투영 + 첫 층 Q·K·V) |
| mlp + attn (v026, v7 enc) | 9.2 ms | 2.0 ms |
| mlp + xattn (v027, v7 XAttn) | 0.18 ms (질의 한 줄만 토큰에 교차 어텐션) | 2.5 ms (층마다 토큰 K·V) |

승객 9명이면 주기마다 그 9배 (ft + attn, 토큰 128개씩이면 약 86 ms). 10초 주기에는 여유가 있지만 V1의 주기 목표(10 ms)보다 크다.

### 친구 v7 (context7.py) 학습 결과 내보내기

v7 `--fleet` 실행(`ml/out/v7/v6_<world>/ctx7fl_*`)은 다시 학습하지 않고 그대로 내보낼 수 있다. 이름이 다르다: v7 `enc` = 여기 `attn`, v7 `attn`(XAttn) = 여기 `xattn`.
시각 부호(`_time`, 토큰 38칸)·PLE·hybrid·d1 ≠ 32는 아직 C++에 없다. 데이터가 VM에 있으니 VM에서 내보내고 `models/`만 가져온다.

```bash
F=~/MerMaid/ml/out/v7/v6_strong     # VM, 읽기만
python3 ml/experiments/v6/export_ctx.py --base ~/MerMaid/ml/out/v6/strong/base.npz --body $F/cache/body_mlp32_v5_fl.pt \
    --ctx $F/ctx7fl_attn_k128_s0/ctx_attn_k128.pt --out ml/out/runs/v027/models --input v5 --stage2 xattn --k 128 \
    --v7 ~/MerMaid/ml/experiments/v7 --val-persons 60 --e-cache $F/cache/E_mlp32_v5_fl.npy     # E 캐시 차이 0이면 표준화가 학습 때와 같다
```

`--v7`은 context7 분할로 표준화 통계를 만들고 manifest에 `token_quota: 0`(그냥 최근 k개, context7 방식)을 적는다. v7 실행은 플릿 예측을 남기지 않아 thresholds.json은 아직 없다.
