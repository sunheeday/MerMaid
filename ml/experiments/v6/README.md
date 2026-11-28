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
