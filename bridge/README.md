# ESP32-S3 BLE 브리지 (`mermaid_bridge`)

Pi 4의 QNX BSP에는 블루투스 드라이버가 없어서, ESP32-S3가 BLE를 맡고 Pi와는 UART로 줄 단위 글자를 주고받습니다.

```
폰·HUD ──BLE── ESP32-S3 ──UART3── Pi P6 ── P5 ── P4
```

프로토콜(프레임, UUID, 메시지)은 `protocol/PROTOCOL.md`가 기준입니다. 이 폴더는 그 구현입니다.

## 보드와 빌드

| 항목 | 값 |
|---|---|
| 보드 | ESP32-S3-DevKitC-1 N16R8 |
| Arduino IDE 보드 설정 | ESP32S3 Dev Module, Flash 16MB, PSRAM Disabled |
| 코어 / 라이브러리 | Arduino-ESP32 3.3.12, NimBLE-Arduino 2.x |
| 업로드 포트 | 보드의 "UART" USB 포트 (맥 전원 겸 로그) |

명령줄로 굽기 (Arduino IDE 대신, 2026-11-18 v7, 2026-11-19 v8을 이렇게 구움. Arduino IDE로 이미 설치한 코어·라이브러리가 있으면 그대로 씀):

```
brew install arduino-cli
arduino-cli config set board_manager.additional_urls https://espressif.github.io/arduino-esp32/package_esp32_index.json
arduino-cli core update-index && arduino-cli core install esp32:esp32@3.3.12
arduino-cli lib install NimBLE-Arduino            # 2.5.2로 시험
arduino-cli compile -b esp32:esp32:esp32s3:FlashSize=16M,PSRAM=disabled --build-path /tmp/bridge_build bridge/mermaid_bridge
arduino-cli upload  -b esp32:esp32:esp32s3:FlashSize=16M,PSRAM=disabled -p /dev/cu.usbserial-0001 --input-dir /tmp/bridge_build bridge/mermaid_bridge
```

**동시 연결 수:** NimBLE 기본값은 3입니다. 라이브러리의 `src/nimconfig.h`에서 `CONFIG_BT_NIMBLE_MAX_CONNECTIONS`를 바꿉니다 (현재 3, 프로토콜은 최대 9). 라이브러리를 업데이트하면 이 값이 초기화되니 다시 확인합니다.

## 배선

| Pi 4 | ESP32-S3 |
|---|---|
| 핀 7 (GPIO4, UART3 TX) | GPIO18 (RX) |
| 핀 29 (GPIO5, UART3 RX) | GPIO17 (TX) |
| 핀 9 (GND) | GND |

ESP32 전원은 맥 USB에서 받습니다. Pi 5 V와 동시에 넣지 않습니다.

## Pi 쪽 UART3 켜기 (재부팅마다, root)

```
slay -f devc-serpl011 2>/dev/null
sleep 1
cp /data/home/qnxuser/devc-serpl011 /tmp/devc-serpl011   # 부팅 이미지에 없어서 SDP에서 복사해 둔 드라이버
chmod +x /tmp/devc-serpl011
gpio-bcm2711 set 4,5 a4                                   # 핀 mux (쉼표 뒤 띄어쓰기 금지)
/tmp/devc-serpl011 -b115200 -v -c50000000 -e -F -u3 0xfe201600,153
stty -echo < /dev/ser3
ls /dev/ser*                                              # /dev/ser1 /dev/ser3
```

`/dev/ser3`는 한 프로그램만 열 수 있습니다.

## 동작

- 연결 번호 0~8을 직접 배정합니다. 연결되면 브리지가 먼저 보안 요청을 보내고(v7), **암호화가 끝난 뒤** Pi에 `<id>|CONNECT`. 15초 안에 암호화가 안 되면 끊습니다. 끊김 → `<id>|DISCONNECT` (CONNECT를 보낸 연결만)
- 폰이 RX에 쓴 줄 → Pi에 `<id>|<줄>` (Long Write 최대 512 B, `\n`마다 나눔)
- Pi의 `<id>|<payload>` → 그 연결에 Notify, `ALL|<payload>` → 모두에게, `<id>|DROP` → 그 연결을 끊음
- MTU에 맞춰 Notify를 나눠 보냅니다 (MTU 247 → 244 B씩). 512 B 넘는 payload는 버립니다
- 보안: LE Secure Connections, Just Works, 본딩을 플래시에 저장. RX·TX 모두 암호화 필수. 암호화 전 연결에는 Notify도 보내지 않습니다
- RX는 응답 없는 쓰기와 응답 있는 쓰기를 모두 받습니다 (v7). 암호화 전에 응답 있는 쓰기를 하면 '암호화 필요' 에러가 돌아가 기기 쪽에서도 페어링이 시작됩니다
- 본딩된 기기가 다시 붙으면 저장된 키로 바로 암호화되어 '암호화 완료' 알림이 '연결됨' 알림보다 먼저 올 수 있습니다. 이때도 연결 알림에서 바로 `CONNECT`를 보냅니다 (v8, 로그 `already encrypted (bonded reconnect)`). 브리지가 끊는 중인 연결(15초 초과, DROP)에는 늦게 온 암호화 알림으로 `CONNECT`를 보내지 않습니다
- v7의 문제: 위 순서가 뒤집히면 번호 없는 연결로 보고 무시해서, 본딩된 기기의 재연결이 Pi에 알려지지 않고 15초 뒤 끊겼습니다. 맥처럼 이미 본딩된 기기는 첫 연결부터 자주 이렇게 됩니다
- v6까지의 문제: 페어링을 시작하게 하는 것이 없었습니다 (브리지 보안 요청 없음, RX가 응답 없는 쓰기뿐이라 거절 에러도 없음). nRF Connect처럼 손으로 본딩하지 않는 기기(맥 Chrome, 앱)의 HELLO는 조용히 버려지고 Pi가 5초 뒤 DROP 했습니다
- 광고 이름 `MSAgent-xxxx` (MAC 마지막 2바이트)

### 개발용 USB 입력

Pi 없이 맥이 Pi 역할을 할 수 있습니다. USB 시리얼(115,200)에 `0|{"t":"INT","tier":3,"on":true}`처럼 치면 Pi에서 온 줄처럼 처리합니다. 폰에서 Pi로 가는 줄은 USB에도 `<< ` 접두어로 보입니다. 10초마다 `[STAT] up …` 한 줄이 나옵니다.

```
screen /dev/cu.usbserial-* 115200   # 닫기: Ctrl+A, k, y
```

## 검증한 것

- Pi ↔ ESP32 UART 1,000줄 왕복 유실 0
- 폰(nRF Connect) 연결·본딩(재시작 후 유지), MTU 247, Pi → 폰 400 B = 2조각, 600 B 거절, 폰 → Pi 300 B Long Write
- 10 Hz MOT + 1 Hz HB 2분: 1,321줄 유실 0, Notify 실패 0
- v7 (2026-11-18): 맥(Python bleak, 터미널 앱)이 손으로 본딩하지 않고 연결 → 브리지 보안 요청 → `encrypted=1 bonded=1` → HELLO가 Pi P5까지. Pi P1~P6을 실제 ESP32로 돌려 Tier 1·2·3 INT와 10 Hz MOT를 맥에서 받음 (데모 스크립트는 `s0-protocol` 브랜치)
- v8 (2026-11-19): 이미 본딩된 맥(`bridge/tools/ble_check.py`)으로 Pi P6 + P5 + `rf_stub --cycle --motion` 시험. v7은 재연결이 Pi에 알려지지 않았고(15초 뒤 끊김), v8은 첫 연결·재연결 모두 바로 `CONNECT` → HELLO 등록, Tier 1·2·3 INT, MOT 10 Hz, 수동 OFF, 남의 폰 OVR 거부, 재연결 상태 재전송, 무응답 5초 DROP까지 통과

## 남은 것

- 2대 동시 연결, HUD(Chrome Web Bluetooth)와 Android 앱으로 v8 페어링·재연결 확인
- 동시 연결 수 9 시험
- (선택) UART 수신 줄 512 B 상한, 데모용 USB 입력 끄기 `#define`, 30분 연속 시험
