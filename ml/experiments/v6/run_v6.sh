#!/usr/bin/env bash
# 5차 실험 전체 러너 — 저장소 루트에서:  bash ml/experiments/v6/run_v6.sh
#   PHASES="worlds a b1" bash ml/experiments/v6/run_v6.sh     # 일부 단계만
#   JOBS=8 THREADS=8 bash ml/experiments/v6/run_v6.sh           # 세계 생성 프로세스 수 / torch·lightgbm 스레드 수
# 단계 (노트북 8코어 기준 예상)
#   worlds  세계 4개: base(400명×20탑승)/fleet(20대×48탑승) × weak/strong              ~30분
#   a       축 A: 공통 모델 3종 × 입력 7종 × 2세계 (+ v6 에서 ft·mlp 시드 1,2)           ~2.5시간
#   b1      축 B 핵심: k128 × {ft-attn, ft-gru, ft-gru_qfirst, ft-mean_dev, mlp-attn, mlp-gru} × 2세계   ~3시간
#           (1단계 ft vs mlp 는 여기서도 본다 — 공통 모델만으로는 사람 문턱을 몰라 ROC 0.6 근처라 차이가 안 보임)
#   b2      축 B: k32 × {attn, gru} × 2세계                                             ~1시간
#   b3      축 B: k256 × {attn, gru} × 2세계                                            ~2.5시간
#   b4      축 B 시드: strong 세계 k128 attn/gru 시드 1,2 (+ weak)                      ~1.5시간
#   c       축 C: 입력 v5 로 문맥 모델 (ft-gru, ft-attn, k128) × 2세계                    ~1시간
#   sum     요약표 (언제든 다시 돌려도 됨)
# 이미 결과가 있는 실행은 건너뛴다 (폴더에 oneline.csv / results.csv 행이 있으면).
set -u
PHASES=${PHASES:-"worlds a b1 b2 b3 b4 c sum"}
JOBS=${JOBS:-8}; THREADS=${THREADS:-8}
OUT=${OUT:-ml/out/v6}; V6=ml/experiments/v6; PY=${PY:-python3}
PERSONS=${PERSONS:-400}; RIDES=${RIDES:-20}; VEHICLES=${VEHICLES:-20}; TRIPS=${TRIPS:-48}; STEPS_A=${STEPS_A:-3000}; STEPS_B=${STEPS_B:-8000}; ADAPT=${ADAPT:-24}   # 연습용으로 줄일 수 있음
mkdir -p $OUT; LOG=$OUT/run_v6.log
say() { echo "[$(date +%H:%M:%S)] $*" | tee -a $LOG; }
has() { case " $PHASES " in *" $1 "*) return 0;; *) return 1;; esac; }

world_args() { echo "--base $OUT/$1/base.npz --fleet $OUT/$1/fleet.npz"; }

ctx() {  # ctx <world> <input> <stage1> <stage2> <k> <seed> [extra args...]
  local w=$1 inp=$2 s1=$3 s2=$4 k=$5 seed=$6; shift 6
  local d=$OUT/$w/context_${inp}_${s1}-${s2}_k${k}_s${seed}
  if [ -f "$d/oneline.csv" ]; then say "건너뜀 $d"; return; fi
  local body=$OUT/$w/axis_a/${s1}_${inp}_s0.pt; local bodyarg=""; [ -f "$body" ] && bodyarg="--body $body"   # 축 A 몸통이 있으면 재사용, 없으면 캐시에 학습
  say "시작 $w $inp $s1-$s2 k$k 시드 $seed"
  $PY $V6/context6.py $(world_args $w) --cache $OUT/$w/cache --input $inp --stage1 $s1 --stage2 $s2 --k $k --seed $seed --threads $THREADS \
      $bodyarg --steps $STEPS_B --adapt-trips $ADAPT "$@" > $OUT/$w/ctx_${inp}_${s1}-${s2}_k${k}_s${seed}.log 2>&1 || say "실패 $w $inp $s1-$s2 k$k s$seed (로그 $OUT/$w/ctx_${inp}_${s1}-${s2}_k${k}_s${seed}.log)"
}

if has worlds; then
  for w in weak strong; do
    mkdir -p $OUT/$w
    [ -f $OUT/$w/base.npz ]  || { say "세계 생성 base/$w";  $PY $V6/make_world6.py base  --persons $PERSONS --rides $RIDES --habit $w --seed 0 --jobs $JOBS --out $OUT/$w/base.npz  > $OUT/$w/base.log 2>&1 || say "실패 base/$w"; }
    [ -f $OUT/$w/fleet.npz ] || { say "세계 생성 fleet/$w"; $PY $V6/make_world6.py fleet --vehicles $VEHICLES --trips $TRIPS --habit $w --seed 5 --jobs $JOBS --out $OUT/$w/fleet.npz > $OUT/$w/fleet.log 2>&1 || say "실패 fleet/$w"; }
    [ -f $OUT/$w/base_check.png ] || $PY $V6/check_world6.py $OUT/$w/base.npz > $OUT/$w/base_check.txt 2>&1 || true
  done
fi

if has a; then
  for w in weak strong; do
    say "축 A $w"
    $PY $V6/axis_a.py $(world_args $w) --out $OUT/$w/axis_a --threads $THREADS --steps $STEPS_A --adapt-trips $ADAPT >> $OUT/$w/axis_a.log 2>&1 || say "실패 축 A $w"
    $PY $V6/axis_a.py $(world_args $w) --out $OUT/$w/axis_a --threads $THREADS --steps $STEPS_A --adapt-trips $ADAPT --inputs v6 --models mlp,ft --seeds 1,2 >> $OUT/$w/axis_a.log 2>&1 || say "실패 축 A 시드 $w"
  done
fi

if has b1; then for w in strong weak; do for s2 in gru attn gru_qfirst mean_dev; do ctx $w v6 ft $s2 128 0; done; for s2 in gru attn; do ctx $w v6 mlp $s2 128 0; done; done; fi
if has b2; then for w in strong weak; do for s2 in gru attn; do ctx $w v6 ft $s2 32 0; done; done; fi
if has b3; then for w in strong weak; do for s2 in gru attn; do ctx $w v6 ft $s2 256 0; done; done; fi
if has b4; then for w in strong weak; do for seed in 1 2; do for s2 in gru attn; do ctx $w v6 ft $s2 128 $seed; done; done; done; fi
if has c; then for w in strong weak; do for s2 in gru attn; do ctx $w v5 ft $s2 128 0; done; done; fi
if has sum; then
  $PY $V6/summarize6.py weak=$OUT/weak strong=$OUT/strong --out $OUT/summary_bc.md > /dev/null 2>&1 && say "요약 → $OUT/summary_bc.md"
  for w in weak strong; do [ -f $OUT/$w/axis_a/results.csv ] && $PY $V6/axis_a.py --summarize $OUT/$w/axis_a > /dev/null 2>&1 && say "축 A 요약 → $OUT/$w/axis_a/summary.md"; done
fi
say "끝 (PHASES=$PHASES)"
