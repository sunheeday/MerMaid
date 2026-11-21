// 가짜 MPU-6050 (imu::Bus): 실제 IMU 없이 P1 LIVE를 시험한다. tests/test_imu.cpp와 Mac의 p1_acq --fake-imu가 쓴다.
// 시각은 부르는 쪽이 set_time(now_ns)으로 준다. FIFO를 켠 뒤부터 100 Hz로 샘플을 FIFO(1024바이트)에 쌓고,
// 가득 차면 가장 오래된 바이트를 버리고 INT_STATUS의 넘침 비트를 세운다 (데이터시트 동작).
// 고장 넣기: fail_until_ns(그때까지 모든 I2C 실패), inject_stray_byte(바이트 수를 12의 배수가 아니게), who_am_i.
#pragma once
#include <cmath>
#include <cstdint>
#include <cstring>
#include <deque>
#include <functional>

#include "imu.hpp"

namespace mm::imu {

class FakeMpu : public Bus {
public:
    // 샘플 번호 → 센서 축 원값. 기본: 수평 정지 (z = +1 g), 자이로 바이어스 (1.0, -0.5, 0.25) °/s
    std::function<Raw(uint64_t)> signal = [](uint64_t) {
        Raw r{};
        r.a[2] = static_cast<int16_t>(kAccelLsbPerG);
        r.g[0] = static_cast<int16_t>(std::lround(1.0 * kGyroLsbPerDps));
        r.g[1] = static_cast<int16_t>(std::lround(-0.5 * kGyroLsbPerDps));
        r.g[2] = static_cast<int16_t>(std::lround(0.25 * kGyroLsbPerDps));
        return r;
    };
    uint8_t who_am_i = kWhoAmI;
    uint64_t fail_until_ns = 0;
    double rate_hz = kRateHz;  // 센서 클럭 (실제 칩은 ±1% 안팎으로 다를 수 있음)

    void set_time(uint64_t now) {
        now_ = now;
        if (!fifo_on_) return;
        const uint64_t due = static_cast<uint64_t>((now_ - fifo_t0_) * 1e-9 * rate_hz);
        while (made_ < due) push_sample(signal(index_++)), made_++;
    }
    void inject_stray_byte() { push_byte(0xAA); }
    uint64_t produced() const { return index_; }  // 지금까지 만든 샘플 수 (리셋해도 이어짐)
    size_t fifo_bytes() const { return fifo_.size(); }

    bool write(uint8_t reg, uint8_t val) override {
        if (failing()) return false;
        regs_[reg] = val;
        if (reg == reg::PWR_MGMT_1 && (val & kPwrReset)) {
            std::memset(regs_, 0, sizeof regs_);
            fifo_.clear();
            fifo_on_ = false;
            overflow_ = false;
        }
        if (reg == reg::USER_CTRL) {
            if (val & kUserFifoReset) {
                fifo_.clear();
                overflow_ = false;
            }
            const bool on = (val & kUserFifoEnable) && regs_[reg::FIFO_EN] == kFifoAccelGyro;
            if (on && !fifo_on_) {
                fifo_t0_ = now_;
                made_ = 0;
            }
            fifo_on_ = on;
        }
        return true;
    }

    bool read(uint8_t reg, uint8_t* buf, size_t n) override {
        if (failing()) return false;
        if (reg == reg::WHO_AM_I && n == 1) {
            buf[0] = who_am_i;
        } else if (reg == reg::INT_STATUS && n == 1) {
            buf[0] = overflow_ ? kIntFifoOverflow : 0;
            overflow_ = false;  // 읽으면 지워짐
        } else if (reg == reg::FIFO_COUNTH && n == 2) {
            buf[0] = static_cast<uint8_t>(fifo_.size() >> 8);
            buf[1] = static_cast<uint8_t>(fifo_.size() & 0xFF);
        } else if (reg == reg::FIFO_R_W) {
            for (size_t i = 0; i < n; i++) {
                buf[i] = fifo_.empty() ? 0 : fifo_.front();
                if (!fifo_.empty()) fifo_.pop_front();
            }
        } else {
            for (size_t i = 0; i < n; i++) buf[i] = regs_[(reg + i) & 0x7F];
        }
        return true;
    }

    uint8_t reg_value(uint8_t reg) const { return regs_[reg & 0x7F]; }

private:
    bool failing() const { return now_ < fail_until_ns; }
    void push_byte(uint8_t b) {
        if (fifo_.size() >= static_cast<size_t>(kFifoBytes)) {
            fifo_.pop_front();
            overflow_ = true;
        }
        fifo_.push_back(b);
    }
    void push_sample(const Raw& r) {
        const int16_t v[6] = {r.a[0], r.a[1], r.a[2], r.g[0], r.g[1], r.g[2]};
        for (int16_t x : v) {
            push_byte(static_cast<uint8_t>((static_cast<uint16_t>(x) >> 8) & 0xFF));
            push_byte(static_cast<uint8_t>(static_cast<uint16_t>(x) & 0xFF));
        }
    }

    uint8_t regs_[128] = {};
    std::deque<uint8_t> fifo_;
    bool fifo_on_ = false, overflow_ = false;
    uint64_t now_ = 0, fifo_t0_ = 0, made_ = 0, index_ = 0;
};

}  // namespace mm::imu
