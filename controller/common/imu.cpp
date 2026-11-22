// MPU-6050 P1 LIVE 코어. 설명은 imu.hpp, 설계는 docs/controller/DATA_LOGGING.md §4
#include "imu.hpp"

#include "sample_ring.hpp"

#include <cmath>
#include <cstdio>
#include <fstream>
#include <limits>
#include <sstream>
#include <stdexcept>

#include "json_min.hpp"

namespace mm::imu {

namespace {
constexpr double NaN = std::numeric_limits<double>::quiet_NaN();
int16_t be16(const uint8_t* p) { return static_cast<int16_t>((p[0] << 8) | p[1]); }
}  // namespace

Raw decode(const uint8_t* p) {
    Raw r;
    for (int i = 0; i < 3; i++) r.a[i] = be16(p + 2 * i);
    for (int i = 0; i < 3; i++) r.g[i] = be16(p + 6 + 2 * i);
    return r;
}

Mount parse_mount(const std::string& text) {
    const json::Value v = json::parse(text);
    const json::Value& rows = v.at("R");
    if (rows.type != json::Value::Array || rows.arr.size() != 3) throw std::runtime_error("imu_mount: R must be 3x3");
    Mount m;
    for (int i = 0; i < 3; i++) {
        const json::Value& row = rows.arr[i];
        if (row.type != json::Value::Array || row.arr.size() != 3) throw std::runtime_error("imu_mount: R must be 3x3");
        for (int j = 0; j < 3; j++) m.R[i][j] = row.arr[j].number();
    }
    // 회전 행렬인지: R·Rᵀ = I, det = +1 (각각 1e-3 안)
    for (int i = 0; i < 3; i++)
        for (int j = 0; j < 3; j++) {
            double s = 0;
            for (int k = 0; k < 3; k++) s += m.R[i][k] * m.R[j][k];
            if (std::fabs(s - (i == j ? 1.0 : 0.0)) > 1e-3) throw std::runtime_error("imu_mount: R is not orthonormal");
        }
    const auto& R = m.R;
    const double det = R[0][0] * (R[1][1] * R[2][2] - R[1][2] * R[2][1]) -
                       R[0][1] * (R[1][0] * R[2][2] - R[1][2] * R[2][0]) +
                       R[0][2] * (R[1][0] * R[2][1] - R[1][1] * R[2][0]);
    if (std::fabs(det - 1.0) > 1e-3) throw std::runtime_error("imu_mount: det(R) must be +1 (got a reflection?)");
    return m;
}

Mount load_mount(const std::string& path) {
    std::ifstream f(path);
    if (!f) throw std::runtime_error("imu_mount: cannot open " + path);
    std::stringstream ss;
    ss << f.rdbuf();
    return parse_mount(ss.str());
}

static Vec3 rotate(const Mat3& R, const Vec3& v) {
    Vec3 o{};
    for (int i = 0; i < 3; i++) o[i] = R[i][0] * v[0] + R[i][1] * v[1] + R[i][2] * v[2];
    return o;
}

Sample convert(const Raw& r, const Mount& m, const Vec3& bias_dps) {
    Vec3 a{}, g{};
    for (int i = 0; i < 3; i++) {
        a[i] = r.a[i] / kAccelLsbPerG * kG;
        g[i] = (r.g[i] / kGyroLsbPerDps - bias_dps[i]) * kPi / 180.0;
    }
    return {rotate(m.R, a), rotate(m.R, g)};
}

void fill_row(Row& row, uint64_t seq, const Sample& s) {
    row.fill(NaN);
    row[proto::C_T] = static_cast<double>(seq) / kRateHz;
    row[proto::C_AX] = s.a[0];
    row[proto::C_AY] = s.a[1];
    row[proto::C_AZ] = s.a[2];
    row[proto::C_GX] = s.g[0];
    row[proto::C_GY] = s.g[1];
    row[proto::C_GZ] = s.g[2];
}

Still check_still(const Raw* w, size_t n) {
    Still s;
    if (n < 2) return s;
    double m1 = 0, m2 = 0;
    Vec3 g1{}, g2{};
    for (size_t k = 0; k < n; k++) {
        double sq = 0;
        for (int i = 0; i < 3; i++) {
            const double a = w[k].a[i] / kAccelLsbPerG * kG;
            sq += a * a;
            const double g = w[k].g[i] / kGyroLsbPerDps;
            g1[i] += g;
            g2[i] += g * g;
        }
        const double mag = std::sqrt(sq);
        m1 += mag;
        m2 += mag * mag;
    }
    auto sd = [n](double sum, double sum2) {
        const double mean = sum / n;
        return std::sqrt(std::max(0.0, sum2 / n - mean * mean));
    };
    s.acc_mag_mean = m1 / n;
    s.acc_mag_sd = sd(m1, m2);
    s.still = s.acc_mag_sd < kStillAccSd;
    for (int i = 0; i < 3; i++) {
        s.gyro_mean_dps[i] = g1[i] / n;
        s.gyro_sd_dps[i] = sd(g1[i], g2[i]);
        if (s.gyro_sd_dps[i] >= kStillGyroSdDps) s.still = false;
    }
    return s;
}

bool fifo_reset(Bus& bus) {
    return bus.write(reg::USER_CTRL, 0x00) && bus.write(reg::USER_CTRL, kUserFifoReset) &&
           bus.write(reg::USER_CTRL, kUserFifoEnable);
}

bool init(Bus& bus, const std::function<void(int)>& sleep_ms, std::string& err) {
    uint8_t id = 0;
    if (!bus.read(reg::WHO_AM_I, &id, 1)) {
        err = "WHO_AM_I read failed";
        return false;
    }
    if (id != kWhoAmI) {
        char buf[64];
        std::snprintf(buf, sizeof buf, "WHO_AM_I = 0x%02x (expected 0x68)", id);
        err = buf;
        return false;
    }
    if (!bus.write(reg::PWR_MGMT_1, kPwrReset)) {
        err = "reset failed";
        return false;
    }
    sleep_ms(100);
    struct W {
        uint8_t reg, val;
        const char* what;
    };
    const W seq[] = {
        {reg::PWR_MGMT_1, kPwrClkGyroX, "PWR_MGMT_1"},
        {reg::SMPLRT_DIV, kSmplrtDiv100Hz, "SMPLRT_DIV"},
        {reg::CONFIG, kDlpf44Hz, "CONFIG"},
        {reg::GYRO_CONFIG, kGyro250dps, "GYRO_CONFIG"},
        {reg::ACCEL_CONFIG, kAccel4g, "ACCEL_CONFIG"},
        {reg::INT_ENABLE, 0x00, "INT_ENABLE"},
        {reg::FIFO_EN, kFifoAccelGyro, "FIFO_EN"},
    };
    for (const W& w : seq) {
        if (!bus.write(w.reg, w.val)) {
            err = std::string("write ") + w.what + " failed";
            return false;
        }
        if (w.reg == reg::PWR_MGMT_1) sleep_ms(10);  // PLL 안정
    }
    if (!fifo_reset(bus)) {
        err = "FIFO enable failed";
        return false;
    }
    err.clear();
    return true;
}

Tick read_fifo(Bus& bus) {
    Tick t;
    uint8_t st = 0, cnt[2] = {0, 0};
    if (!bus.read(reg::INT_STATUS, &st, 1) || !bus.read(reg::FIFO_COUNTH, cnt, 2)) {
        t.ok = false;
        return t;
    }
    const int count = (cnt[0] << 8) | cnt[1];
    if ((st & kIntFifoOverflow) || count >= kFifoBytes) {
        t.overflow = true;
        return t;
    }
    if (count % kSampleBytes) {
        t.misaligned = true;
        t.discarded = static_cast<size_t>((count + kSampleBytes - 1) / kSampleBytes);
        return t;
    }
    int n = count / kSampleBytes;
    uint8_t buf[kMaxChunkSamples * kSampleBytes];
    while (n > 0) {
        const int k = n < kMaxChunkSamples ? n : kMaxChunkSamples;
        if (!bus.read(reg::FIFO_R_W, buf, static_cast<size_t>(k * kSampleBytes))) {
            t.ok = false;  // 몇 바이트를 꺼냈는지 모름: 다음 박자의 바이트 수 검사가 잡는다
            return t;
        }
        for (int i = 0; i < k; i++) t.samples.push_back(decode(buf + i * kSampleBytes));
        n -= k;
    }
    return t;
}

// ---------------- Acquirer ----------------

Acquirer::Acquirer(Bus& bus, Config cfg, std::function<void(int)> sleep_ms)
    : bus_(bus), cfg_(std::move(cfg)), sleep_ms_(std::move(sleep_ms)) {}

void Acquirer::begin_calib(uint64_t now) {
    state_ = CALIB;
    calib_start_ = now;
    last_ok_ = now;
    window_.clear();
}

void Acquirer::start_ride(uint64_t now, std::vector<Out>& out) {
    state_ = RUN;
    seq_ = 0;
    ride_start_ = now;
    has_last_ = false;
    st_.rides++;
    st_.rate_hz = 0;
    Out o;
    o.kind = Out::RIDE_START;
    o.row = ring::start_info_unknown();  // 링에 주행 정보로 들어간다 (sample_ring.hpp StartInfo)
    o.row[ring::S_BIAS_X] = bias_[0];
    o.row[ring::S_BIAS_Y] = bias_[1];
    o.row[ring::S_BIAS_Z] = bias_[2];
    o.row[ring::S_BIAS_UNCAL] = st_.bias_uncalibrated ? 1 : 0;
    o.row[ring::S_ACCEL_RANGE_G] = 4;
    o.row[ring::S_GYRO_RANGE_DPS] = 250;
    out.push_back(o);
}

void Acquirer::restart(std::vector<Out>& out) {
    if (state_ == RUN) {
        Out o;
        o.kind = Out::RIDE_END;
        o.seq = seq_;
        out.push_back(o);
    }
    st_.restarts++;
    state_ = INIT;
    next_init_ = 0;
    window_.clear();
    has_last_ = false;
}

void Acquirer::emit(const Raw& r, uint32_t flags, std::vector<Out>& out) {
    Out o;
    o.kind = Out::SAMPLE;
    o.flags = flags;
    o.seq = seq_++;
    fill_row(o.row, o.seq, convert(r, cfg_.mount, bias_));
    out.push_back(o);
    last_ = r;
    has_last_ = true;
    st_.samples++;
    if (flags & FLAG_HOLD) st_.hold++;
}

std::vector<Out> Acquirer::step(uint64_t now) {
    std::vector<Out> out;
    if (state_ == FATAL) return out;
    if (state_ == INIT) {
        if (now < next_init_) return out;
        if (init(bus_, sleep_ms_, err_)) {
            init_fails_ = 0;
            begin_calib(now);
        } else {
            st_.init_fails++;
            if (++init_fails_ >= kMaxInitFails) state_ = FATAL;
            next_init_ = now + kInitRetryNs;
        }
        return out;
    }

    Tick t = read_fifo(bus_);
    if (t.ok) {
        last_ok_ = now;
    } else {
        st_.i2c_error_ticks++;
        if (now - last_ok_ > kErrorRestartNs) {  // FIFO가 넘쳤을 수도 있는 긴 끊김: 주행을 끊고 다시 시작
            err_ = "I2C errors for over 1 s";
            restart(out);
            return out;
        }
    }
    if (t.overflow) {  // 샘플을 잃었고 몇 개인지 모름 (0.85초 넘게 못 읽음): 주행을 끊는다
        st_.overflow++;
        err_ = "FIFO overflow";
        restart(out);
        return out;
    }
    if (t.misaligned) {
        st_.misaligned++;
        if (!fifo_reset(bus_)) st_.i2c_error_ticks++;
    }

    if (state_ == CALIB) {
        if (t.misaligned) window_.clear();  // 연속이 깨짐
        window_.insert(window_.end(), t.samples.begin(), t.samples.end());
        while (window_.size() >= static_cast<size_t>(kCalibSamples)) {
            const Still s = check_still(window_.data(), kCalibSamples);
            if (s.still) {
                bias_ = s.gyro_mean_dps;
                new_bias_ = true;
                window_.clear();
                start_ride(now, out);
                return out;
            }
            window_.erase(window_.begin(), window_.begin() + kCalibSamples / 2);  // 1초씩 밀기
        }
        if (now - calib_start_ >= kCalibTimeoutNs) {
            if (cfg_.has_saved_bias) {
                bias_ = cfg_.saved_bias_dps;
            } else {
                bias_ = {0, 0, 0};
                st_.bias_uncalibrated = true;
            }
            window_.clear();
            start_ride(now, out);
        }
        return out;
    }

    // RUN
    for (const Raw& r : t.samples) emit(r, 0, out);
    if (t.misaligned) {
        if (has_last_ && t.discarded <= static_cast<size_t>(kHoldMax)) {
            const Raw last = last_;
            for (size_t i = 0; i < t.discarded; i++) emit(last, FLAG_HOLD, out);
        } else {
            err_ = "FIFO misaligned, too many samples lost";
            restart(out);
            return out;
        }
    }
    if (now > ride_start_ + 1000000000ULL) st_.rate_hz = seq_ * 1e9 / static_cast<double>(now - ride_start_);
    return out;
}

std::vector<Out> Acquirer::stop() {
    std::vector<Out> out;
    if (state_ == RUN) {
        Out o;
        o.kind = Out::RIDE_END;
        o.seq = seq_;
        out.push_back(o);
        state_ = INIT;
    }
    return out;
}

bool Acquirer::take_new_bias(Vec3& dps) {
    if (!new_bias_) return false;
    new_bias_ = false;
    dps = bias_;
    return true;
}

}  // namespace mm::imu
