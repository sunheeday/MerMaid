/* MPU-6050 bring-up test for QNX on Raspberry Pi 4
 *
 * 설계 §4.3 레지스터 설정으로 100 Hz 고정 주기로 14바이트(가속도+온도+자이로)를 읽는다.
 *   ./mpu          화면 출력 (10 Hz로 솎아서 표시: g, dps, °C)
 *   ./mpu --csv    매 샘플 CSV: t_ns,ax,ay,az,gx,gy,gz,dt_us  (m/s^2, dps)
 *                  예) ./mpu --csv > imu.csv
 */
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <time.h>
#include <devctl.h>
#include <hw/i2c.h>

#define ADDR      0x68
#define PERIOD_NS 10000000L          /* 10 ms = 100 Hz */
#define G         9.80665

static int fd;

static int wr(uint8_t reg, uint8_t val) {
    struct { i2c_send_t h; uint8_t b[2]; } m;
    memset(&m, 0, sizeof(m));
    m.h.slave.addr = ADDR; m.h.slave.fmt = I2C_ADDRFMT_7BIT;
    m.h.len = 2; m.h.stop = 1; m.b[0] = reg; m.b[1] = val;
    return devctl(fd, DCMD_I2C_SEND, &m, sizeof(m), NULL);
}

static int rd(uint8_t reg, uint8_t *out, int n) {
    struct { i2c_sendrecv_t h; uint8_t b[16]; } m;
    memset(&m, 0, sizeof(m));
    m.h.slave.addr = ADDR; m.h.slave.fmt = I2C_ADDRFMT_7BIT;
    m.h.send_len = 1; m.h.recv_len = n; m.h.stop = 1; m.b[0] = reg;
    int r = devctl(fd, DCMD_I2C_SENDRECV, &m, sizeof(m), NULL);
    if (r == EOK) memcpy(out, m.b, n);
    return r;
}

static int16_t be16(const uint8_t *p) { return (int16_t)((p[0] << 8) | p[1]); }

static int64_t now_ns(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (int64_t)ts.tv_sec * 1000000000LL + ts.tv_nsec;
}

int main(int argc, char **argv) {
    int csv = (argc > 1 && strcmp(argv[1], "--csv") == 0);
    uint8_t buf[14], id;
    int r;

    fd = open("/dev/i2c1", O_RDWR);
    if (fd < 0) { perror("open /dev/i2c1"); return 1; }
    uint32_t speed = 100000;
    devctl(fd, DCMD_I2C_SET_BUS_SPEED, &speed, sizeof(speed), NULL);

    if ((r = rd(0x75, &id, 1)) != EOK) { fprintf(stderr, "WHO_AM_I: %s\n", strerror(r)); return 1; }
    fprintf(stderr, "WHO_AM_I = 0x%02x%s\n", id, id == 0x68 ? " (MPU-6050)" : " (예상과 다름: 칩 확인 필요)");

    /* 설계 §4.3 */
    wr(0x6B, 0x01);  usleep(100000);  /* PWR_MGMT_1: 슬립 해제, 클럭 = 자이로 PLL */
    wr(0x19, 9);                      /* SMPLRT_DIV: 1 kHz / (1+9) = 100 Hz */
    wr(0x1A, 0x03);                   /* CONFIG: DLPF 약 44 Hz */
    wr(0x1C, 0x00);                   /* ACCEL_CONFIG: ±2 g,   16384 LSB/g */
    wr(0x1B, 0x00);                   /* GYRO_CONFIG:  ±250 dps, 131 LSB/dps */

    if (csv) printf("t_ns,ax,ay,az,gx,gy,gz,dt_us\n");

    struct timespec next;
    clock_gettime(CLOCK_MONOTONIC, &next);
    int64_t prev = 0;
    for (unsigned long k = 0;; k++) {
        next.tv_nsec += PERIOD_NS;
        if (next.tv_nsec >= 1000000000L) { next.tv_nsec -= 1000000000L; next.tv_sec++; }
        clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &next, NULL);

        int64_t t = now_ns();
        if ((r = rd(0x3B, buf, 14)) != EOK) { fprintf(stderr, "read: %s\n", strerror(r)); break; }
        double ax = be16(buf + 0)  / 16384.0, ay = be16(buf + 2)  / 16384.0, az = be16(buf + 4)  / 16384.0;
        double tc = be16(buf + 6)  / 340.0 + 36.53;
        double gx = be16(buf + 8)  / 131.0,   gy = be16(buf + 10) / 131.0,   gz = be16(buf + 12) / 131.0;
        long dt_us = prev ? (long)((t - prev) / 1000) : 0;   /* 샘플 간격: 지터 확인용 */
        prev = t;

        if (csv) {
            printf("%lld,%.4f,%.4f,%.4f,%.3f,%.3f,%.3f,%ld\n", (long long)t,
                   ax * G, ay * G, az * G, gx, gy, gz, dt_us);
        } else if (k % 10 == 0) {
            printf("A[g] %6.2f %6.2f %6.2f | G[dps] %7.2f %7.2f %7.2f | %5.1f C | dt %5ld us\n",
                   ax, ay, az, gx, gy, gz, tc, dt_us);
        }
    }
    return 0;
}
