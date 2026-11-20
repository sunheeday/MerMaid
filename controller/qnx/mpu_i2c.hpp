// QNX I2C 위의 imu::Bus (MPU-6050, 주소 0x68). controller/bringup/mpu_test.c의 devctl 방식 그대로.
// QNX에서만 컴파일된다 (hw/i2c.h). Mac 시험은 common/fake_mpu.hpp를 쓴다.
#pragma once
#ifdef __QNX__
#include <devctl.h>
#include <errno.h>
#include <fcntl.h>
#include <hw/i2c.h>
#include <unistd.h>

#include <cstdint>
#include <cstring>
#include <string>

#include "imu.hpp"

namespace mm::imu {

class I2cBus : public Bus {
public:
    static constexpr uint8_t kAddr = 0x68;
    static constexpr size_t kMaxRead = 128;  // FIFO 8샘플(96바이트)보다 크게

    ~I2cBus() override {
        if (fd_ >= 0) ::close(fd_);
    }
    // speed_hz를 드라이버가 거부하면 100 kHz로 내리고 actual_hz에 적는다
    bool open(const std::string& path, uint32_t speed_hz, uint32_t& actual_hz, std::string& err) {
        fd_ = ::open(path.c_str(), O_RDWR);
        if (fd_ < 0) {
            err = "open " + path + ": " + std::strerror(errno);
            return false;
        }
        actual_hz = speed_hz;
        if (devctl(fd_, DCMD_I2C_SET_BUS_SPEED, &actual_hz, sizeof actual_hz, nullptr) != EOK) {
            actual_hz = 100000;
            devctl(fd_, DCMD_I2C_SET_BUS_SPEED, &actual_hz, sizeof actual_hz, nullptr);
        }
        return true;
    }

    bool write(uint8_t reg, uint8_t val) override {
        struct {
            i2c_send_t h;
            uint8_t b[2];
        } m;
        std::memset(&m, 0, sizeof m);
        m.h.slave.addr = kAddr;
        m.h.slave.fmt = I2C_ADDRFMT_7BIT;
        m.h.len = 2;
        m.h.stop = 1;
        m.b[0] = reg;
        m.b[1] = val;
        return devctl(fd_, DCMD_I2C_SEND, &m, sizeof m, nullptr) == EOK;
    }

    bool read(uint8_t reg, uint8_t* buf, size_t n) override {
        if (n > kMaxRead) return false;
        struct {
            i2c_sendrecv_t h;
            uint8_t b[kMaxRead];
        } m;
        std::memset(&m, 0, sizeof m);
        m.h.slave.addr = kAddr;
        m.h.slave.fmt = I2C_ADDRFMT_7BIT;
        m.h.send_len = 1;
        m.h.recv_len = static_cast<uint32_t>(n);
        m.h.stop = 1;
        m.b[0] = reg;
        if (devctl(fd_, DCMD_I2C_SENDRECV, &m, sizeof m, nullptr) != EOK) return false;
        std::memcpy(buf, m.b, n);
        return true;
    }

private:
    int fd_ = -1;
};

}  // namespace mm::imu
#endif
