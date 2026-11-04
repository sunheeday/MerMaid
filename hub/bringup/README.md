# hub/bringup — 하드웨어 동작 확인용 테스트

허브(`hub/main.cpp`)와 별개로, 보드에서 센서·장치가 동작하는지 따로 확인하는 독립 프로그램을 둡니다.

## mpu_test.c — MPU-6050 (I2C)

WHO_AM_I(0x75)를 읽어 칩을 확인하고, 가속도를 g 단위로 출력합니다.

### 배선 (Pi 4)

| MPU-6050 | Pi 4 |
|---|---|
| VCC | 3.3 V (핀 1) |
| GND | GND (핀 6) |
| SDA | GPIO 2 (핀 3) |
| SCL | GPIO 3 (핀 5) |
| XDA, XCL, AD0, INT | 연결 안 함 (AD0 미연결 → 주소 0x68) |

### 빌드·실행 (Pi에서 직접)

QSTI 이미지의 `/usr/bin`에 `gcc`가 있어 보드에서 바로 빌드합니다.

```sh
ls /dev/i2c*            # /dev/i2c1 이 있어야 함
gcc -o mpu mpu_test.c
./mpu                   # Ctrl+C로 종료
```

정상이면 `WHO_AM_I = 0x68`이 나오고, 가만히 두면 Z ≈ 1.0 g입니다.

### 결과

- 2026-11-05: 가속도 X, Y, Z 출력 확인. <!-- TODO: WHO_AM_I 값, 정지 상태 Z 값 기록 -->

### 겪은 문제

- 보드의 `vi`가 st 터미널에서 잘 동작하지 않음 → `cat > file << 'EOF'`로 작성하거나 PC에서 `scp`로 전송
- `qcc`는 보드에 없음 (PC SDP용). 보드에서는 `gcc` 사용
- QNX `sed`는 `-i`를 지원하지 않음 → `sed '...' a.c > b.c && mv b.c a.c`
