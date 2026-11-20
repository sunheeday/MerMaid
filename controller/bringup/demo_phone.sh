#!/bin/sh
# 폰 시연: 실제 ESP32 + 폰 앱으로 Tier 1·2·3이 켜지는 것을 본다 (저장소 루트에서, qnxuser로).
#   controller/bringup/demo_phone.sh [초 (기본 600)] [위험 확률 (기본 0.95)]
# - 위험 확률은 모델 대신 fake_p3가 고정값으로 낸다 (데이터 조작). 주행 데이터는 make_demo_trip.py가 만든 흔들림 큰 가짜 주행
# - HUD 기기가 없어도 Tier 1·2를 보이게 P4를 --rf --hud로 띄운다 (송풍·디스플레이를 고정 등록, HUD 화면 켜짐으로 봄)
# - 정책은 그대로: Tier 1은 10초, Tier 2는 60초, Tier 3은 120초 연속 위험이면 켜짐. Tier 3은 폰 화면이 켜져 있고 잠금이 풀려 있어야 함
# 먼저: UART3 (`sudo sh controller/bringup/uart3.sh`, 재부팅마다), ESP32 전원과 Pi 핀 7·29·9 연결
# 결과: out/demo/ (p1~p6 로그, cycles.csv)
set -e
SECS=${1:-600}
P=${2:-0.95}
WAIT=${WAIT:-10}  # P5·P6을 띄운 뒤 주행 시작까지 (폰을 붙일 시간)
B=${BUILD:-build}
O=out/demo
SER=${SER:-/dev/ser3}
[ -e "$SER" ] || { echo "$SER 없음: sudo sh controller/bringup/uart3.sh" >&2; exit 2; }
for x in p1_acq p2_feat fake_p3 p4_policy p5_devmgr p6_comm; do
    [ -x "$B/$x" ] || { echo "$B/$x 없음 (cmake --build build)" >&2; exit 2; }
done
slay -f p1_acq p2_feat fake_p3 p3_infer p4_policy p5_devmgr p6_comm rf_stub 2>/dev/null || true
rm -rf "$O"
mkdir -p "$O/state"
python3 controller/tests/make_demo_trip.py "$O/trip.bin" --seconds "$SECS"

$B/p6_comm --dev "$SER" > "$O/p6.out" 2>&1 &
sleep 1
$B/p5_devmgr --state "$O/state" > "$O/p5.out" 2>&1 &
sleep 1
echo "P5·P6 up. 폰 앱을 열어 차(MSAgent)와 연결하세요. ${WAIT}초 뒤 주행 시작"
sleep "$WAIT"
grep -q "hello conn" "$O/p5.out" && grep "hello conn" "$O/p5.out" || echo "(아직 등록된 기기 없음. 주행 중에 붙어도 10초 안에 등록됩니다)"

$B/p4_policy --config controller/config --state "$O/state" --log "$O/cycles.csv" --rf --hud --once \
    > "$O/p4.out" 2> "$O/p4.err" &
sleep 2  # P6이 기억한 SCR·PROF를 P4에 다시 보낼 때까지
$B/fake_p3 --p "$P,$P,$P,$P,$P" --once > "$O/p3.out" 2>&1 &
$B/p2_feat --once --motion > "$O/p2.out" 2> "$O/p2.err" &
echo "주행 시작 ($SECS 초, 위험 확률 $P): Tier 1 약 10초, Tier 2 약 60초, Tier 3 약 120초 뒤"
$B/p1_acq "$O/trip.bin" --speed 1 --timeout-ms 5 > "$O/p1.out" 2> "$O/p1.err"
sleep 2
slay -f p5_devmgr p6_comm 2>/dev/null || true
echo "끝. 명령:"
grep -E '^(CMD|REG)' "$O/p4.out" || true
