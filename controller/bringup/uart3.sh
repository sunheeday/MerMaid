#!/bin/sh
# Pi 4 UART3 (/dev/ser3, 핀 7 TX · 29 RX · 9 GND)를 켠다. 재부팅마다 root로 한 번 (bridge/README.md와 같은 순서).
#   sudo sh controller/bringup/uart3.sh
# 끝나면 /dev/ser3를 qnxuser도 열 수 있게 권한을 연다 (p6_comm을 root 없이 띄우려고).
# 드라이버 파일: 부팅 이미지에 없어서 SDP에서 복사해 둔 /data/home/qnxuser/devc-serpl011
set -e
DRV=${DRV:-/data/home/qnxuser/devc-serpl011}
[ "$(id -u)" = "0" ] || { echo "root로 실행: sudo sh $0" >&2; exit 1; }
[ -f "$DRV" ] || { echo "드라이버 없음: $DRV" >&2; exit 1; }
if [ -e /dev/ser3 ]; then
    echo "/dev/ser3 이미 있음 (드라이버를 다시 띄우려면 slay devc-serpl011 후 재실행)"
else
    slay -f devc-serpl011 2>/dev/null || true
    sleep 1
    cp "$DRV" /tmp/devc-serpl011
    chmod +x /tmp/devc-serpl011
    gpio-bcm2711 set 4,5 a4          # 핀 mux (쉼표 뒤 띄어쓰기 금지)
    /tmp/devc-serpl011 -b115200 -v -c50000000 -e -F -u3 0xfe201600,153
    n=0
    while [ ! -e /dev/ser3 ] && [ $n -lt 20 ]; do sleep 0.2; n=$((n + 1)); done
fi
[ -e /dev/ser3 ] || { echo "/dev/ser3가 생기지 않음" >&2; exit 1; }
stty -echo < /dev/ser3
chmod 666 /dev/ser3
ls -la /dev/ser*
