#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <devctl.h>
#include <hw/i2c.h>

#define ADDR 0x68
static int fd;

int wr(uint8_t reg, uint8_t val) {
    struct { i2c_send_t h; uint8_t b[2]; } m;
    m.h.slave.addr = ADDR; m.h.slave.fmt = I2C_ADDRFMT_7BIT;
    m.h.len = 2; m.h.stop = 1; m.b[0] = reg; m.b[1] = val;
    return devctl(fd, DCMD_I2C_SEND, &m, sizeof(m), NULL);
}

int rd(uint8_t reg, uint8_t *out, int n) {
    struct { i2c_sendrecv_t h; uint8_t b[16]; } m;
    m.h.slave.addr = ADDR; m.h.slave.fmt = I2C_ADDRFMT_7BIT;
    m.h.send_len = 1; m.h.recv_len = n; m.h.stop = 1; m.b[0] = reg;
    int r = devctl(fd, DCMD_I2C_SENDRECV, &m, sizeof(m), NULL);
    if (r == EOK) memcpy(out, m.b, n);
    return r;
}

int main(void) {
    uint8_t buf[6], id;
    fd = open("/dev/i2c1", O_RDWR);
    if (fd < 0) { perror("open /dev/i2c1"); return 1; }
    uint32_t speed = 100000;
    devctl(fd, DCMD_I2C_SET_BUS_SPEED, &speed, sizeof(speed), NULL);

    if (rd(0x75, &id, 1) != EOK) { perror("WHO_AM_I"); return 1; }
    printf("WHO_AM_I = 0x%02x\n", id);
    wr(0x6B, 0x00);   /* sleep 해제 */

    while (1) {
        if (rd(0x3B, buf, 6) != EOK) { perror("read"); break; }
        int16_t ax = (buf[0]<<8)|buf[1], ay = (buf[2]<<8)|buf[3], az = (buf[4]<<8)|buf[5];
        printf("X=%6.2f  Y=%6.2f  Z=%6.2f g\n", ax/16384.0, ay/16384.0, az/16384.0);
        usleep(200000);
    }
    return 0;
}
