#!/bin/sh
# LIVE: 실제 IMU(MPU-6050)로 P1~P6을 띄운다 (보드에서, 저장소 루트에서 실행).
#   controller/qnx/run_live.sh <out_dir> <vehicle_type>
# Ctrl-C를 누르면 P1이 RIDE_END를 보내고 끝난 뒤, 나머지 프로세스를 내린다. 결과: <out_dir>/cycles.csv, p*.out, p*.err, state/
# 환경 변수
#   FAKE_IMU=1     가짜 IMU (IMU 없이 경로 시험)          DURATION_S=N  N초 주행 후 자동으로 끝
#   RF=0           P5·P6 없이 (P4 --hud 고정 등록)        SER=/dev/ser3 ESP32 UART
#   I2C=/dev/i2c1  IMU 버스                               MODELS=…      기본 ml/out/runs/<CURRENT>/models
# 시작 순서: P6 → P5 → (P7 로거: 3단계에서 추가) → P4 → P3 → P2 → P1. LIVE는 센서 오류 뒤 새 주행이 이어질 수 있어서
# P2~P4를 --once 없이 띄운다. 상태(state/)는 지우지 않는다 (자이로 바이어스, 허용도, 프로필이 이어짐).
B=${BUILD:-build}
MODELS=${MODELS:-ml/out/runs/$(cat ml/out/runs/CURRENT 2>/dev/null)/models}
[ -f "$MODELS/models.json" ] || { echo "models not found: $MODELS (ml/runs.py use vNNN)" >&2; exit 2; }
out=$1; vt=$2
[ -n "$vt" ] || { echo "usage: $0 <out_dir> <vehicle_type>" >&2; exit 2; }
mkdir -p "$out/state"
trap '' INT  # Ctrl-C는 P1(앞에서 도는 프로세스)만 받는다. 이 스크립트는 정리를 위해 남는다

pids=""
rf4="--hud"; rf2=""
if [ "${RF:-1}" != "0" ]; then
    rf4="--rf"; rf2="--motion"
    $B/p6_comm --dev "${SER:-/dev/ser3}" > "$out/p6.out" 2>&1 &
    pids="$pids $!"
    sleep 1
    $B/p5_devmgr --state "$out/state" > "$out/p5.out" 2>&1 &
    pids="$pids $!"
    sleep 1
fi
$B/p4_policy --config controller/config --state "$out/state" --log "$out/cycles.csv" $rf4 > "$out/p4.out" 2> "$out/p4.err" &
pids="$pids $!"
[ "$rf4" = "--rf" ] && sleep 2  # P6이 기억해 둔 SCR·PROF가 P4에 닿을 때까지
$B/p3_infer --models "$MODELS" --vehicle-type "$vt" > "$out/p3.out" 2> "$out/p3.err" &
pids="$pids $!"
$B/p2_feat $rf2 > "$out/p2.out" 2> "$out/p2.err" &
pids="$pids $!"

fake=""; [ -n "$FAKE_IMU" ] && [ "$FAKE_IMU" != "0" ] && fake="--fake-imu"
dur=""; [ -n "$DURATION_S" ] && dur="--duration-s $DURATION_S"
echo "LIVE: Ctrl-C to end the ride"
$B/p1_acq --live --i2c "${I2C:-/dev/i2c1}" --state "$out/state" $fake $dur > "$out/p1.out" 2> "$out/p1.err"
r1=$?
sleep 1  # RIDE_END가 P2 → P3 → P4로 지나가게
for p in $pids; do kill "$p" 2>/dev/null; done
wait 2>/dev/null
echo "p1 exit code: $r1"
cat "$out/p1.out"
for f in "$out"/p*.err; do [ -s "$f" ] && { echo "--- $f"; tail -20 "$f"; }; done
[ $r1 -eq 0 ]
