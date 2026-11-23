#!/bin/sh
# 주행 끝내기: 로그 파일을 정상으로 닫고 모든 프로그램을 내린다.  sh /data/home/qnxuser/mermaid/rf-log/controller/bringup/stop.sh
REPO=/data/home/qnxuser/mermaid/rf-log
slay -s INT p1_acq 2>/dev/null
echo "주행 종료 중 (10초)..."
sleep 10
slay -f p1_acq p2_feat p3_infer p4_policy p5_devmgr p6_comm p7_logger 2>/dev/null
sleep 1
R=$(ls -td "$REPO"/out/drive_*/ 2>/dev/null | head -1)
n=$(pidin -F "%N" | grep -E "p[1-7]_" | sort -u | wc -l | tr -d ' ')
echo "남은 프로그램: $n"
ls "$R"logs/rides/ 2>/dev/null
ls "$R"logs/rides/*.open.sqlite > /dev/null 2>&1 && echo "주의: 아직 닫히지 않은 파일이 있습니다 (.open)" || echo "로그 파일 정상으로 닫힘: $R"
