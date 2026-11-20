# 실제 IMU · 주행 로그 · 로그 서버 설계 v0.2

> 상태: **초안, 리뷰 필요** · 2026-11-20
> 범위: P1 LIVE(MPU-6050), 차 안 로거 P7 + SQLite, 멀미 라벨(M08 SYM), 차 밖 로그 서버, 학습 데이터 내보내기,
> 개인 보정(§12).
> Claude 합성 루프는 §11에 연결 지점만 적고, 실제 로그가 쌓인 뒤 따로 설계한다.
>
> **기준:** `main` 398237d (PR #11: P4·P2 ↔ P5·P6 연결, P6 SCR/PROF 재전송, `ml/runs.py`, `ml/realdata/`,
> P5·P6 재동기 #8, 브리지 v8 #7 포함). 개발 브랜치는 `feat/data-logging`.

## 1. 목표와 원칙

**목표**
1. 실제 IMU로 10 ms 수집 → 10초 추론 → 개입까지 차 안에서 돈다.
2. 주행마다 원본 샘플, 추론 입력·출력, 정책 결정, 기기 사건, 멀미 라벨이 SD 카드에 남는다.
3. 남은 기록을 차 밖 서버로 모으고, 기존 학습 파이프라인(`ml/runs.py` 버전 폴더)으로 바로 내보낸다.

**원칙**
- **로그는 제어를 절대 막지 않는다.** P7이 없거나 느리거나 죽어도 P1~P6의 동작과 주기 시간은 같다 (§6-1 시험으로 확인).
- 제어 경로에서 디스크 I/O를 하지 않는다. SD 카드 쓰기는 P7의 낮은 우선순위 스레드만 한다.
- 차 안은 네트워크 없이 완결된다. 서버로 옮기는 일은 주행이 끝난 뒤에 따로 한다.
- 보드가 실제로 본 값(추론 입력 77칸)과 다시 계산할 수 있는 원본(IMU 100 Hz)을 둘 다 남긴다.

**이번 범위가 아닌 것:** 차량 CAN(G2~G7 신호), 차에서 서버로 직접 업로드(v2), Claude 루프 구현, 대시보드 화면.

## 2. 전체 구조

```
[차 안: Raspberry Pi 4 / QNX 8.0]

 MPU-6050 ─I2C 400kHz─▶ P1 LIVE ──Sample(MsgSend)──▶ P2 ─VEH─▶ P3 ─RISK─▶ P4 ─TIER_CMD─▶ P5 ─▶ P6 ─UART─▶ ESP32 ─BLE─▶ 앱/HUD
  (FIFO 1KB)              │                            │MOTION──────────────────────────────▶ P6     ▲          │
                          │ 공유 메모리 링                                     ▲ CTX_*(SCR/OVR/PROF)──┘          │ M08 SYM
                          ▼ /mermaid_samples          LOG_INFER  LOG_DECISION   LOG_CTX · LOG_SYM ◀───────────────┘
                         P7 로거 ◀────────────────────── P3 ─────── P4 ────────── P6 · P5 (MsgSend, 2 ms 시간 초과)
                          │ 쓰기 스레드 (우선순위 9)
                          ▼
              ~/mermaid/logs/rides/<ride_id>.sqlite

[차 밖: Mac (v1) → 클라우드]
  tools/collect_logs.py ──ssh/scp──▶ 닫힌 주행 파일 ──POST /api/rides──▶ 로그 서버 (Python FastAPI, server/)
                                                                         ├ server/data/rides/<ride_id>.sqlite (원본 보관)
                                                                         └ server/data/mermaid.db (색인 · 주기 · 라벨 · 사건)
  ml/realdata/build_real.py --source mermaid ──▶ ml/out/runs/vNNN/real_mermaid.npz ──▶ train.py
```

| 프로세스 | 언어 | 우선순위 | 이번 작업 |
|---|---|---|---|
| P1 수집 | C++17 | 40 | **`--live` 추가**, 공유 메모리 링 쓰기 |
| P2 피처 | C++17 | 30 | 변경 없음 |
| P3 추론 | C++17 | 20 | LOG_INFER 보내기 |
| P4 정책 | C++17 | 15 | LOG_DECISION 보내기 |
| P5 기기 관리 | C++17 | 14 | 안전 OFF 진입·해제, 잡아 둔 ON 적용을 LOG_CTX로 |
| P6 통신 | C++17 | 12 | M08 SYM 규칙, 폰·HUD와 오간 사건 전부를 LOG_CTX로, SYM은 LOG_SYM으로 |
| **P7 로거 (새로)** | C++17 | 받기 11 / 쓰기 9 | 새로 |

P7도 다른 프로세스와 같은 `controller/` CMake 빌드, `ipc.hpp`, 보드의 gcc를 쓴다.

## 3. 메모리와 데이터가 지나가는 길

### 3-1. 센서에서 RAM까지 (LIVE)

| 단계 | 위치 | 크기 | 수명 |
|---|---|---|---|
| ① 측정 레지스터 / FIFO | MPU-6050 칩 안 | FIFO 1,024바이트 = 12바이트 × 85샘플(0.85초) | 칩이 계속 덮어씀. FIFO는 읽으면 빠짐 |
| ② I2C 읽기 버퍼 | P1 RAM (스택) | 최대 96바이트(8샘플)씩 | 한 번 읽기 |
| ③ `msg::Sample` | P1 RAM | 8 + 8 + 32×8 = 272바이트 | 한 샘플 |
| ④ 공유 메모리 링 | RAM (P1 · P7 공유) | 3,000칸 × 288바이트 ≈ 864 KB (30초) | 30초 뒤 덮어씀 |
| ⑤ P2 `Inbox` | P2 RAM | 메시지 1개 | 한 샘플 |
| ⑥ P2 링버퍼 | P2 RAM | 16채널 × 30,000 × 8바이트 ≈ 3.8 MB (5분) | 5분 뒤 덮어씀, 주행마다 비움 |
| ⑦ SQLite 파일 | **SD 카드** | 시간당 약 11 MB (§6-6) | 서버가 받을 때까지 남음 |

②~⑥은 RAM이라 전원이 꺼지면 사라진다. ⑦만 남는다. 프로세스 사이 복사는 QNX 커널이 보낸 쪽 메모리에서 받는 쪽 메모리로 직접 한다.

### 3-2. 지금(로그 없음) 디스크에 남는 것

`cycles.csv`(10초마다 확률 p2~p6와 기기 ON/OFF, 실행마다 `"w"`로 덮어씀), `state/tolerance/*.json`, `state/profiles/*.json`, 실행 요약 `p*.out`뿐이다. 원본 샘플과 피처 77칸은 남지 않는다. 메모리 사용량은 몇 시간을 돌려도 늘지 않는다(P1 `late_us`는 100만 개, 4 MB에서 멈춤. LIVE에서는 이 벡터를 쓰지 않고 통계만 낸다).

## 4. P1 LIVE (MPU-6050)

```
p1_acq --live [--i2c /dev/i2c1] [--i2c-speed 400000] [--mount controller/config/imu_mount.json]
              [--state DIR] [--prio 40] [--timeout-ms 5]
p1_acq <trip.bin> [--speed S] ...      # 재생 모드는 그대로 (시험용). 재생도 링에 쓴다
```

### 4-1. 레지스터 설정 (`mpu_test.c` 대비 바뀌는 곳은 굵게)

| 레지스터 | 주소 | 값 | 뜻 |
|---|---|---|---|
| PWR_MGMT_1 | 0x6B | 0x80 → 100 ms 대기 → 0x01 | **리셋 후** 슬립 해제, 클럭 = 자이로 PLL |
| SMPLRT_DIV | 0x19 | 9 | 1 kHz / (1+9) = 100 Hz |
| CONFIG | 0x1A | 0x03 | DLPF 약 44 Hz (100 Hz 샘플의 나이퀴스트 50 Hz 아래) |
| GYRO_CONFIG | 0x1B | 0x00 | ±250 °/s, 131 LSB/(°/s). 승용차 요 각속도는 보통 60 °/s 이하 |
| ACCEL_CONFIG | 0x1C | **0x08** | **±4 g, 8192 LSB/g.** z축에 중력 1 g가 늘 있어서, ±2 g면 방지턱에서 잘림 |
| FIFO_EN | 0x23 | **0x78** | **가속도 xyz + 자이로 xyz만 FIFO에 (온도 제외), 샘플당 12바이트** |
| USER_CTRL | 0x6A | **0x04 → 0x40** | **FIFO 리셋 후 FIFO 켜기** |
| INT_ENABLE | 0x38 | 0x00 | 인터럽트 핀 안 씀 (폴링) |

시작할 때 WHO_AM_I(0x75) = 0x68인지 확인한다. 아니거나 I2C가 실패하면 초기화 실패로 세고 1초 뒤 다시 시도한다. 10번 연속 실패하면 종료 코드 3.

### 4-2. 읽기 루프 (10 ms마다)

```
매 10 ms (CLOCK_MONOTONIC 절대 시각, 지금 P1과 같은 방식):
  1. INT_STATUS(0x3A) 1바이트 + FIFO_COUNT(0x72~0x73) 2바이트 읽기
  2. FIFO_OFLOW(bit4)이거나 count ≥ 1024(가득)이면: 넘침. 몇 개를 잃었는지 알 수 없으므로 주행을 끊고 다시 시작(4-5)
     (INT_STATUS 비트가 INT_ENABLE 없이도 서는지는 보드에서 확인. 그래서 count로도 판정)
  3. count % 12 ≠ 0이면: 정렬이 깨짐. FIFO 리셋, 남아 있던 ceil(count / 12)개를 빠진 샘플로 보고 4-5 규칙
  4. n = count / 12 (보통 1, 클럭 위상 차이로 가끔 0 또는 2)
  5. n개를 최대 8개(96바이트)씩 FIFO_R_W(0x74)에서 읽기
  6. 샘플마다 변환(4-3) → Row 만들기 → 링에 쓰기 → P2에 MsgSend (시간 초과 5 ms)
```

- 샘플 시각은 **센서 클럭** 기준이다. `t = seq / 100` (초, 주행 시작부터). Pi가 늦게 깨어나도 FIFO에 쌓여 있어서 간격은 센서가 정한다.
- 센서 클럭과 Pi 클럭의 차이를 보려고 링 칸마다 Pi의 `mono_ns`(읽은 시각)를 같이 적는다. 보드 시험에서 실제 샘플률을 잰다 (§14 완료 기준: 100 ± 1 Hz).
- I2C 시간(400 kHz): 상태 3바이트 + 1샘플 12바이트 ≈ 0.5 ms. 100 kHz여도 약 2 ms라 10 ms 안에 충분하다. QNX I2C 드라이버가 400 kHz를 받지 않으면 100 kHz로 내리고 경고한다.

### 4-3. 변환 (온도는 읽지 않음)

FIFO 12바이트 = `ax ay az gx gy gz`, 각각 big-endian int16.

```
a_s = raw_a / 8192 × 9.80665                  [m/s², 센서 축]
g_s = (raw_g / 131 − bias_dps) × π / 180       [rad/s, 센서 축]
a = R · a_s,  g = R · g_s                     [차량 축: x 앞, y 왼쪽, z 위]
```

- `R`은 `controller/config/imu_mount.json`의 3×3 회전 행렬이다. 기본값은 단위 행렬(보드의 x가 차의 앞을 보게 장착).
  `{"version": 1, "R": [[1,0,0],[0,1,0],[0,0,1]], "note": "보드 x = 차 앞"}`. 행렬식이 1 ± 1e-3이 아니면 시작하지 않는다.
- 가속도는 중력을 포함한다(`trip_format.json`: 수평 정지 시 z = +9.81).

### 4-4. 시작 보정 (자이로 바이어스)

1. FIFO를 켠 뒤 2초(200샘플)를 모은다. 정지가 아니면 창을 1초씩 밀며 다시 판정한다.
2. 정지 판정: 가속도 크기의 표준편차 < 0.05 m/s², 자이로 각 축 표준편차 < 0.5 °/s.
3. 정지면 자이로 평균을 `bias_dps`로 쓰고 `state/imu_bias.json`에 저장한다 (`store::write_atomic`).
4. 30초 안에 정지 상태를 못 얻으면 저장된 `imu_bias.json`을 쓰고 경고한다. 파일도 없으면 0을 쓰고 health에 `bias_uncalibrated=1`을 남긴다.
5. 정지 상태의 가속도 크기가 9.81 ± 0.5 m/s²를 벗어나면 경고만 한다(범위 설정이나 장착 이상 확인용).

보정 2초는 RIDE_START 전이라 주행 샘플에 들어가지 않는다.

### 4-5. Row 채우기와 빠진 샘플

- `row[C_T] = seq / 100`, `row[C_AX..C_GZ] =` 변환값, **나머지 25칸은 NaN**(주행 전체 NaN, `trip_format.json` 규칙).
- **중간에 NaN을 넣지 않는다.** P2의 필터(Wf, Biquad, Oman)는 NaN이 한 번 들어가면 상태가 계속 NaN이 된다.
- 빠진 샘플 처리 (구현: `controller/common/imu.cpp`의 `Acquirer`):

| 경우 | 처리 |
|---|---|
| 정렬 깨짐, 빠진 샘플 50개(0.5초) 이하 | 마지막 값을 반복해 채운다(hold). `seq`는 빠진 개수만큼 올린다. 링 칸에 `FLAG_HOLD` |
| 정렬 깨짐, 50개 초과 | 다시 시작: RIDE_END → 센서 재초기화(4-1) → 보정(4-4) → 새 RIDE_START. 피처 상태를 깨끗하게 다시 시작 |
| FIFO 넘침 (85샘플 = 0.85초 넘게 못 읽음) | 빠진 개수를 모르므로 다시 시작 |
| I2C 오류 1초 미만 | 그 틱만 건너뛴다. 샘플은 FIFO에 남아 있다가 다음 틱에 읽힌다 |
| I2C 오류 1초 이상 계속 | 다시 시작. 재초기화가 10번 연속 실패하면 종료 코드 3 |

### 4-6. 주행 경계

- **v1 (이번):** 보정이 끝나면 RIDE_START, SIGINT·SIGTERM을 받으면 RIDE_END를 보내고 끝낸다.
  LIVE에서는 P2~P4를 `--once` 없이 띄운다. 새 스크립트 `controller/qnx/run_live.sh`의 시작 순서는 P6 → P5 → P7 → P4 → P3 → P2 → P1.
- **v2 (나중):** 정지 판정(4-4와 같은 기준)이 5분 계속되면 RIDE_END, 다시 움직이면 보정 후 RIDE_START. 휴식 피처(`kRestStillTimeS = 180`)와 겹치지 않게 5분으로 둔다.

### 4-7. P1 타이밍 예산 (10 ms 주기)

| 일 | 예상 |
|---|---|
| I2C 읽기 (400 kHz, 1샘플) | 약 0.5 ms |
| 변환 + 링 쓰기 | < 10 µs |
| P2로 MsgSend (P2는 복사 후 바로 회신) | 수십 µs |
| **합계** | **< 1 ms** (100 kHz면 < 2.5 ms) |

## 5. 공유 메모리 링 `/mermaid_samples` (P1 → P7)

- **만드는 쪽은 P1이다.** P1은 P7이 있든 없든 똑같이 동작한다. P7은 링이 없으면 1초마다 다시 연다.
- 생산자 1 · 소비자 1, 락 없음. `std::atomic<uint64_t>`가 aarch64에서 lock-free인지 `static_assert(is_always_lock_free)`로 확인한다.

```cpp
// controller/common/sample_ring.hpp (Mac 시험과 QNX가 같이 씀, POSIX shm_open + mmap)
struct RingHeader {
    uint32_t magic;          // 'MMSR'
    uint32_t version;        // 1
    uint32_t cap;            // 3000
    uint32_t col_mask;       // 링에 실제 값이 있는 trip 칸 (LIVE: t, ax..gz = 0x7F)
    uint64_t epoch;          // P1이 시작할 때마다 +1 (P7이 P1 재시작을 알아챔)
    std::atomic<uint64_t> head;  // 지금까지 쓴 칸 수 (P1만 씀, release)
};
struct RingSlot {            // 288바이트
    uint64_t n;              // 이 칸의 일련번호 (= 쓸 때의 head). 읽은 뒤 다시 보고 덮어쓰기 확인
    uint64_t mono_ns;        // P1이 읽은 시각
    uint32_t kind;           // SAMPLE / RIDE_START / RIDE_END
    uint32_t flags;          // FLAG_HOLD 등
    uint64_t seq;            // 주행 안 샘플 번호
    double row[32];          // trip_format 순서
};
```

- **P1 쓰기:** `slot = &slots[head % cap]` → 내용 쓰기 → `slot->n = head` → `head.store(head+1, release)`.
- **P7 읽기 (200 ms마다):** `h = head.load(acquire)`. `h − tail > cap`이면 넘친 것이다. 잃은 개수를 health에 적고 `tail = h − cap`으로 옮긴다. 칸을 복사한 뒤 `slot->n == tail`인지 다시 확인하고, 다르면 덮어쓰인 것이라 버린다.
- RIDE_START·RIDE_END도 링에 들어가서 P7이 P2~P4와 같은 주행 경계를 쓴다. P7은 이것으로 주행 파일을 열고 닫는다.
- RIDE_START 칸의 `row`는 샘플이 아니라 주행 정보다 (`sample_ring.hpp` `StartInfo`): 자이로 바이어스 3축, 보정 실패 여부,
  가속도·자이로 범위. P7이 meta에 적는다. 재생 모드는 모두 NaN. (`imu_bias.json` 파일을 읽으면 P1이 쓰기 전에 읽을 수 있어서 링으로 넘긴다.)
- P7은 **처음 링을 열 때 이미 쓰인 칸을 건너뛴다** (`Reader::skip_to_head`). 지난 실행에서 기록했거나 복구한 칸을 다시 쓰지 않기 위해서다.
  P1이 다시 시작하면 epoch가 바뀌어 새 주행의 처음부터 읽는다.

## 6. P7 로거

```
p7_logger [--dir ~/mermaid/logs] [--device-id pi01] [--min-free-mb 1024] [--prio 11]
```

### 6-1. 구조와 격리

- **받기 스레드** (우선순위 11, 서비스 이름 `mermaid_p7`): LOG_* 메시지를 받으면 큐에 복사하고 바로 회신한다. 디스크 I/O를 하지 않는다.
- **쓰기 스레드** (우선순위 9): 200 ms마다 링을 비우고 큐를 꺼내서 SQLite에 쓴다. 트랜잭션은 1초에 한 번.
- RIDE_END를 읽은 뒤 **2초 더** 파일을 열어 둔다. 마지막 주기의 LOG_INFER·LOG_DECISION이 RIDE_END보다 늦게 올 수 있어서다.
- RIDE_START 없이 샘플을 만나면(주행 중에 P7이 다시 시작) 그 샘플부터 새 파일을 연다 (`meta.joined_at_seq`).
- 주행 밖에서 받은 SCR·PROF는 마지막 값만 기억했다가 다음 주행 파일을 열 때 `ctx`에 적는다(`t` NULL, `mono_ns < mono_start_ns`).
- 주행 밖에서 받은 그 밖의 기기 사건(CONNECT, HELLO, INT, DROP …)은 **최근 64개**(`kPreRideCtx`)를 기억했다가 다음 주행 파일에
  한 번 적는다(`t` NULL). 폰은 보통 주행이 시작되기 전에 붙기 때문에, 이것이 없으면 그 주행의 HELLO가 로그에 없다.
  64개를 넘겨 버린 것, 공간 부족으로 기록하지 않는 주행 중의 사건, 주행 밖의 LOG_INFER·LOG_DECISION·LOG_SYM은 세기만 한다(`outside_ride`).
- 큐: 고정 크기 512개. 차면 가장 오래된 것을 버리고 `queue_drop`을 센다(메모리가 늘지 않게).
- P7은 **아무에게도 MsgSend 하지 않는다.** 그래서 교착 고리가 생기지 않는다.
- 보내는 쪽 규칙: `ipc::Client(kNameP7, 2 ms)`. 실패하면 자기 통계 `log_fail`만 올리고 계속한다(P3·P4는 RIDE_END 통계 줄, P6은 10초 stat 줄, P5는 1분마다의 sync 줄).
  P6·P5는 사건이 드물고 하나하나가 뜻이 있어서, 실패한 사건을 작은 큐(P6 256개, P5 64개)에 둔 채 다음 바퀴에 한 번씩 다시 보낸다.
  한 바퀴에 실패는 한 번까지만 기다리므로(2 ms) P7이 멈춰 있어도 제어는 그대로다. 큐가 차면 버리고 센다(P6 `log_drop`).
  P7이 없으면 `Client`가 1초에 한 번만 다시 연결을 시도한다(지금 `ipc.hpp` 동작).
- Mac 시험 `log_sim` (구현됨): (a) 보통 주행, (b) 주행 중 `kill -9` 뒤 다시 띄워 복구, (c) 10초 SIGSTOP. 세 경우 모두 P1 전송 실패 0,
  P4 주기 빠짐 0, (c)는 링 덕분에 멈춘 동안의 샘플도 모두 기록. 주기 시간 변화는 Pi에서 잰다.
- **격리 시험 (완료 기준):** (a) P7 없이, (b) P7을 주행 중에 `kill -9`, (c) P7 쓰기 스레드를 SIGSTOP. 세 경우 모두 P1 샘플 손실 0, P2→P4 주기 최대 시간 변화 < 0.5 ms.

### 6-2. 새 QNX 메시지 (`controller/qnx/log_messages.hpp`, 0x8060~)

| 메시지 | 번호 | 보내는 쪽 | 시점 | 크기 |
|---|---|---|---|---|
| LOG_INFER | 0x8060 | P3 | 10초마다, RISK를 P4에 보낸 **뒤** | 약 6.5 KB |
| LOG_DECISION | 0x8061 | P4 | 10초마다, 명령을 다 보낸 **뒤** | 약 1 KB |
| LOG_CTX | 0x8062 | P6 (UART 스레드), P5 | 사건마다 | 약 300 B |
| LOG_SYM | 0x8063 | P6 (UART 스레드) | 앱이 SYM을 보낼 때 (규칙 통과한 것만) | 약 100 B |

```cpp
constexpr int kMaxDevices = kMaxPhones + 3;   // 폰 9 + airflow-01 + display-01 + HUD
struct LogInfer {
    Hdr h{LOG_INFER, 0, 0};
    uint64_t seq; double t; uint64_t mono_ns;
    char model[16];                         // ml/runs CURRENT 버전 (예: "v007")
    uint32_t n;                             // 행 수 (폰 수, 없으면 "default" 1행)
    char id[kMaxPhones][kIdLen];
    double x[kMaxPhones][proto::kNumFeatures];   // P3에 들어간 77칸 그대로 (NaN 포함)
    Risk p[kMaxPhones];                     // p2..p6, 개인 보정 전 (모델 출력 그대로)
    double cal_a[kMaxPhones], cal_b[kMaxPhones];  // 적용한 개인 보정 (§12, 없으면 1, 0)
};
struct LogDecision {
    Hdr h{LOG_DECISION, 0, 0};
    uint64_t seq; double t; uint64_t mono_ns;
    uint32_t n;
    struct { char device[kIdLen]; uint8_t on; uint8_t tier; uint8_t changed; uint8_t pad; } d[kMaxDevices];
};
struct LogCtx {
    Hdr h{LOG_CTX, 0, 0};
    uint64_t mono_ns;
    char sender[kIdLen];
    uint8_t kind;          // CtxKind: 아래 표 (0~13). ctx 표에는 이름으로 들어간다
    char value[192];       // 원래 JSON 그대로 (잘리면 끝에 "…")
};
struct LogSym {
    Hdr h{LOG_SYM, 0, 0};
    uint64_t mono_ns;
    char phone[kIdLen];
    int32_t misc;          // 0~10
};
```

- **시간 기준:** P3·P4는 주행 시각 `t`를 알지만 P6는 모른다. 모든 메시지에 `mono_ns`를 넣고, P7이 링의 `(seq, mono_ns)` 짝으로 `mono_ns → t`를 바꾼다(가장 가까운 샘플).
- `policy.hpp`의 `Command`에는 이유가 없으므로 LOG_DECISION은 기기별 최종 상태(on, tier)와 이번 주기에 바뀌었는지(`changed`)만 남긴다. 지금 `cycles.csv`의 기기 칸과 같은 정보다.
  기기 목록: airflow-01, display-01, 등록된 폰 (`tier`는 등록되지 않은 기기면 0).
- **LOG_CTX는 폰·HUD와 오간 사건 전부다** (2026-11-21, RF). HB(1초마다)와 MOT(10 Hz)만 뺀다. `value`는 보내는 쪽이 만든 JSON이고
  191자를 넘지 않는다(잘리지 않음). `sender`는 기기 ID, Pi → 기기 방향은 **받는 기기**의 ID, HELLO 전이라 모르면 `conn<N>`,
  전체는 `all`, P5 사건은 `p5`, 연결 번호도 못 읽은 줄은 `uart`.

| kind (번호) | 누가 | 언제 | value |
|---|---|---|---|
| `HELLO` (0) | P6 | 기기 등록이 P5에 전달된 뒤 한 번 | `{"conn","role","ver"}` (role 0 = app, 1 = hud) |
| `BYE` (1) | P6 | 등록을 지움 (DISCONNECT, 무응답, 같은 번호에 새 CONNECT, P6 시작) | `{"conn"}` (sender `conn<N>` 또는 `all`) |
| `SCR` (2) | P6 | 기기가 보냄 | `{"on","att"}` |
| `OVR` (3) | P6 | 기기가 보냄 (권한 검사 통과) | `{"device","on"}` |
| `PROF` (4) | P6 | 앱이 보냄 (범위 검사 통과) | `{"mssq","sens"}` |
| `CONNECT` (5) | P6 | 브리지가 BLE 연결을 알림 (HELLO 전) | `{"conn"}` |
| `DISCONNECT` (6) | P6 | 브리지가 끊김을 알림 | `{"conn","registered"}` |
| `INT` (7) | P6 | Pi → 기기로 INT가 실제로 나감. 받은 기기마다 한 줄 | `{"conn","tier","on"}`, ALL로 나간 것은 `"all":1` |
| `PROF_OUT` (8) | P6 | Pi → 앱으로 PROF 회신이 나감 | `{"conn","mssq","sens"}` |
| `DROP` (9) | P6 | 브리지에 끊으라고 함 | `{"conn","reason"}`: `no_hello_5s`(HELLO 5초 없음), `silent_5s`(5초 무응답), `p5`(P5 요청), `p6_start`(P6 시작, conn `"all"`) |
| `REJECT` (10) | P6 | 기기가 보냈지만 버림 | `{"conn","reason","raw"}`: 이유(60자까지)와 원문 앞 90자. 예: `SYM misc not an integer 0..10`, `OVR target <ID> not allowed`, `SCR before HELLO`, `unknown t X`, `bad line`(conn null) |
| `SAFE_OFF` (11) | P5 | DEV_SYNC가 끊겨 전부 OFF | `{"no_sync_s","conns"}` |
| `SAFE_CLEAR` (12) | P5 | DEV_SYNC가 돌아와 해제 | `{"held"}`: 잡아 둔 ON 개수 |
| `HELD_ON` (13) | P5 | 안전 OFF 중 잡아 둔 ON을 해제 뒤 적용 | `{"device","tier"}` |

  - 번호는 바꾸지 않고 뒤에만 붙인다. 표 구조는 그대로라 `schema_version`은 1을 유지한다(읽는 쪽은 모르는 kind를 건너뛰면 된다).
  - **시각:** 모든 줄에 사건이 **생긴 순간**의 `mono_ns`가 있다(보낸 순간이 아니다. P6의 받기 스레드에서 생긴 INT도 그 자리에서 찍는다).
    P7이 주행 시각 `t`로 바꿔 함께 적는다. **주행 안/밖은 이 `mono_ns`로만 정한다**: `mono_start_ns`보다 앞이면 `t` NULL(주행 전),
    아니면 `t`가 있다. P7에 늦게 닿았는지(보내는 쪽 큐에서 최대 100 ms), 그때 첫 샘플이 와 있었는지와는 상관없다. 벽시계가 필요하면 `meta.wall_start + (mono_ns − mono_start_ns)`.
  - P6의 받기 스레드는 MsgSend를 하지 않는다(`RF_MESSAGES.md` §2-2). 그래서 사건은 큐에 넣고 UART 스레드가 P7에 보낸다(100 ms 안).
  - REJECT는 1초에 10개까지만 남긴다(쓰레기 줄이 쏟아질 때 로그 길이 막히지 않게). 원문의 제어 문자와 ASCII 밖 바이트는 `?`로 바꾼다.
  - 10초마다 다시 보내는 SCR·PROF(`CtxResend`)는 사건이 아니라서 보내지 않는다. 스트림 ON/OFF(OUT_STREAM)도 남기지 않는다(INT tier 3·2와 같은 정보).
- LOG_SYM: P6이 M08 SYM 규칙(§7)을 통과한 점수만 보낸다 → `label` 표. 통과하지 못한 것은 `ctx`에 REJECT로 남는다.

### 6-3. 파일 배치

```
~/mermaid/logs/
  session_count                 # P7이 시작할 때마다 +1 (write_atomic)
  rides/
    pi01-00042-003.open.sqlite  # 기록 중
    pi01-00042-002.sqlite       # 닫힘 = 완전한 주행 (올릴 대상)
    pi01-00042-002.uploaded     # 서버가 받음 (collect_logs.py가 만드는 빈 파일)
```

- **ride_id** = `<device_id>-<session:05d>-<ride:03d>`. Pi 4는 RTC가 없어서 벽시계를 믿을 수 없다. 그래서 순서는 세션과 주행 번호로 정한다. NTP가 맞춰졌을 때만 `meta.wall_start`를 적는다.
- **닫기:** RIDE_END(또는 P1 epoch 변경, P7 종료)에서 `wal_checkpoint(TRUNCATE)` → `journal_mode=DELETE` → 닫기 → `.open` 없는 이름으로 rename → 디렉터리 fsync. 파일 하나로 완결되어 그대로 올릴 수 있다.
- **복구:** P7이 시작할 때 남은 `*.open.sqlite`가 있으면 열어서 `PRAGMA quick_check`. 통과하면 `meta.closed_reason = 'recovered'`로 닫고, 실패하면 `.broken`으로 이름을 바꿔 둔다(지우지 않음).

### 6-4. SQLite

- 공식 amalgamation **3.47.2**를 `controller/third_party/sqlite/`(`sqlite3.c`, `sqlite3.h`, 라이선스: public domain)에 넣고 CMake로 함께 빌드한다. 보드에 라이브러리가 있는지에 기대지 않는다.
  CMake는 이 파일이 있으면 함께 빌드하고, 없으면 시스템 SQLite(`find_package(SQLite3)`, Mac SDK 3.43)를 쓴다. 둘 다 없으면 P7을 빌드하지 않는다.
  (amalgamation은 아직 저장소에 넣지 않았다.)
- 컴파일 옵션: `SQLITE_THREADSAFE=0`(DB는 쓰기 스레드만 만짐), `SQLITE_OMIT_LOAD_EXTENSION`, `SQLITE_DEFAULT_FOREIGN_KEYS=1`.
- 열 때: `journal_mode=WAL`, `synchronous=NORMAL`, `temp_store=MEMORY`. 1초에 트랜잭션 한 번이므로 전원이 갑자기 꺼지면 최대 약 1초를 잃는다.

### 6-5. 주행 파일 스키마 (schema_version 1)

```sql
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
-- schema_version, ride_id, device_id, session, ride_no, source ('live'|'replay'),
-- columns (JSON: samples.data의 칸 이름, LIVE = ["ax","ay","az","gx","gy","gz"]),
-- imu_mount (JSON R), imu_bias_dps (JSON), accel_range_g, gyro_range_dps,
-- model_version (첫 LOG_INFER), thresholds·policy_json (설정 파일 전체), git_commit (빌드할 때 git describe),
-- sample_rate_hz, mono_start_ns, mono_end_ns, wall_start (2024년 이후 시계일 때만), p1_samples (RIDE_END의 보낸 수),
-- bias_uncalibrated, joined_at_seq (주행 중간에 P7이 시작했을 때),
-- closed_reason ('ride_end'|'p1_restart'|'shutdown'|'recovered')
-- imu_bias_dps·accel_range_g·gyro_range_dps는 링 RIDE_START 칸에서 (§5), imu_mount는 --config의 파일 전체

CREATE TABLE samples (            -- 1초(100샘플) 묶음
  seq0     INTEGER PRIMARY KEY,   -- 첫 샘플 번호. t = seq / 100
  n        INTEGER NOT NULL,
  mono_ns0 INTEGER NOT NULL,      -- 첫 샘플을 P1이 읽은 시각
  data     BLOB NOT NULL,         -- float32 LE, n × len(columns), 행 우선
  hold     BLOB                   -- hold로 채운 샘플의 비트맵 (없으면 NULL)
);
CREATE TABLE infer (
  seq INTEGER NOT NULL, t REAL NOT NULL, mono_ns INTEGER NOT NULL,
  phone TEXT NOT NULL,
  x  BLOB NOT NULL,               -- float64 LE × 77 (feature_order 순서, NaN 포함)
  p2 REAL, p3 REAL, p4 REAL, p5 REAL, p6 REAL,   -- 개인 보정 전 (재학습은 이 값으로)
  cal_a REAL NOT NULL DEFAULT 1, cal_b REAL NOT NULL DEFAULT 0,
  PRIMARY KEY (seq, phone)
);
CREATE TABLE decision (
  seq INTEGER NOT NULL, t REAL NOT NULL, device TEXT NOT NULL,
  on_ INTEGER NOT NULL, tier INTEGER NOT NULL, changed INTEGER NOT NULL,
  PRIMARY KEY (seq, device)
);
CREATE TABLE ctx   (mono_ns INTEGER NOT NULL, t REAL, sender TEXT NOT NULL, kind TEXT NOT NULL, value TEXT);
-- kind: HELLO BYE SCR OVR PROF CONNECT DISCONNECT INT PROF_OUT DROP REJECT SAFE_OFF SAFE_CLEAR HELD_ON (§6-2 표). t NULL = 주행 시작 전
CREATE TABLE label (mono_ns INTEGER NOT NULL, t REAL, phone TEXT NOT NULL, misc INTEGER NOT NULL CHECK (misc BETWEEN 0 AND 10));
CREATE TABLE health(mono_ns INTEGER NOT NULL, t REAL, key TEXT NOT NULL, value REAL NOT NULL);
-- health key: ring_overrun, queue_drop, hold_samples (주행을 닫을 때), sample_rate_hz (1분마다), samples_paused
--   (I2C 오류·FIFO 넘침은 P1이 주행을 끊고 다시 시작하므로 주행 경계와 P1 통계 줄에 남는다.
--    보내는 쪽 log_fail은 P7이 받을 수 없을 때 생기는 값이라 각 프로세스의 통계 줄에 남긴다)
CREATE INDEX label_phone ON label(phone, t);
```

### 6-6. 크기와 보관

| 표 | 시간당 |
|---|---|
| samples (6칸 × 4바이트 × 100 Hz) | 8.6 MB |
| infer (폰 9대 최대, 77 × 8바이트 + 확률) | 2.3 MB |
| 나머지 | < 0.1 MB |
| **합계** | **약 11 MB** (32 GB SD면 수천 시간) |

- 60초마다 `statvfs`로 남은 공간을 본다.
  - `--min-free-mb`(1 GB) 아래: `.uploaded`가 있는 가장 오래된 주행부터 지운다.
  - 그래도 부족하면 `samples` 기록만 멈추고 health에 `samples_paused`. 나머지 표는 계속 쓴다.
  - 200 MB 아래: 새 주행 파일을 만들지 않는다(제어는 계속).
- `.uploaded`가 없는 주행은 절대 자동으로 지우지 않는다.

## 7. 멀미 라벨: M08 `SYM` (새 RF 메시지)

모델의 정답은 "다음 300초 안에 MISC ≥ k"(`docs/SPEC.md`)이다. 실제 주행으로 학습하려면 MISC 점수를 받아야 한다.

> **팀 합의됨 (2026-11).** S0에서 음성 MISC 보고(M04)는 일부러 뺐다(`protocol/PROTOCOL.md` §3). M08 SYM은 그것과 다른
> **데이터 수집용 터치 입력**이고 정책(P4)에는 쓰지 않는다. M04 번호는 재사용하지 않고 **M08**을 새로 쓴다.

- **형식 (확정):** 앱 → C: `{"t":"SYM","misc":3}`. `misc`는 0~10 **정수**. `messages.schema.json`, `PROTOCOL.md` §3에 들어갔다
  (`python3 protocol/check.py` 통과). 앱은 같은 형식으로 `fix/app-first-build`에서 구현 중.
- **P6 규칙** (`comm.cpp`, 구현됨): 역할이 `app`인 연결만, HELLO 뒤, 0~10 정수만 통과 → LOG_SYM(P7) → `label` 표. P4로는 보내지 않는다.
  통과하지 못한 것(범위 밖, 소수, 문자열, HUD가 보냄, HELLO 전)은 P6 로그에 남기고 버리며 `ctx`에 `REJECT`로 남는다. 회신은 없다.
  `4.0`은 정수 4로 받는다(JSON Schema의 integer와 같은 기준).
- **Pi에서 확인하는 법:** `bridge/tools/ble_check.py full --sym 4` (또는 `ai --sym 4`) → P6 창에 `P7 SYM <ID> misc=4`, 주행이 닫힌 뒤
  `sqlite3 <ride>.sqlite "SELECT t, phone, misc FROM label"`. `--sym 11`은 `ctx`의 REJECT 한 줄.
- **앱:**
  - 주행 중(INT를 한 번이라도 받은 뒤) 2분마다 알림.
  - 0~10 버튼 한 줄에 MISC 기준 문구: 0 증상 없음 · 1 약간 불편 · 2~5 어지러움·더움·두통(약~강) · 6~9 메스꺼움(약~강) · 10 구토.
  - 응답이 없으면 그 칸은 비워 둔다(이전 값으로 채우지 않음). 언제든 직접 입력할 수 있다.
  - **로그 수집 동의를 받은 승객만** 보낸다. 동의는 앱 설정에 저장하고, 끄면 SYM을 보내지 않는다.
- P6가 `mono_ns`를 붙이므로 앱 시계는 쓰지 않는다.

## 8. 차 → 서버 수집 (v1: Mac이 가져옴)

`tools/collect_logs.py [--host qnxuser@192.168.2.2] [--remote-dir mermaid/logs/rides] [--local DIR] [--server http://localhost:8080] [--key KEY] [--dry-run]`
(구현됨. API 키는 `--key` 또는 `MERMAID_API_KEY`. `--local`은 SSH 대신 폴더에서)

1. `ssh host 'ls -1 mermaid/logs/rides'` → `<ride_id>.sqlite` 중 `.uploaded`가 없는 것 (`.open`, `.broken`은 이름 규칙에서 빠진다).
2. 파일마다 `scp`로 임시 폴더에 받기 → sha256 계산.
3. `POST /api/rides` (multipart, 파일 + sha256).
4. 서버가 200(이미 있음)이나 201(새로 받음)을 돌려주면 `ssh host 'touch …/<id>.uploaded'`.
   409·422는 표시만 하고 다음 파일로 (표시하지 않으므로 다음 실행에 다시 시도). 하나라도 실패하면 종료 코드 1.
5. 몇 번을 다시 돌려도 결과가 같다 (멱등).

v2(범위 밖): 차에 Wi-Fi가 있으면 P8 업로더가 직접 POST. QNX에 HTTP·TLS 클라이언트가 필요하다.

## 9. 로그 서버 (Python 3.12 + FastAPI)

**선택 이유:** 학습 파이프라인(`ml/`)이 Python이라 주행 파일 읽기 코드를 서버와 `build_real.py`가 같이 쓴다. 차 밖이라 실시간 제약이 없다.

```
server/
  app.py            # FastAPI 라우트 (create_app: 시험은 폴더와 키를 넘겨 만든다)
  db.py             # 중앙 DB 접근 (sqlite3 표준 라이브러리. PostgreSQL로 옮길 때 이 파일만 바꿈)
  ridefile.py       # 주행 파일 읽기·검증 (ml/realdata/mermaid.py도 이것을 씀)
  requirements.txt  # fastapi, uvicorn, python-multipart, numpy, httpx
  data/             # git 제외: incoming/ (받는 중), rides/ (읽기 전용 보관), mermaid.db
tests/server/       # pytest + TestClient. 시험용 주행 파일은 ride_log.cpp의 kSchema를 그대로 읽어 만든다
```

실행 (저장소 루트에서): `MERMAID_API_KEY=… uvicorn server.app:app --host 0.0.0.0 --port 8080`
환경 변수: `MERMAID_DATA`(기본 `server/data`), `MERMAID_MAX_UPLOAD_MB`(기본 1024).
시험: `python3 -m pytest tests/server`, 끝까지는 ctest `log_sim`의 D 단계 (P7이 만든 진짜 파일 → collect_logs → uvicorn).

### 9-1. 중앙 DB (`server/data/mermaid.db`)

```sql
CREATE TABLE ride (
  ride_id TEXT PRIMARY KEY, device_id TEXT, session INTEGER, ride_no INTEGER,
  source TEXT, schema_version INTEGER, model_version TEXT, git_commit TEXT,
  wall_start TEXT, duration_s REAL, n_samples INTEGER, n_hold INTEGER,
  n_cycles INTEGER, n_labels INTEGER, phones TEXT,      -- JSON 배열
  closed_reason TEXT, sha256 TEXT NOT NULL, size_bytes INTEGER,
  received_at TEXT NOT NULL, health TEXT                -- JSON 요약
);
CREATE TABLE cycle (ride_id TEXT, seq INTEGER, t REAL, phone TEXT,
                    p2 REAL, p3 REAL, p4 REAL, p5 REAL, p6 REAL, cal_a REAL, cal_b REAL,
                    PRIMARY KEY (ride_id, seq, phone));
CREATE TABLE decision (ride_id TEXT, seq INTEGER, t REAL, device TEXT, on_ INTEGER, tier INTEGER, changed INTEGER,
                       PRIMARY KEY (ride_id, seq, device));
CREATE TABLE label (ride_id TEXT, t REAL, phone TEXT, misc INTEGER);
CREATE TABLE event (ride_id TEXT, t REAL, sender TEXT, kind TEXT, value TEXT);
```

원본 샘플과 77칸 입력은 중앙 DB에 복사하지 않는다. 주행 파일(`server/data/rides/<ride_id>.sqlite`, 읽기 전용)에 그대로 두고 필요할 때 읽는다.

### 9-2. API v1 (모든 요청에 `X-API-Key` 헤더)

| 메서드 | 경로 | 하는 일 | 응답 |
|---|---|---|---|
| POST | `/api/rides` | 주행 파일 업로드 | 201 새로 받음 / 200 같은 sha256 이미 있음 / 409 같은 id, 다른 sha256 / 422 검증 실패 |
| GET | `/api/rides?device=&since=&has_labels=` | 목록 (`since`는 받은 시각 `received_at` ISO 기준) | `[{ride_id, device_id, wall_start, received_at, duration_s, n_samples, n_cycles, n_labels, phones, closed_reason, health}]` |
| GET | `/api/rides/{id}` | 요약, health, 주행 파일 meta 전체 | JSON |
| GET | `/api/rides/{id}/cycles` | 10초 기록 | `{cycles: [{seq, t, risk: {폰: [p2..p6]}, devices: {기기: {on, tier, changed}}}], labels: [{t, phone, misc}], events: [{t, sender, kind, value}]}` |
| GET | `/api/rides/{id}/samples?from=&to=&cols=` | 원본 구간 [from, to) (최대 10분, 빠진 샘플은 채우지 않음) | `{hz, columns, t, data (NaN은 null), hold (구간 안 인덱스)}` |
| GET | `/api/rides/{id}/file` | 주행 파일 내려받기 | sqlite |
| GET | `/api/health` | 살아 있는지 (키 없이) | `{ok, rides}` |
| POST | `/api/export` | `{ride_ids \| filter, note}` → `build_real.py --source mermaid` 실행 (**6단계**, 아직 없음) | `{version: "v010"}` |

**업로드 검증:** 파일 이름 = `meta.ride_id` (`<device>-<session:05d>-<ride:03d>` 규칙, 경로 문자 거부), `schema_version == 1`,
`PRAGMA integrity_check == ok`, 필수 표 7개 존재, `meta.closed_reason` 있음(닫힌 파일만), sha256 일치, 크기 ≤ `MERMAID_MAX_UPLOAD_MB`.
통과하면 `rides/`로 옮겨 읽기 전용으로 두고, 중앙 DB 표들을 한 트랜잭션으로 채운다(실패하면 옮긴 파일을 지운다).
같은 ride_id를 동시에 올려도 잠금으로 하나만 받는다.

인증: v1은 로컬 네트워크 전용 + API 키 하나. 밖으로 열 때(클라우드) HTTPS와 기기별 키를 다시 정한다.

## 10. 학습 데이터로 내보내기

`ml/realdata/build_real.py`에 **source `mermaid`**를 추가한다 (comma2k19, BATON과 같은 자리).

```
python3 ml/realdata/build_real.py --source mermaid --root server/data/rides [--rides id1,id2] [--features recompute|board]
→ ml/out/runs/vNNN/real_mermaid.npz, real_mermaid_report.json, run.json(kind "real-mermaid", inputs: ride_id 목록 + sha256)
```

- **입력 피처**
  - `recompute`(기본): `samples` → trip 행(t, IMU 6칸, 나머지 NaN) → C++ `featurize`. `feature_order`가 바뀌어도 다시 만들 수 있다.
  - `board`: `infer.x`를 그대로 쓴다. 보드와 같은 값인지 비교하는 데 쓴다.
  - 승객 피처(MSSQ, 민감도)는 그 주행의 `ctx` PROF에서 가져온다.
- **라벨** (SPEC과 같은 규칙): 주기 t마다 `y_k = 1 if (t, t+300] 안의 SYM 중 MISC ≥ k가 있음`.
  그 300초 안에 그 승객의 SYM이 **하나도 없으면 라벨을 NaN**으로 둔다(0으로 채우지 않음). `train.py`는 NaN 라벨 행을 그 k의 학습과 평가에서 뺀다(작은 수정 필요).
- `misc` 칸: 마지막 SYM 값을 최대 120초까지 유지, 넘으면 NaN.
- `person_id` = 폰 ID(영구 기기 ID, `PROTOCOL.md` §4). 사람 단위 분할에 쓴다.
- 기존 `npz` 키(`X, y, t, misc, ride_id, trip_id, person_id, k, feature_names, feature_version`)를 그대로 맞춰 `train.py`, `calibrate.py`, `compare.py`가 바로 읽게 한다.

## 11. Claude 합성 루프와 이어지는 곳 (구현은 나중)

- 이미 있는 `ml/realdata/calibrate.py`(피처마다 KS 거리, 평균 KS = 보정 점수)가 루프의 채점기다. MerMaid 실주행이 세 번째 기준 데이터가 된다.
- 루프: 서버가 만든 **요약 통계** → Claude가 `sim_config.json` / `sim_effects.json` 변경을 허용 범위 안의 JSON 패치로 제안 → `build_dataset.py`(작게) → `calibrate.py` → 점수가 좋아지고 실제 MISC(TU Delft 재생, MerMaid 떼어 둔 사람) 성능이 나빠지지 않을 때만 채택 → `train.py` → `runs.py use`.
- Claude에게 보내지 않는 것: 원본 샘플, 주행별 행, MSSQ·MISC 개인 값, 기기 ID.

## 12. 개인 보정 (B): 사람마다 확률을 다시 맞추기

승객이 자동 개입을 끄거나 직접 켜는 행동과 실제 MISC로, 사람마다 모델 확률을 다시 맞춘다. 덜 민감한 사람은 덜 켜지고, 더 민감한 사람은 더 일찍 켜지게 하는 것이 목표다.

### 12-1. 지금 상태 (`policy.cpp` `on_override`)

- 30초 안에 끄면 기기 허용도 카운터 `n += 1`(저장), 5분 쿨다운, 한 주행에 3번이면 그 주행 동안 끔. 직접 켜면 `n −= 1`.
- `n`이 들어가는 모델 입력 G8(`tol_display`, `tol_phone`)은 v1에서 꺼져 있다(`enabled=false`). 그래서 **다음 판단에 영향이 없다.**
- 자기 폰에서 직접 켜면 민감도(G10) × 1.15. 끌 때는 내려가지 않는다(한쪽 방향).
- 문턱(`thresholds.json`)은 모든 사람이 같다.

### 12-2. 모델

```
p_i,k = σ( a_i · logit(p_k) + b_i )      k = 2..6, 사람 i마다 a_i > 0, b_i 하나씩
```

- a_i, b_i를 k끼리 같이 쓰면 σ(a·logit(p) + b)가 p에 대해 증가함수라서 **p2 ≥ p3 ≥ … ≥ p6 순서가 그대로 유지된다.** 매개변수도 사람당 2개뿐이다.
- 확률을 보정하는 것은 사람별로 문턱을 옮기는 것과 같다. 그래서 P4 정책과 문턱은 바꾸지 않고, 공용 기기(Tier 1·2)와 개인 폰(Tier 3)에 모두 일관되게 적용된다.
- **계층 베이지안(MAP):** 사전분포 log a_i ~ N(0, σ_a²), b_i ~ N(0, σ_b²). σ_a, σ_b는 모든 사람의 데이터로 추정한다. 기록이 적은 사람은 a=1, b=0(전체 모델) 가까이 머문다.

### 12-3. 학습 신호 (P7 주행 파일에서)

| 신호 | 쓰는 방법 | 가중치 |
|---|---|---|
| **MISC (M08 SYM)** — 주 라벨 | §10과 같은 규칙: 주기 t의 y_k = (t, t+300] 안에 MISC ≥ k. 그 구간에 SYM이 없으면 쓰지 않음 | 1 |
| 빠른 끄기 (자동 ON 뒤 30초 안 OVR off) | 그 뒤 300초 동안 SYM이 있고 모두 MISC < input_k면, 끈 시점 주기의 y_{input_k} = 0 | 0.5 |
| 자기 폰에서 직접 켜기 | 켠 시점 주기의 y_{input_k} = 1 (MISC 기록이 그 구간에 없을 때만) | 0.5 |

- **입력 p는 항상 보정 전 모델 출력**이다(`infer.p2..p6`). 그래서 LOG_INFER와 `infer` 표에 보정 전 확률과 적용한 a, b를 같이 남긴다(§6-2, §6-5).
- 그 밖에 끈 이유(방해, 이미 멀미)는 MISC로만 구분한다. OVR 하나만으로는 판단하지 않는다.

### 12-4. 안전 한계와 채택 기준

- a_i ∈ [0.5, 2.0].
- **낮추는 쪽 상한:** 각 Tier 문턱 근처(문턱 ± 0.05)의 p에서 `p − p_i ≤ 0.10`. 넘으면 b_i를 한계까지 올린다. 놓치는 것(멀미)이 귀찮은 것보다 나쁘기 때문이다.
- **채택:** 그 사람의 주행을 하나씩 빼고 맞춰 본 log loss(leave-one-ride-out)가 a=1, b=0보다 좋을 때만 채택한다. 라벨이 있는 주행이 2개 미만이면 a=1, b=0.
- 실제 데이터에서 사람마다 얼마나 움직이는지(평균에 머무는지)는 로그가 쌓인 뒤 검증한다. 시뮬레이터 검증은 이번 범위가 아니다.

### 12-5. 만들고 내려보내는 길

```
로그 서버의 주행 파일 ─▶ ml/personal/fit_calib.py ─▶ ml/out/runs/vNNN/ (kind "calib")
                                                       calib.json { "<phone>": {"a", "b", "n_rides", "n_labels", "loo_gain"} }
                                    tools/collect_logs.py --push-calib vNNN ─ssh─▶ Pi state/calib/<phone>.json
```

- **`state/calib/`은 P4가 읽기만 한다.** P4가 쓰는 `state/profiles/`와 나눈 이유: 밖에서 넣은 값을 P4가 메모리의 옛 프로필로 덮어쓰지 않게 하려는 것이다.
- P4는 폰이 등록될 때(허용도와 같은 시점) `state/calib/<phone>.json`을 읽는다. 다음 등록이나 주행부터 적용된다.
- P4 → P3: `ProfileEntry`에 `cal_a`, `cal_b` 두 칸을 추가한다(PROFILES 회신). 기본 프로필(`default`)은 1, 0이다.
- P3: 단조 보정을 거친 Risk에 위 식을 적용한 뒤 RISK로 보낸다. a=1, b=0이면 식을 건너뛰어 지금과 비트 단위로 같은 값이 나온다.
- 보정이 있는 승객(a≠1 또는 b≠0)에게는 P4의 "직접 켜기 → 민감도 × 1.15" 규칙을 적용하지 않는다. 같은 증거를 두 번 반영하지 않기 위해서다 (팀 확인, §15).
- Python 정책 재생(`ml/train/policy.py`)과 P3 동등성 시험(`model_parity.py`)도 같은 식을 쓰게 맞춘다.

## 13. 시험

| 대상 | Mac (qnxsim) | Pi |
|---|---|---|
| `sample_ring.hpp` | 단위: 넘침, 덮어쓰기 감지, epoch 변경 | – |
| P7 | 재생 P1 + qnxsim P3/P4/P6 대역 → 주행 파일 검사 | 30분 LIVE |
| MPU 변환 | 단위: raw → m/s²·rad/s, R 적용, hold 규칙 | 정지 시 z ≈ 9.81 |
| SYM | `test_comm` 규칙 (역할, 범위, 정수, HELLO 전), `log_rf_sim`에서 label 표까지 | 앱 → label 표 (`ble_check.py --sym`) |
| 폰과 오간 사건 | `test_comm`·`test_devmgr`(사건 내용), `test_ride_log`(주행 전 사건), `log_rf_sim`: 가짜 폰이 HELLO·SCR·OVR on/off·SYM·거부·무응답 DROP·재연결 → 닫힌 주행 파일의 `ctx`·`label` 검사, P7을 멈추고 죽여도 폰이 받는 줄이 같음 | 실제 폰으로 한 주행 뒤 `ctx` 표 |
| 서버 | pytest: 업로드 201/200/409/422, 목록, cycles | – |
| 내보내기 | 작은 가짜 주행 파일 → npz 키와 라벨 규칙 | – |
| 개인 보정 | 가짜 주행 파일 → fit_calib → calib.json → P3 적용. a=1·b=0이면 기존과 비트 동일, 단조성, 안전 한계 | – |
| 끝까지 | 재생 trip → P1~P7 → collect → 서버 → build_real → train 1 fold | LIVE 주행 1회 |

## 14. 개발 순서와 완료 기준

0. **브랜치:** `main`(398237d)에서 만든 `feat/data-logging`. 단계마다 커밋하고, 3단계까지 끝나면 PR.

| 단계 | 내용 | 완료 기준 |
|---|---|---|
| 1 | IMU만 있을 때 성능 (G2~G7 NaN으로 재생, 오프라인) | 현재 모델과 AUC · Tier 지표 비교표. 많이 나쁘면 IMU 전용 모델을 따로 학습할지 결정 |
| 2 | P1 `--live` (4장) + `imu_mount.json` + `sample_ring.hpp` | 보드 30분: 샘플률 100 ± 1 Hz, FIFO 넘침 0, hold 0, 정지 시 \|a\| = 9.81 ± 0.2 |
| 3 | P7 + LOG_* + SQLite + 보관 규칙 | 30분 주행: samples 수 = P1 보낸 수, infer 180주기, 격리 시험 3종 통과 (§6-1) |
| 4 | M08 SYM (프로토콜 · P6 · 앱 · 문서) + 폰과 오간 사건 전부를 `ctx`에 | 앱 입력이 1초 안에 label 표에 들어감, 범위 밖 값은 버려짐. **프로토콜·P6·P5·P7·문서는 구현됨 (맥 시험 통과, Pi는 아직)**, 앱은 진행 중 |
| 5 | 서버 + `collect_logs.py` | pytest 통과, 같은 파일 두 번 올려도 한 번, `.uploaded` 생김 |
| 6 | `build_real.py --source mermaid` + `train.py` NaN 라벨 | 실주행으로 vNNN 생성, `train.py`가 읽음, `recompute`와 `board` 피처 차이 < 1e-6 |
| 7 | 개인 보정 B (§12): `fit_calib.py`, `state/calib`, P4 → P3 `cal_a/cal_b`, P3 적용, `policy.py` 맞춤 | 가짜 로그로 추정 → 배포 → P3 적용 경로 통과, a=1·b=0이면 확률 비트 동일, 안전 한계 시험 통과. 실데이터 효과 검증은 로그가 쌓인 뒤 |

1과 2·3은 서로 기다리지 않는다. 4는 앱 작업이 함께 필요하다. 5·6은 3의 주행 파일 형식이 정해지면 시작할 수 있다(재생 모드 파일로 개발). 7은 3·4·5가 끝난 뒤.

## 15. 확정 필요

| 항목 | 제안 | 정할 사람 |
|---|---|---|
| ~~M08 SYM 추가~~ | **합의됨 (2026-11):** `{"t":"SYM","misc":0~10 정수}`, 터치 입력, 수집 전용, 정책에는 안 씀 | 팀 |
| MISC 알림 간격 | 2분 | 팀 |
| 가속도 범위 | ±4 g | 하드웨어 담당 |
| 보드 장착 방향 | 보드 x = 차 앞 (R = 단위 행렬) | 하드웨어 담당 |
| 주행 경계 | v1 프로세스 시작·종료, v2 정지 5분 | 팀 |
| `cycles.csv` | P7이 안정되면 없애고 `decision` 표로 대신 | 팀 |
| 중앙 DB | v1 SQLite → 차가 여러 대면 PostgreSQL | – |
| 로그 수집 동의 문구 | 앱 첫 실행 때 | 팀 |
| 개인 보정이 있으면 민감도 × 1.15 규칙 끄기 | 끔 (같은 증거 이중 반영 방지) | 팀 |
| 개인 보정의 낮추는 쪽 상한 | 문턱 근처에서 0.10 | 팀 |
| OVR 약한 신호 가중치 | 0.5 | – |
