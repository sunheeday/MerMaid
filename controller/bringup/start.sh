#!/bin/sh
# 실차·시연 시작 한 줄: UART3 켜기 → 남은 프로그램 정리 → P6→P5→P7→P4→P3→P2→P1 → 이미 붙어 있던 폰 다시 등록
#   su  →  sh /data/home/qnxuser/mermaid/rf-log/controller/bringup/start.sh [분 (기본 120)]
# 실제 IMU, 모델 MODELS(기본 v007), 주행 로그(P7), HUD가 연결돼 있다고 보고 송풍·앰비언트도 켬 (HUD=0이면 끔).
# 결과: out/drive_<월일_시분초>/ (p*.out, cycles.csv, logs/rides/*.sqlite). 끝내기: stop.sh
# root가 아니면 UART3를 켤 수 없다 (이미 켜져 있으면 qnxuser로도 됨).
MIN=${1:-120}
REPO=/data/home/qnxuser/mermaid/rf-log
MODELS=${MODELS:-/data/home/qnxuser/mermaid/repo/ml/out/runs/v007/models}
if [ ! -e /dev/ser3 ]; then
    if [ "$(id -u)" = "0" ]; then
        echo "UART3 켜는 중..."
        sh /data/home/qnxuser/uart3.sh > /dev/null 2>&1
    else
        echo "UART3(/dev/ser3)가 꺼져 있습니다. su 로 root가 된 뒤 다시 실행하세요"; exit 1
    fi
fi
[ -e /dev/ser3 ] || { echo "UART3 켜기 실패 (/dev/ser3 없음)"; exit 1; }
[ -f "$MODELS/models.json" ] || { echo "모델 없음: $MODELS"; exit 1; }
cd "$REPO" || exit 1
slay -f p1_acq p2_feat p3_infer p4_policy p5_devmgr p6_comm p7_logger 2>/dev/null
sleep 1
R=out/drive_$(date +%m%d_%H%M%S)
echo "센서를 2초 동안 움직이지 마세요 (자이로 보정)"
HUD=${HUD:-1} RF=1 MODELS="$MODELS" LOGS=$R/logs DURATION_S=$((MIN * 60)) \
    nohup sh controller/qnx/run_live.sh "$R" 1 > "$R.run.log" 2>&1 &
sleep 15
# 폰이 P6보다 먼저 브리지에 붙어 있으면 등록되지 않는 문제(수정 전)를 피하려고 0번 연결을 한 번 끊는다
echo "0|DROP" > /dev/ser3
sleep 5
n=$(pidin -F "%N" | grep -E "p[1-7]_" | sort -u | wc -l | tr -d ' ')
echo "------------------------------------------"
echo "프로그램 $n/7 실행 중"
grep -q RIDE_START "$R/p1.err" 2>/dev/null && echo "주행 기록 시작됨 ($(date))" || echo "주행 시작 대기 중: 센서를 가만히 두세요 (확인: cat $REPO/$R/p1.err)"
grep "REG" "$R/p4.out" 2>/dev/null || echo "아직 등록된 기기 없음 (폰 앱을 열어 연결)"
echo "결과 폴더: $REPO/$R  (주행 $MIN분)"
[ "$n" = "7" ] || { echo "실행 실패. 로그: $REPO/$R.run.log"; exit 1; }
