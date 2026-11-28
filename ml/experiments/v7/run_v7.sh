#!/usr/bin/env bash
# 6차 실험 러너 — 저장소 루트에서:  bash ml/experiments/v7/run_v7.sh
#   PHASES="worlds" JOBS=8 bash ml/experiments/v7/run_v7.sh                 # 노트북: 세계 4개 생성 (8코어 ≈ 세계당 40분)
#   PHASES="p1 sum" DEVICE=cuda bash ml/experiments/v7/run_v7.sh           # GPU: 1차 48실행 + 요약
#   PHASES="p2 sum" DEVICE=cuda bash ml/experiments/v7/run_v7.sh           # 2차(경계 조건) — 세계 p2w 가 먼저 있어야 함
# 단계
#   worlds  1차 세계 4개 (160명 × 200탑승):  m1 · m4_blocked · m4_run10 · m4_iid        (모드 수 1 / 배열 3종)
#   p2w     2차 세계: m2_iid · m4_iid_r60 (60탑승) · m4_iid_r400 (400탑승) · m4_iid_sd05 (모드 차 절반) · m4_iid_dense (문턱 −1) · m4_run10_drift
#   p1      1차: 세계 4 × {gru, attn} × k {128, 512, 2048} × 시드 {0, 1} = 48 실행
#   p2      2차: 1차에서 차이가 난 k 로 경계 조건 (가시성 --hide G2 / 잡음 토큰 10 / 선택 uniform / 시각 부호 / hybrid, enc, mean_dev)
#   v6a     기존 v6 세계(ml/out/v6/<W6S>/{base,fleet}.npz, 입력 v5)에서 어텐션 형태×k×시각 부호 (세계당 42실행)   ← 세계 생성 없음
#   v6b     기존 v6 세계에서 구조 축 (깊이·폭·드롭아웃·d1·음성·학습량·분위수+PLE·uniform)                       (세계당 ~22실행)
#   p2a     2차 a: 모델 공정화 — 1차 세계 4개 × {gru, attn, enc, hybrid}(시각 부호 + d1 64) + kf(검색 키 보강) 변형, k512, 시드 2 (56)
#   p2b     2차 b: 경계 세계 6개(p2w) × 5 모델 × 시드 2 + 가림·잡음 (64)
#   sum     요약표
# PAR=4 이면 한 단계의 실행을 4개씩 동시에 (GPU 하나로 충분; 캐시는 잠금으로 보호)
# 이미 oneline.csv 가 있는 실행은 건너뛴다.
set -u
PHASES=${PHASES:-"worlds p1 sum"}   # 2차: PHASES="p2a p2w p2b sum" PAR=4
JOBS=${JOBS:-8}; THREADS=${THREADS:-8}; DEVICE=${DEVICE:-cpu}
OUT=${OUT:-ml/out/v7}; V7=ml/experiments/v7; PY=${PY:-python3}
PAR=${PAR:-1}; JOBSF=$(mktemp)                    # PAR>1 이면 실행을 모아 두었다가 단계 끝에 동시에 돌린다 (GPU 는 한 실행이 5% 밖에 안 쓴다)
PERSONS=${PERSONS:-160}; RIDES=${RIDES:-200}; VALP=${VALP:-20}; TESTP=${TESTP:-40}; STEPS=${STEPS:-10000}; K1=${K1:-"128 512 2048"}; SEEDS=${SEEDS:-"0 1"}
mkdir -p $OUT; LOG=$OUT/run_v7.log
say() { echo "[$(date +%H:%M:%S)] $*" | tee -a $LOG; }
has() { case " $PHASES " in *" $1 "*) return 0;; *) return 1;; esac; }

world() {  # world <name> <world7 args...>
  local w=$1; shift
  [ -f $OUT/$w/base.npz ] && { say "있음 $w"; return; }
  mkdir -p $OUT/$w; say "세계 생성 $w: $*"
  $PY $V7/world7.py --persons $PERSONS --rides $RIDES --seed 0 --jobs $JOBS --out $OUT/$w/base.npz "$@" > $OUT/$w/world.log 2>&1 || say "실패 세계 $w (로그 $OUT/$w/world.log)"
}
ctx() {  # ctx <world> <stage2> <k> <seed> <tag|-> [extra args...]   → $OUT/<world>/ctx7_<stage2>_k<k>[_<tag>]_s<seed>/
  local w=$1 s2=$2 k=$3 seed=$4 tag=$5; shift 5; [ "$tag" = "-" ] && tag=""
  [ -f $OUT/$w/base.npz ] || { say "세계 없음 $w"; return; }
  local d=$OUT/$w/ctx7_${s2}_k${k}${tag:+_$tag}_s${seed}
  [ -f $d/oneline.csv ] && { say "건너뜀 $d"; return; }
  local cmd="$PY $V7/context7.py --world $OUT/$w/base.npz --stage2 $s2 --k $k --seed $seed --steps $STEPS --val-persons $VALP --test-persons $TESTP --threads $THREADS --device $DEVICE --out $d ${tag:+--tag $tag} $* > $d.log 2>&1 || echo \"[\$(date +%H:%M:%S)] 실패 $d (로그 $d.log)\" | tee -a $LOG"
  if [ "$PAR" -gt 1 ]; then echo "$cmd" >> $JOBSF; say "예약 $w $s2 k$k s$seed ${tag:+[$tag]} $*"; else say "시작 $w $s2 k$k s$seed ${tag:+[$tag]} $*"; bash -c "$cmd"; fi
}
flush() {  # 예약된 실행을 PAR 개씩 동시에
  [ -s $JOBSF ] || return 0
  say "동시 실행 $PAR 개 × $(wc -l < $JOBSF) 건"; xargs -P $PAR -I{} -d '\n' bash -c '{}' < $JOBSF; : > $JOBSF; say "묶음 끝"
}

if has worlds; then
  world m1         --modes 1
  world m4_blocked --modes 4 --arrange blocked
  world m4_run10   --modes 4 --arrange run:10
  world m4_iid     --modes 4 --arrange iid
fi
if has p2w; then
  world m2_iid        --modes 2 --arrange iid
  RIDES=60  world m4_iid_r60   --modes 4 --arrange iid
  RIDES=400 world m4_iid_r400  --modes 4 --arrange iid
  world m4_iid_sd05   --modes 4 --arrange iid --mode-sd 0.5
  world m4_iid_dense  --modes 4 --arrange iid --theta-shift -1.0
  world m4_run10_drift --modes 4 --arrange run:10 --drift slow
fi
if has p1; then
  for w in m1 m4_blocked m4_run10 m4_iid; do for k in $K1; do for s in $SEEDS; do for s2 in gru attn; do ctx $w $s2 $k $s -; done; done; done; done
  flush
fi
# ---- 2차 a: 모델 공정화 (1차 세계 4개). 시각 부호 + d1 64 를 전 모델에, 검색 키 보강(kf) 변형 포함.  4세계 × 7 × 시드 2 = 56
if has p2a; then
  K2=${K2:-512}; F="--time rides --d1 64"
  for w in m1 m4_blocked m4_run10 m4_iid; do for s in $SEEDS; do
    ctx $w gru    $K2 $s f    $F;            ctx $w attn   $K2 $s f    $F;   ctx $w enc $K2 $s f $F;   ctx $w hybrid $K2 $s f $F
    ctx $w gru    $K2 $s fkf  $F --key-feats auto;  ctx $w attn $K2 $s fkf $F --key-feats auto;  ctx $w hybrid $K2 $s fkf $F --key-feats auto
  done; done
  flush
fi
# ---- 2차 b: 경계 세계 (p2w 가 먼저) × {gru, attn, hybrid}(+시각, d1 64, kf) + gru/attn kf 없이.  6세계 × 5 × 시드 2 = 60, 그리고 m4_run10 가림·잡음 4
if has p2b; then
  K2=${K2:-512}; F="--time rides --d1 64"
  for w in m2_iid m4_iid_r60 m4_iid_r400 m4_iid_sd05 m4_iid_dense m4_run10_drift; do for s in $SEEDS; do
    ctx $w gru $K2 $s fkf $F --key-feats auto;  ctx $w attn $K2 $s fkf $F --key-feats auto;  ctx $w hybrid $K2 $s fkf $F --key-feats auto
    ctx $w gru $K2 $s f $F;                     ctx $w attn $K2 $s f $F
  done; done
  for s2 in gru attn; do ctx m4_run10 $s2 $K2 0 fkf_hide $F --key-feats auto --hide G2;  ctx m4_run10 $s2 $K2 0 fkf_noise $F --key-feats auto --neg-per-ride 10; done
  flush
fi
if has p2; then
  K2=${K2:-512}
  for w in m2_iid m4_iid_r60 m4_iid_r400 m4_iid_sd05 m4_iid_dense m4_run10_drift; do for s2 in gru attn; do ctx $w $s2 $K2 0 -; done; done
  for s2 in gru attn; do
    ctx m4_iid $s2 $K2 0 hide --hide G2
    ctx m4_iid $s2 $K2 0 noise --neg-per-ride 10
    ctx m4_iid $s2 $K2 0 uni --select uniform
    ctx m4_iid $s2 $K2 0 time --time rides
    ctx m4_iid $s2 $K2 0 qple --scale quantile --embed ple
    ctx m4_iid $s2 $K2 0 drop02 --dropout 0.2
    ctx m4_iid $s2 $K2 0 h8 --heads 8 --d2 128
  done
  for s2 in hybrid enc mean_dev; do ctx m4_iid $s2 $K2 0 -; ctx m4_run10 $s2 $K2 0 -; done
  flush
fi
# ---- 기존 v6 세계(weak/strong, 플릿 채점) 위에서 어텐션 설계 공간 — 입력 v5 만
OUT6=${OUT6:-ml/out/v6}; W6S=${W6S:-"strong"}; VALP6=${VALP6:-60}; ADAPT=${ADAPT:-24}; K6=${K6:-"32 128 256"}
ctx6() {  # ctx6 <v6world> <stage2> <k> <seed> <tag|-> [extra args...]  → $OUT/v6_<world>/ctx7fl_<stage2>_k<k>[_<tag>]_s<seed>/
  local w=$1 s2=$2 k=$3 seed=$4 tag=$5; shift 5; [ "$tag" = "-" ] && tag=""
  [ -f $OUT6/$w/base.npz ] && [ -f $OUT6/$w/fleet.npz ] || { say "v6 세계 없음 $OUT6/$w"; return; }
  local d=$OUT/v6_$w/ctx7fl_${s2}_k${k}${tag:+_$tag}_s${seed}; mkdir -p $OUT/v6_$w
  [ -f $d/oneline.csv ] && { say "건너뜀 $d"; return; }
  local cmd="$PY $V7/context7.py --world $OUT6/$w/base.npz --fleet $OUT6/$w/fleet.npz --cache $OUT/v6_$w/cache --val-persons $VALP6 --adapt-trips $ADAPT --input v5 --stage2 $s2 --k $k --seed $seed --steps $STEPS --threads $THREADS --device $DEVICE --out $d ${tag:+--tag $tag} $* > $d.log 2>&1 || echo \"[\$(date +%H:%M:%S)] 실패 $d (로그 $d.log)\" | tee -a $LOG"
  if [ "$PAR" -gt 1 ]; then echo "$cmd" >> $JOBSF; say "예약 v6/$w $s2 k$k s$seed ${tag:+[$tag]} $*"; else say "시작 v6/$w $s2 k$k s$seed ${tag:+[$tag]} $*"; bash -c "$cmd"; fi
}
if has v6a; then   # 형태 {enc, attn, hybrid} × k {32,128,256} × 시각 {none, rides} × 시드 {0,1} + gru 기준 = 세계당 42
  for w in $W6S; do for k in $K6; do for s in $SEEDS; do
    ctx6 $w gru $k $s -
    for s2 in enc attn hybrid; do ctx6 $w $s2 $k $s -; ctx6 $w $s2 $k $s time --time rides; done
  done; done; done
  flush
fi
if has v6b; then   # 구조 축 (ATT 형태, k128, 시드 0): 깊이·폭·드롭아웃·요약 차원·음성 수·학습량·스케일·선택
  ATT=${ATT:-attn}
  for w in $W6S; do
    ctx6 $w $ATT 128 0 L1 --layers 1;      ctx6 $w $ATT 128 0 L4 --layers 4
    ctx6 $w $ATT 128 0 h8 --heads 8 --d2 128; ctx6 $w $ATT 128 0 h2 --heads 2
    ctx6 $w $ATT 128 0 drop02 --dropout 0.2
    ctx6 $w $ATT 128 0 d1_8 --d1 8;        ctx6 $w $ATT 128 0 d1_64 --d1 64;   ctx6 $w gru 128 0 d1_8 --d1 8;  ctx6 $w gru 128 0 d1_64 --d1 64
    ctx6 $w $ATT 128 0 neg10 --neg-per-ride 10; ctx6 $w gru 128 0 neg10 --neg-per-ride 10
    STEPS=20000 ctx6 $w $ATT 128 0 s20k;  STEPS=20000 ctx6 $w gru 128 0 s20k
    ctx6 $w $ATT 128 0 qple --scale quantile --embed ple; ctx6 $w gru 128 0 qple --scale quantile --embed ple
    ctx6 $w $ATT 32 0 uni --select uniform; ctx6 $w gru 32 0 uni --select uniform
    ctx6 $w mean_dev 128 0 -
  done
  flush
fi
if has sum; then $PY $V7/summarize7.py --out $OUT | tail -5; say "요약 $OUT/summary7.md"; fi
