# controller/bringup — 하드웨어 동작 확인용 테스트

컨트롤러(P1~P6)와 별개로, 보드에서 센서·장치가 동작하는지 따로 확인하는 독립 프로그램을 둡니다.

## mpu_test.c — MPU-6050 (I2C)

WHO_AM_I(0x75)를 읽어 칩을 확인하고, 설계 §4.3의 레지스터 설정으로 100 Hz 고정 주기로 14바이트(가속도 + 온도 + 자이로)를 읽습니다.

- `./mpu`: 10 Hz로 솎아서 화면 출력 (g, dps, °C, 샘플 간격)
- `./mpu --csv > imu.csv`: 매 샘플 CSV `t_ns,ax,ay,az,gx,gy,gz,dt_us` (m/s², dps)

### 배선 (Pi 4)

| MPU-6050 | Pi 4 |
|---|---|
| VCC | 3.3 V (핀 1) |
| GND | GND (핀 6) |
| SDA | GPIO 2 (핀 3) |
| SCL | GPIO 3 (핀 5) |
| XDA, XCL, AD0, INT | 연결 안 함 (AD0 미연결 → 주소 0x68) |

### 빌드·실행 (Pi에서 직접)

QSTI 이미지에 `gcc`가 있어(`/system/bin/gcc`) 보드에서 바로 빌드합니다. `qnxuser`는 `i2c` 그룹이라 root 없이 `/dev/i2c1`에 접근됩니다.

Mac에서 SSH로 보내 실행 (Mac 인터넷 공유로 연결 시 Pi = `192.168.2.2`):

```sh
scp controller/bringup/mpu_test.c qnxuser@192.168.2.2:mermaid/
ssh qnxuser@192.168.2.2 'cd ~/mermaid && gcc -Wall -o mpu mpu_test.c && ./mpu'   # Ctrl+C로 종료
```

정상이면 정지 상태에서 가속도 크기 √(ax²+ay²+az²) ≈ 1.0 g이고, 수평으로 놓으면 Z ≈ 1.0 g입니다.

### 결과

**2026-11-05, 1차 (가속도 6바이트, 약 5 Hz)**: Pi 화면에서 실행해 X, Y, Z 출력 확인.

**2026-11-05, 2차 (14바이트, 100 Hz, 정지 상태 10초)** — 원본: [`data/recordings/2026-11-05_static_bringup.csv`](../../data/recordings/2026-11-05_static_bringup.csv)

| 항목 | 결과 | 판정 |
|---|---|---|
| WHO_AM_I | **0x72** (MPU-6050이면 0x68) | 확인 필요 (아래) |
| 가속도 평균 (g) | X −0.60, Y 0.61, Z −0.51 | 센서가 기울어져 있음 |
| 가속도 크기 | 0.9996 g | 정상 |
| 가속도 노이즈 (표준편차) | 약 0.013–0.019 m/s² | 정상 |
| 자이로 평균 (dps) | X −0.71, Y 3.66, Z −0.34 | 정지 오프셋, 보정 필요 |
| 자이로 노이즈 (표준편차) | 약 0.05 dps | 정상 |
| 샘플 간격 | 평균 9999.5 µs, 표준편차 2.0 µs, 범위 9994–10006 µs, 11 ms 초과 0회 (984샘플) | 100 Hz 안정 |

- **WHO_AM_I = 0x72**: 정품 MPU-6050(0x68)이 아니라 호환 칩(클론)으로 보입니다. 가속도 크기가 1 g로 맞고 자이로·온도 값도 그럴듯해서, 레지스터 맵은 MPU-6050과 같게 동작합니다. 모듈 표기와 판매 정보로 칩을 확인해 두세요.
- **기울기**: 수평 장착이 아니라 Z가 1 g가 아닙니다. 설계 §4.5는 수평 장착(z축 위, x 전방, y 좌측)을 전제로 하므로 실제 장착 시 수평으로 고정하거나 정지 중력으로 자세를 보정해야 합니다.
- **자이로 오프셋**: Y축이 약 3.7 dps 치우쳐 있습니다. Phase 1은 자이로를 쓰지 않지만, 쓸 때는 시작 시 정지 평균을 빼는 보정이 필요합니다.

### 겪은 문제

- 보드의 `vi`가 st 터미널에서 잘 동작하지 않음 → `cat > file << 'EOF'`로 작성하거나 PC에서 `scp`로 전송
- `qcc`는 보드에 없음 (PC SDP용). 보드에서는 `gcc` 사용
- QNX `sed`는 `-i`를 지원하지 않음 → `sed '...' a.c > b.c && mv b.c a.c`

## P3 추론과 AC-01 / Q9 (2026-11-17 모델 v007 · 피처 v3, QNX 8.0, clang 22.1.8, lib_lightgbm 4.6.0 보드 빌드)

Mac에서 코드, 배포 중인 모델 버전(`ml/runs.py`의 CURRENT), 벡터를 보낸 뒤 보드에서 빌드합니다. `V`는 그 버전 폴더입니다.

```bash
V=$(python3 ml/runs.py current) && python3 ml/verify/model_parity.py make
COPYFILE_DISABLE=1 tar --no-xattrs --no-mac-metadata -czf - controller protocol ml/out/runs/CURRENT $V/models $V/parity/vectors.bin | ssh qnxuser@192.168.2.2 'mkdir -p ~/mermaid/repo && cd ~/mermaid/repo && tar xzf -'
ssh qnxuser@192.168.2.2 'cd ~/mermaid/repo && L=$HOME/mermaid/LightGBM && cmake -S controller -B build -DMM_LIGHTGBM_INCLUDE=$L/include -DMM_LIGHTGBM_LIB=$L/lib_lightgbm.so && cmake --build build && ctest --test-dir build && ./build/verify_model $V/models $V/parity/vectors.bin $V/parity/actual_pi.bin mem'
scp qnxuser@192.168.2.2:mermaid/repo/$V/parity/actual_pi.bin $V/parity/ && python3 ml/verify/model_parity.py check $V/parity/actual_pi.bin
```

| 항목 | v007 (2026-11-17) | 이전 모델 (2026-11-11, T-22 이전) | 기준 |
|---|---|---|---|
| AC-01 확률 차이 (4,202행: 데이터 2,000, NaN 201, 0 201, 분기 기준값 ±1e-9와 정확히 그 값 1,800) | 최대 2.2e-16 | 최대 2.2e-16 | < 1e-6 |
| AC-01 Tier 결정 | 100% 일치 | 100% 일치 | 100% |
| Q9 모델 5개 로드 | 95 ms (트리 224, 102, 100, 100, 100) | 126 ms (트리 146, 98, 54, 17, 271) | 1초 이내 |
| Q9 한 주기 추론 (폰 9대 = 9행 × 모델 5개) | 중앙값 0.85 ms, 최대 0.90 ms | 중앙값 1.48 ms, 최대 2.0 ms | 10 ms 이내 |
| Q9 메모리 (pidin: 코드 + 데이터 + 스택, 테스트 벡터 2.6 MB 포함) | 약 12.6 MB (8 MB + 4.0 MB + 0.5 MB) | 약 13 MB | 200 MB 이내 |
| C++ 단위 테스트 (피처 + 이벤트 히스테리시스, 정책, 저장) | 3개 통과 | 통과 | – |

- 한 행씩 부르면 약 170 µs로, 행을 모아 부를 때(행당 약 80 µs)보다 느립니다 (v007. 이전 모델은 320 / 130 µs). P3는 한 주기의 폰들을 한 번에 넘깁니다.
- Mac에서 보낸 파일을 QNX `tar`가 풀 때 "time stamp ... in the future" 경고가 쏟아지지만 보드 시계는 맞습니다 (`date` 확인). 무시해도 되고, 혹시 몰라 빌드는 `rm -rf build` 후 새로 했습니다.
- QNX에서 `getrusage`의 `ru_maxrss`는 0이라 메모리는 `pidin -p <pid> mem`으로 잽니다.
- QNX `strtod`가 일부 소수를 1~2 ULP 다르게 읽어서, 설정 JSON의 숫자는 `json_min.hpp`가 직접 읽습니다. LightGBM 모델 파일은 영향이 없었습니다(기준값과 정확히 같은 행까지 일치).

## P1→P4 프로세스 파이프라인과 AC-02 (2026-11-17 모델 v007 · 데이터셋 v006, BLE 없이)

P1 수집(trip.bin 재생), P2 피처, P3 추론, P4 정책이 각각 별도 프로세스이고 QNX 메시지 전달(`name_attach` / `MsgSend` / `MsgReceive`)로 이어집니다. 코드는 `controller/qnx/`, 메시지 정의는 `messages.hpp`입니다.

- 받는 쪽은 메시지를 복사하자마자 회신하고 처리합니다. 그래서 100 Hz로 보내는 P1이 뒤 단계의 계산을 기다리지 않습니다.
- 각 프로세스는 다음 단계에 연결된 뒤에 자기 이름을 붙입니다 (P4 → P3 → P2 → P1 순서). 이름이 보이면 받을 준비가 끝난 것입니다.
- 주행 경계(RIDE_START/END)는 샘플과 같은 길로 P2 → P3 → P4에 전달되어 순서가 섞이지 않습니다.
- P3는 주기마다 P4에 등록된 폰과 프로필을 묻습니다. 폰이 하나도 없으면 승객 피처를 NaN으로 둔 `default` 한 행으로 추론합니다.
- P4는 허용도와 프로필이 바뀔 때마다 `--state` 아래에 저장합니다 (임시 파일 → fsync → rename). 시작할 때 저장된 값을 읽어 옵니다.
- BLE(P5)가 붙기 전까지 HUD, 폰, 프로필은 P4 명령행으로 줍니다. 화면은 켜짐, 주의는 있음으로 고정합니다. 정책 시각은 주행 시각입니다.

Mac에서 보내고 실행한 뒤 비교합니다 (폰 2대: 데이터셋 탑승 1과 0의 승객 피처, 재생 trip은 탑승 1). `V`는 모델 버전, `D`는 그 모델의 데이터셋 버전 폴더입니다.

```bash
V=$(python3 ml/runs.py current); D=$(python3 ml/runs.py dataset)
COPYFILE_DISABLE=1 tar --no-xattrs --no-mac-metadata -czf - controller protocol ml/out/runs/CURRENT $V/models $D/trips/ride_0001.bin | ssh qnxuser@192.168.2.2 'cd ~/mermaid/repo && tar xzf - && cmake --build build'
```

```bash
ssh qnxuser@192.168.2.2 "cd ~/mermaid/repo && controller/qnx/run_pipeline.sh $D/trips/ride_0001.bin out/fast 0 $(python3 ml/verify/pipeline_check.py spec 1 0)"
```

```bash
scp qnxuser@192.168.2.2:mermaid/repo/out/fast/cycles.csv ml/verify/out/pipeline/cycles_fast.csv && python3 ml/verify/pipeline_check.py check $D/trips/ride_0001.bin ml/verify/out/pipeline/cycles_fast.csv 1 0
```

실시간은 세 번째 인자를 `1`로 줍니다 (v006 ride_0001은 31분 걸림). `spec` 출력의 맨 앞 숫자가 차종 인자이므로 그 앞에 다른 숫자를 넣지 않습니다 (넣으면 P4가 그 숫자를 폰으로 읽고 끝나서 P1~P3가 멈춥니다).

| 항목 | 빠른 재생 (쉬지 않고) | 실시간 (1배속) | 기준 |
|---|---|---|---|
| 샘플 (P1 보냄 → P2 받음) | 186,518 → 186,518, 빠짐 0 | 186,518 → 186,518, 빠짐 0, 전송 실패 0 | AC-02: 100 Hz 샘플 손실 없음 |
| 걸린 시간 | 3.5초 | 1,865.17초 (샘플 수 × 10 ms = 1,865.18초) | – |
| P1 깨어남 지연 | – | 중앙값 953 µs, 최대 993 µs | 간격이 10 ms로 유지되면 됨 |
| 10초 주기 (P2 벡터 → P4 정책 끝) | 186주기, 중앙값 0.62 ms, 최대 0.76 ms | 186주기, 중앙값 0.57 ms, 최대 2.03 ms | AC-02: 10초 주기 안에 끝남 |
| P2 샘플 하나 처리 최대 (10초 경계의 피처 계산 포함) | 2.6 ms | 2.5 ms | 10 ms 미만 |
| P3 추론 최대 (폰 2대) | 0.62 ms | 0.55 ms | – |
| Mac featurize + Python과 비교 | 확률 최대 차이 1.1e-16, 기기 4개 ON/OFF 186주기 모두 일치 | 같음 | 차이 < 1e-6, 100% 일치 |

2026-11-11 이전 모델(T-22 이전, 데이터셋 ride_0001 200,535샘플)에서도 샘플 손실 0, 주기 지연 실시간 중앙값 0.78 ms · 최대 1.84 ms, Mac과 200주기 모두 일치였습니다.

- P1은 늘 약 1 ms 늦게 깨어납니다. QNX 타이머가 1 ms 틱 단위로 올림하기 때문입니다. 늦는 양이 일정해서 샘플 간격은 10 ms로 유지됩니다.
- `pidin`에서 P2~P4가 P1과 같은 우선순위 40으로 보이는 것은 정상입니다. QNX에서는 메시지를 받는 서버가 보낸 쪽의 우선순위를 물려받습니다.
- 처음 실행에서는 P2가 P3에 연결되기 전에 이름을 붙였습니다. 그래서 P3가 모델을 읽는 동안 P1의 RIDE_START가 5 ms 시간 초과로 빠졌습니다. 이름을 붙이는 순서를 바꿔 고쳤습니다.

## P1→P6 RF 연결 (2026-11-18, 모델 v007, ESP32 없이 가상 터미널)

P4 `--rf`(P5 명부로 등록, TIER_CMD), P2 `--motion`(P6에 MOTION 100 ms)으로 P5·P6까지 잇는다 (`docs/RF_MESSAGES.md` §7).
ESP32·폰 대신 `controller/tests/bridge_pty.py`가 가상 터미널 master(`/dev/ptyp0`)에서 폰 1대와 HUD를 흉내 내고, P6은 slave(`/dev/ttyp0`)를 연다.

```bash
ssh qnxuser@192.168.2.2 'cd ~/mermaid/repo && mkdir -p out/rf && (python3 controller/tests/bridge_pty.py --master /dev/ptyp0 --log out/rf/bridge.log --phone phone-0:25.81:1.479 --hud > out/rf/bridge.out 2>&1 &) ; sleep 1; RF=1 SER=/dev/ttyp0 RF_WAIT=3 controller/qnx/run_pipeline.sh ml/out/runs/v006/trips/ride_0001.bin out/rf 0 1; slay -s TERM python3'
```

실제 ESP32는 UART3를 켠 뒤(`sudo sh controller/bringup/uart3.sh`, 재부팅마다) `RF=1`만 주면 된다 (`SER` 기본 `/dev/ser3`).

| 항목 | 빠른 재생 | 실시간 (1,865초) | 기준 |
|---|---|---|---|
| 샘플 (P1 → P2) | 186,518, 빠짐 0 | 186,518, 빠짐 0, 전송 실패 0 | 손실 없음 |
| P1 깨어남 지연 | – | 중앙값 949 µs, 최대 981 µs | 10 ms 간격 유지 |
| 10초 주기 (P2 → P4 끝) | 중앙값 0.53 ms, 최대 0.80 ms | 중앙값 0.50 ms, 최대 0.88 ms | RF 없을 때(0.57 / 2.03 ms)와 같은 수준 |
| P2 MOTION → P6 | 18,651, 실패 0 | 18,651, 실패 0 | – |
| P4 DEV_SYNC / TIER_CMD 실패 | 0 / 0 | 0 / 0 | 0 |
| 등록 (P5 명부 → P4) | 폰 1, HUD 1 | 폰 1, HUD 1 | – |
| 명령 → 기기 | Tier 1 ON → 폰·HUD에 INT | 같음 | – |

**발견한 문제 → 고침 (2026-11-18):** 기기가 P4보다 먼저 SCR·PROF를 보내면 P6이 P4에 못 보내고 버렸다 (`ERR cannot send to P4`).
기기는 SCR를 바뀔 때만, PROF를 연결 직후 한 번만 보내므로 P4는 화면이 꺼진 것으로 보고 Tier 2·3를 막고,
P3에는 기본 프로필이 갔다. 위 표에서 Tier 1만 켜지고 MOT가 0인 이유다.
P6이 연결마다 마지막 SCR·PROF를 기억해 P4에 못 보냈으면 1초마다, 아니면 10초마다 다시 보내도록 고쳤다
(`docs/RF_MESSAGES.md` §3). `run_pipeline.sh`는 `RF=1`일 때 P4를 띄운 뒤 2초 기다린다
(빠른 재생은 첫 주기가 바로 와서, 기다리지 않으면 재전송 전 66주기를 화면 없음·기본 프로필로 처리한다. 실시간은 첫 주기가 10초 뒤라 상관없다).

고친 뒤 빠른 재생 (같은 명령, 결과 `out/rf3`):

| 항목 | 결과 |
|---|---|
| P6 | 기동 때 `cannot send to P4` 1번 → P4가 뜬 뒤 1초 안에 `P4 reachable again (resend SCR phone-0 …)` |
| 등록 · 실패 | 폰 1, HUD 1 · DEV_SYNC 0, TIER_CMD 0, MOTION 0 실패 |
| 명령 | Tier 1 ON 240초, Tier 2 ON 1,080초, Tier 3 ON 1,340초 → 폰·HUD에 INT |
| MOT | 폰 5,245줄, HUD 7,845줄 (Tier 2·3이 켜진 동안) |
| 10초 주기 지연 | 중앙값 0.56 ms, 최대 0.99 ms |

RF 없는 AC-02(`out/fast`)와 비교하면 Tier 1·2는 같은 시각이고 Tier 3만 1,180초 → 1,340초다. 재전송 탓이 아니다:
AC-02의 phone-0은 EV 경험이 `0.0`인데 RF로 들어온 폰은 PROF에 EV가 없어 NaN이라 P3 확률이 170초부터 달라진다 (T-17).

## 폰 시연: 실제 ESP32로 Tier 1·2·3 (2026-11-18)

위험 확률을 모델 대신 `fake_p3`가 0.95로 고정하고(데이터 조작), 흔들림이 큰 가짜 주행(`controller/tests/make_demo_trip.py`)을 실시간으로 재생한다.
HUD 기기가 없어도 Tier 1·2가 보이게 P4는 `--rf --hud`. 정책은 그대로라 주행 시작 후 Tier 1 10초, Tier 2 60초, Tier 3 120초에 켜진다.

```bash
ssh qnxuser@192.168.2.2 'cd ~/mermaid/repo && WAIT=40 sh controller/bringup/demo_phone.sh 420 0.95'
```

`WAIT`초 안에 폰 앱(또는 맥 BLE 흉내)을 차(MSAgent)에 연결한다. 늦게 붙어도 10초 안에 등록된다.

브리지 펌웨어는 v7이어야 한다 (`fix/rf-esp32` 브랜치). v6은 페어링을 시작하지 않아 HELLO가 버려진다.

| 확인 | 결과 (맥이 폰 대신 BLE로 연결, 브리지 v7) |
|---|---|
| 페어링 | 브리지 보안 요청 → `encrypted=1 bonded=1`, HELLO → P5 등록, INT 1·2·3 OFF 상태 수신 |
| Tier | 주행 시작 후 10초 Tier 1 ON, 60초 Tier 2 ON, 120초 Tier 3 ON → 맥에 INT |
| MOT | Tier 3 ON 뒤 10 Hz (`ax`, `ay`, `pax`, `pay`) |

**맥 Chrome ↔ ESP32 (원인 찾음, 브리지 v7(`fix/rf-esp32`)에서 고침, Chrome으로는 다시 확인 필요):** HUD 웹(`hud/web/app.html`)에서 `MSAgent-…`가 보이고 BLE 연결과 MTU 247까지는 되지만
페어링(암호화)이 끝나지 않는다. ESP32 로그에 `security: encrypted=1`이 없고, 페이지는 `startNotifications`에서 기다리느라 HELLO를 보내지 않는다.
확인할 것: 맥의 블루투스 페어링 요청 창, 맥 Bluetooth 설정의 옛 `MSAgent` 삭제, ESP32에 저장된 본딩 정리.
ESP32 전원과 로그는 맥 USB(보드의 "UART" 포트, CP2102 → `/dev/cu.usbserial-*`)로 받는다. Pi 핀 3가닥(7·29·9)은 데이터선이라 전원이 없다.
