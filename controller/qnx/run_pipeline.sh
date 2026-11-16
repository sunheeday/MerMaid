#!/bin/sh
# P4, P3, P2를 띄우고 P1으로 trip.bin 하나를 재생한 뒤 모두 끝날 때까지 기다린다 (저장소 루트에서 실행).
#   controller/qnx/run_pipeline.sh <trip.bin> <out_dir> <speed> <vehicle_type> [ID:MSSQ:SENS0[:EV]]...
# speed 1 = 실시간, 0 = 쉬지 않고. 결과: <out_dir>/cycles.csv, p1~p4.out(.err), state/
# 매번 새 state로 시작한다 (허용도 0). 이어서 쓰려면 KEEP_STATE=1
# 모델: MODELS (기본 ml/out/runs/<CURRENT>/models, ml/runs.py use로 정함)
B=${BUILD:-build}
MODELS=${MODELS:-ml/out/runs/$(cat ml/out/runs/CURRENT 2>/dev/null)/models}
[ -f "$MODELS/models.json" ] || { echo "models not found: $MODELS (ml/runs.py use vNNN)" >&2; exit 2; }
trip=$1; out=$2; speed=$3; vt=$4
shift 4
[ -n "$vt" ] || { echo "usage: $0 <trip.bin> <out_dir> <speed> <vehicle_type> [phone]..." >&2; exit 2; }
mkdir -p "$out"
[ -n "$KEEP_STATE" ] || rm -rf "$out/state"
phones=""
for p in "$@"; do phones="$phones --phone $p"; done
if [ "$speed" = "0" ]; then t=${TIMEOUT_MS:-1000}; else t=${TIMEOUT_MS:-5}; fi

$B/p4_policy --config controller/config --state "$out/state" --log "$out/cycles.csv" --hud $phones --once \
    > "$out/p4.out" 2> "$out/p4.err" &
p4=$!
$B/p3_infer --models "$MODELS" --vehicle-type "$vt" --once > "$out/p3.out" 2> "$out/p3.err" &
p3=$!
$B/p2_feat --once > "$out/p2.out" 2> "$out/p2.err" &
p2=$!
$B/p1_acq "$trip" --speed "$speed" --timeout-ms "$t" > "$out/p1.out" 2> "$out/p1.err"
r1=$?
wait $p2; r2=$?
wait $p3; r3=$?
wait $p4; r4=$?
echo "exit codes: p1=$r1 p2=$r2 p3=$r3 p4=$r4"
cat "$out/p1.out" "$out/p2.out" "$out/p3.out"
grep -v '^CMD' "$out/p4.out"
grep '^CMD' "$out/p4.out" | head -20
for f in "$out"/p*.err; do [ -s "$f" ] && { echo "--- $f"; cat "$f"; }; done
[ $r1 -eq 0 ] && [ $r2 -eq 0 ] && [ $r3 -eq 0 ] && [ $r4 -eq 0 ]
