# 6차 실험 (v7) — 2단계 어텐션이 필요해지는 설계 공간 탐색

질문: 문맥 모델 2단계에서 **GRU(과거를 순서대로 상태 하나에)** 대신 **어텐션(지금 상황과 닮은 과거를 골라 읽기)** 이 필요해지는 조건은 무엇인가.
5차(v6)에서는 둘이 같았다 (0.884 vs 0.886). 거기서는 사람 문턱이 하나라 "최근 것 요약"으로 충분했기 때문이라는 것이 가설이다.

입력은 저장소 구현 기준 **v5 피처 52개**(`protocol/feature_order.json`; 학습엔 enabled 49열)만 쓴다. 세계 파일에는 v6 의 66열이 그대로 들어 있고 `--input v5` 가 골라 쓴다.

## 세계 (`world7.py`)

사람마다 문턱 집합이 **m개(모드)** 있고, 어느 집합이 쓰이는지는 **탑승 단위의 관측 가능한 상황**이 정한다. 상황 열쇠는 전부 v5 피처로 보이는 것:

| 열쇠 | 0 / 1 | 보이는 피처 |
|---|---|---|
| route | 도심 / 고속도로 | G2 `speed_mean_5m`, `speed_max_5m`, `stop_ratio_5m` |
| rough | 부드러운 / 거친 운전 | G1 `hard_*_count`, `rms_*`, `jerk_*` |
| hot | 보통 / 더운 실내 | G5 `cabin_temp` |
| rear | 앞 / 뒷좌석 | G6 `seat_row` |

모드별 문턱 = 사람 기본 문턱 + δ(사람, 모드, 장치), δ ~ N(0, `--mode-sd`), 사람 평균은 0으로 맞춤. δ가 **사람마다 다르므로** 피처만으로는 알 수 없고 그 사람의 **같은 모드 과거**를 읽어야 한다.

| 변수 | 값 | 뜻 |
|---|---|---|
| `--modes` | 1 / 2 / 4 | 모드 수 (= 2^열쇠 수, 열쇠는 route, rough, hot, rear 순) |
| `--arrange` | `blocked` / `run:L` / `iid` | 모드 배열: 모드마다 한 덩어리 / 평균 L탑승 지속 / 탑승마다 독립 |
| `--rides` | 20 ~ 400 | 사람당 탑승 수 = 이력 길이 |
| `--mode-sd` | 1.0 | 모드 간 문턱 차 (0.5 = 약한 모드) |
| `--theta-shift` | 0 | 문턱 전체 이동 (−1 이면 사건이 잦아져 증거 밀도 ↑) |
| `--drift` | none / slow / abrupt | 문턱이 시간에 따라 흐름 — "최근이 정답"이라 GRU 에 유리한 대조 조건 |

나머지(시각 과제, 개입 효과, 일정, 폰 신호…)는 world6 그대로이고 상황 의존 습관(`HABIT`)은 꺼서 상황 의존은 모드로만 들어온다.
`mode` 열이 행마다 npz 에 들어가고 `*_rides.json` 에 탑승별 모드·열쇠 비트·실제 문턱이 남는다.

## 문맥 모델 (`context7.py`)

한 세계 안에서 사람을 **학습 / 검증 20명 / 시험 40명**으로 고정 분할한다 (시드와 무관). 2단계만 시드가 돈다.

- 1단계 `mlp`(기본, d1 32): `[표준화 ‖ 마스크] → 128 → 64 → d1`, 학습 사람 행으로 학습해 동결, 캐시. 옵션 `--scale quantile`(분위수→정규), `--embed ple --bins 8`(구간 선형 부호화), `--stage1 linear`(몸통 없음).
- 토큰: `[E(d1) ‖ 장치 원핫 3 ‖ 라벨 1 ‖ (시각 2, --time rides)]`. 탑승·장치마다 양성(켠 순간) ≤1 + 음성(자격 있는데 안 켠 순간) `--neg-per-ride`개.
- 문맥 조립은 전부 배열 연산 (GPU 위 gather). 사람별 토큰을 순서대로 깔아 두고 질의 행마다 "내 앞 토큰 구간 [lo, c)" 만 색인한다 → k = 4096 도 한 스텝 수 ms.
- 2단계 (`--stage2`)

| 이름 | 구조 | 비고 |
|---|---|---|
| `gru` | pq, pc → GRU(d2) 마지막 상태 ‖ 질의 → 머리 | v6 권고안. 빈 칸이 섞여도 유효 토큰만 앞으로 당겨 돌린다 |
| `attn` | 질의 → 토큰 교차 어텐션 `--layers` 층, `--heads` 머리, Pre-LN, 빈 토큰 1개 | 마지막 층 가중치로 "같은 모드를 골랐나" 진단 |
| `enc` | v6 방식 `[질의 ‖ 토큰]` 인코더 | 토큰끼리도 어텐션 |
| `hybrid` | gru 상태 ‖ attn 요약 → 머리 | |
| `mean_dev` | 장치별 마스크 평균 | 순서도 질의도 없는 하한 |

- 선택 `--select recent`(최근 k개) / `uniform`(이력 전체에서 고르게 k개).
- 가시성 `--hide G2`: 질의·토큰 모두에서 그 그룹을 NaN 으로 (세계를 다시 만들 필요 없음).

### 채점 (시험 사람 자격 행)

| 표 | 내용 |
|---|---|
| 1 전체 | ctx / empty(문맥 0) / nolabel / nosit / swap(다른 사람 이력) / **samemode**(같은 모드 토큰만 주는 오라클) / common(1단계만) |
| 2 이력 길이 | 앞선 탑승 수 0 / 1~9 / 10~49 / 50~149 / 150+ |
| 3 **먼 참고** | 같은 모드 마지막 참고까지 거리 1 / 2~4 / 5~19 / 20~99 / 100+ / 없음 |
| 4 모드 전환 | 전환 직후 vs 같은 모드 연속 |
| 진단 | 어텐션 같은 모드 가중치 비율 (1 = 무작위), 모드 탐침(요약 E · 원 입력 X 로 모드 맞추기 정확도) |

모드 탐침이 낮으면 어텐션이 못 이기는 것은 모델 탓이 아니라 1단계 요약이 상황을 버린 탓이다 — 가설 검증 전에 먼저 본다.

## 가설

| 조건 | 예상 |
|---|---|
| m = 1, 또는 blocked | GRU ≥ attn (최근 것이 정답) |
| m ≥ 2 × run/iid × 이력 ≥ 60 × 열쇠 보임 | attn > GRU, k·이력이 길수록 벌어짐. 표 3 의 먼 버킷에서 attn 이 오라클에 다가감 |
| `--hide` 로 열쇠를 가리면 | 둘 다 떨어지고 차이 사라짐 |
| drift | GRU ≥ attn |

## 기존 v6 세계에서 어텐션 설계 공간 (`--fleet`, 세계 생성 없음)

`context7.py --world ml/out/v6/strong/base.npz --fleet ml/out/v6/strong/fleet.npz` 로 v6 와 같은 방식(베이스 학습, 검증 60명, 플릿 trip_order ≥ 24 채점)을 돌린다. 입력은 v5 만.
v6 세계에는 모드가 없는 대신 strong 습관(사람별 고속·더위 계수)이 모드 노릇을 하므로 **상황별 표 5**를 본다:
고속(속도 1분 ≥ 80 km/h) / 더운 실내(≥ 27 °C) / 뒷좌석 행 따로, 그리고 "고속 행인데 이 사람의 직전 고속 탑승이 1 / 2~4 / 5~19 / 20+ 전 / 처음" 버킷, 어텐션이 같은 상황 토큰에 몰리는 비율.

| 단계 | 내용 | 실행 수 |
|---|---|---|
| `v6a` | 형태 {enc, attn, hybrid} × k {32, 128, 256} × 시각 {none, rides} × 시드 {0,1} + gru 기준 | 세계당 42 |
| `v6b` | ATT(기본 attn) k128 에서 층 1/4 · 머리 2 / 8(d2 128) · 드롭아웃 0.2 · d1 8/64 · 음성 10 · 2만 스텝 · 분위수+PLE · k32 uniform · mean_dev (gru 짝 포함) | 세계당 ~22 |

```bash
PHASES="v6a sum" DEVICE=cuda W6S="strong weak" bash ml/experiments/v7/run_v7.sh     # 결과 ml/out/v7/v6_<world>/ctx7fl_*/
PHASES="v6b sum" DEVICE=cuda ATT=attn bash ml/experiments/v7/run_v7.sh
```

기대: v6 에선 이력이 20탑승(토큰 ~140개)이라 k128 이면 GRU 도 전부 기억한다 — 평균에서 역전은 기대하지 않고, 상황별·먼 참고 행에서 차이가 숨어 있는지와 구조 축의 민감도를 본다.

## 1차 결과(2026-11-28)와 2차 설계

1차(새 세계 4 × {gru, attn} × k 3 × 시드 2, 시각 부호 없음·d1 32): m1 같음 / blocked GRU(attn 은 k 클수록 하락, 옛 블록이 방해) / iid attn +0.005 (일관, k 와 함께 벌어짐) / run10 평균은 GRU 가 +0.012 지만
같은 모드 참고가 5탑승 이상 멀면 attn 이 +0.05~0.12, 전환 직후 +0.04. 둘 다 오라클(같은 모드만, 0.88)까지 0.03 을 못 가져감.
병목 둘: ① attn 에 시각 정보가 없었음 (기존 세계에서 `--time rides` 가 +0.014) ② 검색 키가 약함 — 모드 탐침이 원 입력 X 0.76 vs 요약 E 0.64 (1단계가 상황 정보를 버림).

2차는 그래서 **모델 공정화 먼저**(`p2a`: 시각 부호 + d1 64 를 전 모델에, `--key-feats auto` 로 상황 피처 11개를 E 옆에 직접 붙여 검색 키 보강, hybrid 포함), 그 다음 경계 세계(`p2w` → `p2b`).

```bash
PHASES="p2a p2w p2b sum" PAR=4 JOBS=20 DEVICE=cuda THREADS=6 bash ml/experiments/v7/run_v7.sh
```

`PAR=4` 는 한 단계의 실행을 4개씩 동시에 돌린다 (한 실행이 GPU 를 5% 밖에 안 써서). 같은 세계의 1단계 몸통·E 캐시는 파일 잠금으로 한 번만 만든다.

## 돌리기

```bash
# 노트북 (세계 생성, 8코어 ≈ 세계당 40분; build/featurize 필요)
PHASES=worlds JOBS=8 bash ml/experiments/v7/run_v7.sh
# GPU (학습·채점; 세계 npz 를 ml/out/v7/<world>/base.npz 에 두고)
PHASES="p1 sum" DEVICE=cuda THREADS=16 bash ml/experiments/v7/run_v7.sh
PHASES="p2w" JOBS=16 bash ml/experiments/v7/run_v7.sh          # 2차 세계 (GPU 머신 CPU 로도 됨, featurize 빌드 필요)
PHASES="p2 sum" DEVICE=cuda bash ml/experiments/v7/run_v7.sh
```

1차 = 세계 4 (m1 / m4_blocked / m4_run10 / m4_iid) × {gru, attn} × k {128, 512, 2048} × 시드 {0, 1} = 48 실행.
2차 = 경계 조건 (m2, 이력 60/400, 모드 차 절반, 증거 밀도, drift, 가림, 잡음 토큰 10, uniform 선택, 시각 부호, 분위수+PLE, 드롭아웃 0.2, 머리 8/d2 128, hybrid/enc/mean_dev).

낱개 실행:

```bash
python3 ml/experiments/v7/context7.py --world ml/out/v7/m4_iid/base.npz --stage2 attn --k 512 --seed 0 --device cuda
python3 ml/experiments/v7/summarize7.py --out ml/out/v7      # → ml/out/v7/summary7.md, all_runs.csv
```

출력: `ml/out/v7/<world>/ctx7_<stage2>_k<k>[_<tag>]_s<seed>/{summary.md, metrics.json, oneline.csv, log.txt, ctx_*.pt}`.

## 연습용 작은 크기

```bash
OUT=ml/out/v7tiny PERSONS=6 RIDES=8 VALP=1 TESTP=2 STEPS=200 JOBS=2 K1=16 SEEDS=0 PHASES="worlds p1 sum" bash ml/experiments/v7/run_v7.sh
```
