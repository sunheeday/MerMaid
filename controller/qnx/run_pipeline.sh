#!/bin/sh
# P4, P3, P2를 띄우고 P1으로 trip.bin 하나를 재생한 뒤 모두 끝날 때까지 기다린다 (저장소 루트에서 실행).
#   controller/qnx/run_pipeline.sh <trip.bin> <out_dir> <speed> <vehicle_type> [ID:MSSQ:SENS0[:EV]]...
# speed 1 = 실시간, 0 = 쉬지 않고. 결과: <out_dir>/cycles.csv, p1~p4.out(.err), state/
# 매번 새 state로 시작한다 (허용도 0, V3 승객 토큰 state/ctx 없음). 이어서 쓰려면 KEEP_STATE=1 (V3 문맥을 여러 주행에 쌓을 때)
# 모델: MODELS (기본 ml/out/runs/<CURRENT>/models, ml/runs.py use로 정함). 정책 설정: CONFIG (기본 controller/config,
#   V3 ctx_gru 모델이면 thresholds.json이 kind response3인 폴더)
# RF=1: P6(UART, 기본 /dev/ser3 = SER)와 P5를 먼저 띄우고 P4는 --rf (P5 명부로 등록, TIER_CMD), P2는 --motion.
#   폰·HUD는 브리지로 붙고 [phone]...은 비워도 된다. P5·P6은 주행이 끝나면 내린다. 결과: p5.out, p6.out 추가
#   UART3는 root로 firmware/bridge/README.md의 순서대로 켜 둔다. ESP32 없이 P5만 보려면 RF=1 P6=0
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
rf4=""; rf2=""; p5=""; p6=""
if [ -n "$RF" ] && [ "$RF" != "0" ]; then
    rf4="--rf"; rf2="--motion"
    mkdir -p "$out/state"
    if [ "${P6:-1}" != "0" ]; then
        $B/p6_comm --dev "${SER:-/dev/ser3}" > "$out/p6.out" 2>&1 &
        p6=$!
        sleep 1
    fi
    $B/p5_devmgr --state "$out/state" > "$out/p5.out" 2>&1 &
    p5=$!
    sleep 1
    [ -n "$RF_WAIT" ] && { echo "RF up: connect phones / HUD now, starting in $RF_WAIT s"; sleep "$RF_WAIT"; }
fi

hud="--hud"; [ -n "$rf4" ] && hud=""
$B/p4_policy --config "${CONFIG:-controller/config}" --state "$out/state" --log "$out/cycles.csv" $hud $rf4 $phones --once \
    > "$out/p4.out" 2> "$out/p4.err" &
p4=$!
# RF: P6이 기억해 둔 SCR·PROF를 P4에 다시 보낼 때까지 (못 보냈으면 1초마다). 빠른 재생은 첫 주기가 바로 오므로
[ -n "$rf4" ] && sleep 2
$B/p3_infer --models "$MODELS" --ctx-state "$out/state/ctx" --vehicle-type "$vt" --once > "$out/p3.out" 2> "$out/p3.err" &
p3=$!
$B/p2_feat --once $rf2 > "$out/p2.out" 2> "$out/p2.err" &
p2=$!
$B/p1_acq "$trip" --speed "$speed" --timeout-ms "$t" > "$out/p1.out" 2> "$out/p1.err"
r1=$?
wait $p2; r2=$?
wait $p3; r3=$?
wait $p4; r4=$?
[ -n "$p5" ] && kill $p5 2>/dev/null
[ -n "$p6" ] && kill $p6 2>/dev/null
echo "exit codes: p1=$r1 p2=$r2 p3=$r3 p4=$r4"
cat "$out/p1.out" "$out/p2.out" "$out/p3.out"
grep -v '^CMD' "$out/p4.out"
grep '^CMD' "$out/p4.out" | head -20
for f in "$out"/p*.err; do [ -s "$f" ] && { echo "--- $f"; cat "$f"; }; done
[ $r1 -eq 0 ] && [ $r2 -eq 0 ] && [ $r3 -eq 0 ] && [ $r4 -eq 0 ]
