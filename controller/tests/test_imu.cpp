// P1 LIVE IMU 코어 테스트 (가짜 MPU, 시각은 직접 진행): 변환, 장착 행렬, 초기화 레지스터, 보정, 주행 중 복구 규칙.
// 설계: docs/controller/DATA_LOGGING.md §4
#include <cmath>
#include <cstdio>
#include <string>
#include <vector>

#include "fake_mpu.hpp"
#include "imu.hpp"
#include "sample_ring.hpp"

using namespace mm;
using namespace mm::imu;

static int failures = 0;
#define CHECK(cond, ...)                                     \
    do {                                                     \
        if (!(cond)) {                                       \
            failures++;                                      \
            std::printf("FAIL %s:%d: ", __FILE__, __LINE__); \
            std::printf(__VA_ARGS__);                        \
            std::printf("\n");                               \
        }                                                    \
    } while (0)

static constexpr uint64_t MS = 1000000ULL;
static void no_sleep(int) {}

// 시뮬레이션: [from, to) 동안 10 ms마다 set_time → step. 나온 것을 모은다
struct Sim {
    FakeMpu mpu;
    Acquirer acq;
    uint64_t now = 0;
    std::vector<Out> outs;
    explicit Sim(Acquirer::Config cfg = {}) : acq(mpu, cfg, no_sleep) {}
    void run_until(uint64_t t_ns) {
        while (now < t_ns) {
            now += 10 * MS;
            mpu.set_time(now);
            auto o = acq.step(now);
            outs.insert(outs.end(), o.begin(), o.end());
        }
    }
    void skip_to(uint64_t t_ns) { now = t_ns; }  // P1이 못 깨어난 시간 (FIFO만 쌓임)
    size_t count(Out::Kind k) const {
        size_t n = 0;
        for (const auto& o : outs) n += o.kind == k;
        return n;
    }
    std::vector<const Out*> samples() const {
        std::vector<const Out*> v;
        for (const auto& o : outs)
            if (o.kind == Out::SAMPLE) v.push_back(&o);
        return v;
    }
};

// 같은 주행 안의 샘플 번호가 0부터 빈틈없이 이어지는지
static bool contiguous(const std::vector<Out>& outs) {
    uint64_t expect = 0;
    for (const auto& o : outs) {
        if (o.kind == Out::RIDE_START) expect = 0;
        if (o.kind != Out::SAMPLE) continue;
        if (o.seq != expect) return false;
        if (std::fabs(o.row[proto::C_T] - o.seq / 100.0) > 1e-12) return false;
        expect++;
    }
    return true;
}

static void decode_convert() {
    const uint8_t b[12] = {0x20, 0x00, 0xE0, 0x00, 0x00, 0x01, 0x00, 0x83, 0xFF, 0x7D, 0x80, 0x00};
    const Raw r = decode(b);
    CHECK(r.a[0] == 8192 && r.a[1] == -8192 && r.a[2] == 1, "accel big-endian (%d %d %d)", r.a[0], r.a[1], r.a[2]);
    CHECK(r.g[0] == 131 && r.g[1] == -131 && r.g[2] == -32768, "gyro big-endian");

    Raw x{};
    x.a[2] = 8192;  // +1 g
    x.g[0] = 131;   // 1 °/s
    Sample s = convert(x, Mount{}, {0, 0, 0});
    CHECK(std::fabs(s.a[2] - 9.80665) < 1e-12, "1 g = 9.80665 m/s^2 (%.6f)", s.a[2]);
    CHECK(std::fabs(s.g[0] - imu::kPi / 180.0) < 1e-12, "1 dps = pi/180 rad/s");
    s = convert(x, Mount{}, {1.0, 0, 0});
    CHECK(std::fabs(s.g[0]) < 1e-12, "bias removed before rotation");

    // 보드를 z축으로 90° 돌려 장착: 센서 x가 차의 왼쪽(+y)
    Mount m;
    m.R = {{{0, -1, 0}, {1, 0, 0}, {0, 0, 1}}};
    x = Raw{};
    x.a[0] = 4096;  // 센서 x로 0.5 g
    s = convert(x, m, {0, 0, 0});
    CHECK(std::fabs(s.a[0]) < 1e-12 && std::fabs(s.a[1] - 0.5 * kG) < 1e-12, "mount rotation (%.3f %.3f)", s.a[0], s.a[1]);

    Row row;
    fill_row(row, 250, s);
    CHECK(row[proto::C_T] == 2.5, "t = seq / 100");
    CHECK(std::isnan(row[proto::C_SPEED]) && std::isnan(row[proto::C_NOISE_LOWFREQ_LEVEL]), "other columns NaN");
    int finite = 0;
    for (double v : row) finite += !std::isnan(v);
    CHECK(finite == 7, "exactly t + 6 IMU columns (%d)", finite);
}

static void mount_parse() {
    bool threw = false;
    try {
        Mount m = load_mount(std::string(MM_CONFIG_DIR) + "/imu_mount.json");
        CHECK(m.R[0][0] == 1 && m.R[1][1] == 1 && m.R[2][2] == 1, "repo config is identity");
    } catch (const std::exception& e) {
        CHECK(false, "repo imu_mount.json: %s", e.what());
    }
    try {
        parse_mount(R"({"R": [[-1,0,0],[0,1,0],[0,0,1]]})");
    } catch (const std::exception&) {
        threw = true;
    }
    CHECK(threw, "reflection rejected");
    threw = false;
    try {
        parse_mount(R"({"R": [[1,0,0],[0,2,0],[0,0,1]]})");
    } catch (const std::exception&) {
        threw = true;
    }
    CHECK(threw, "non-orthonormal rejected");
    threw = false;
    try {
        parse_mount(R"({"R": [[1,0,0],[0,1,0]]})");
    } catch (const std::exception&) {
        threw = true;
    }
    CHECK(threw, "2x3 rejected");
}

static void init_registers() {
    FakeMpu mpu;
    std::string err;
    CHECK(init(mpu, no_sleep, err), "init ok: %s", err.c_str());
    CHECK(mpu.reg_value(reg::ACCEL_CONFIG) == 0x08, "±4 g");
    CHECK(mpu.reg_value(reg::GYRO_CONFIG) == 0x00, "±250 dps");
    CHECK(mpu.reg_value(reg::SMPLRT_DIV) == 9, "100 Hz");
    CHECK(mpu.reg_value(reg::CONFIG) == 0x03, "DLPF 44 Hz");
    CHECK(mpu.reg_value(reg::FIFO_EN) == 0x78, "FIFO: accel + gyro, no temperature");
    CHECK(mpu.reg_value(reg::USER_CTRL) == 0x40, "FIFO enabled");
    CHECK(mpu.reg_value(reg::PWR_MGMT_1) == 0x01, "awake, gyro PLL clock");

    FakeMpu other;
    other.who_am_i = 0x70;
    CHECK(!init(other, no_sleep, err) && err.find("0x70") != std::string::npos, "wrong chip: %s", err.c_str());
}

static void normal_ride() {
    Sim sim;
    sim.run_until(1990 * MS);
    CHECK(sim.count(Out::RIDE_START) == 0, "no ride before 2 s of calibration");
    sim.run_until(2100 * MS);
    CHECK(sim.count(Out::RIDE_START) == 1, "ride starts after calibration");
    Vec3 b;
    CHECK(sim.acq.take_new_bias(b), "new bias reported");
    CHECK(std::fabs(b[0] - 1.0) < 1e-9 && std::fabs(b[1] - (-66 / 131.0)) < 1e-9, "bias = still mean (%.4f %.4f %.4f)",
          b[0], b[1], b[2]);
    CHECK(!sim.acq.take_new_bias(b), "reported once");

    const size_t before = sim.samples().size();
    sim.run_until(12100 * MS);
    const auto s = sim.samples();
    CHECK(s.size() - before >= 999 && s.size() - before <= 1001, "100 Hz: %zu samples in 10 s", s.size() - before);
    CHECK(contiguous(sim.outs), "seq contiguous, t = seq/100");
    const Out& last = *s.back();
    CHECK(std::fabs(last.row[proto::C_AZ] - kG) < 1e-9, "still: az = g (%.6f)", last.row[proto::C_AZ]);
    CHECK(std::fabs(last.row[proto::C_GX]) < 1e-12 && std::fabs(last.row[proto::C_GY]) < 1e-12 &&
              std::fabs(last.row[proto::C_GZ]) < 1e-12,
          "gyro bias removed");
    CHECK(std::fabs(sim.acq.stats().rate_hz - 100) < 1.5, "rate %.2f Hz", sim.acq.stats().rate_hz);

    const auto end = sim.acq.stop();
    CHECK(end.size() == 1 && end[0].kind == Out::RIDE_END && end[0].seq == s.size(), "stop: RIDE_END with count");
    CHECK(sim.acq.stop().empty(), "stop twice: nothing");
}

static void late_wakeup_no_loss() {
    Sim sim;
    sim.run_until(3000 * MS);
    const uint64_t made_before = sim.mpu.produced();
    sim.skip_to(3500 * MS);  // P1이 0.5초 못 깨어남: FIFO에 50개
    sim.run_until(5000 * MS);
    CHECK(contiguous(sim.outs), "no gap after late wakeup");
    CHECK(sim.acq.stats().restarts == 0 && sim.acq.stats().hold == 0, "no restart, no hold");
    // 주행 샘플 수 = 주행 시작 뒤 만든 샘플 수 (FIFO에 남은 것 제외)
    CHECK(sim.mpu.produced() - made_before >= 199, "sensor kept producing");
    CHECK(sim.mpu.fifo_bytes() < 24, "FIFO drained (%zu bytes left)", sim.mpu.fifo_bytes());
}

static void short_i2c_error() {
    Sim sim;
    sim.run_until(3000 * MS);
    sim.mpu.fail_until_ns = 3600 * MS;  // 0.6초 I2C 실패
    sim.run_until(5000 * MS);
    const Stats& st = sim.acq.stats();
    CHECK(st.i2c_error_ticks >= 50, "error ticks counted (%llu)", (unsigned long long)st.i2c_error_ticks);
    CHECK(st.restarts == 0 && sim.count(Out::RIDE_START) == 1, "short error: same ride");
    CHECK(contiguous(sim.outs), "short error: FIFO kept the samples");
    const auto s = sim.samples();
    CHECK(s.size() >= 290, "about 3 s of samples (%zu)", s.size());
}

static void long_i2c_error_restarts() {
    Sim sim;
    sim.run_until(3000 * MS);
    sim.mpu.fail_until_ns = 4500 * MS;  // 1.5초
    sim.run_until(4400 * MS);
    CHECK(sim.count(Out::RIDE_END) == 1, "RIDE_END after 1 s of errors");
    CHECK(sim.acq.stats().restarts == 1, "restart counted");
    sim.run_until(8000 * MS);
    CHECK(sim.count(Out::RIDE_START) == 2, "re-init, re-calibrate, new ride");
    CHECK(contiguous(sim.outs), "new ride starts at seq 0");
}

static void overflow_restarts() {
    Sim sim;
    sim.run_until(3000 * MS);
    sim.skip_to(4200 * MS);  // 1.2초 못 읽음: 1024바이트(0.85초) 넘침
    sim.run_until(4210 * MS);
    CHECK(sim.acq.stats().overflow == 1, "overflow detected");
    CHECK(sim.count(Out::RIDE_END) == 1, "overflow ends the ride (lost samples, unknown count)");
    sim.run_until(8000 * MS);
    CHECK(sim.count(Out::RIDE_START) == 2, "new ride after overflow");
}

static void misaligned_hold() {
    Sim sim;
    sim.run_until(3000 * MS);
    sim.mpu.set_time(3015 * MS);
    sim.mpu.inject_stray_byte();  // 12의 배수가 아님
    sim.run_until(4000 * MS);
    const Stats& st = sim.acq.stats();
    CHECK(st.misaligned == 1, "misaligned once");
    CHECK(st.hold >= 1 && st.hold <= 3, "discarded samples filled with hold (%llu)", (unsigned long long)st.hold);
    CHECK(st.restarts == 0, "no restart for a small loss");
    CHECK(contiguous(sim.outs), "seq continues through hold");
    size_t flagged = 0;
    for (const auto* o : sim.samples()) flagged += (o->flags & FLAG_HOLD) != 0;
    CHECK(flagged == st.hold, "hold samples flagged");
}

static void calibration_slides_until_still() {
    Sim sim;
    // 처음 1.5초는 흔들림, 그 뒤 정지
    sim.mpu.signal = [](uint64_t i) {
        Raw r{};
        r.a[2] = 8192;
        if (i < 150) r.a[2] = static_cast<int16_t>((i % 2) ? 8192 + 2000 : 8192 - 2000);  // 크기가 바뀌는 흔들림
        return r;
    };
    sim.run_until(3400 * MS);
    CHECK(sim.count(Out::RIDE_START) == 0, "not still yet");
    sim.run_until(4100 * MS);
    CHECK(sim.count(Out::RIDE_START) == 1, "ride once a 2 s still window exists");
    CHECK(!sim.acq.stats().bias_uncalibrated, "calibrated");
}

static void calibration_timeout() {
    auto moving = [](uint64_t i) {
        Raw r{};
        r.a[2] = static_cast<int16_t>((i % 2) ? 8192 + 3000 : 8192 - 3000);
        r.g[2] = 131;
        return r;
    };
    {
        Sim sim;
        sim.mpu.signal = moving;
        sim.run_until(29000 * MS);
        CHECK(sim.count(Out::RIDE_START) == 0, "still calibrating at 29 s");
        sim.run_until(31000 * MS);
        CHECK(sim.count(Out::RIDE_START) == 1, "ride after 30 s timeout");
        CHECK(sim.acq.stats().bias_uncalibrated, "no saved bias: uncalibrated flag");
        const auto s = sim.samples();
        CHECK(!s.empty() && std::fabs(s.back()->row[proto::C_GZ] - imu::kPi / 180.0) < 1e-9, "bias 0 used");
    }
    {
        Acquirer::Config cfg;
        cfg.has_saved_bias = true;
        cfg.saved_bias_dps = {0, 0, 1.0};
        Sim sim(cfg);
        sim.mpu.signal = moving;
        sim.run_until(31000 * MS);
        CHECK(sim.count(Out::RIDE_START) == 1 && !sim.acq.stats().bias_uncalibrated, "saved bias used");
        const auto s = sim.samples();
        CHECK(!s.empty() && std::fabs(s.back()->row[proto::C_GZ]) < 1e-9, "saved bias removes 1 dps");
        Vec3 b;
        CHECK(!sim.acq.take_new_bias(b), "saved bias is not reported as new");
    }
}

static void init_failures_fatal() {
    Sim sim;
    sim.mpu.who_am_i = 0x00;
    sim.run_until(8500 * MS);  // 시도: 0.01, 1.01, … 8.01초
    CHECK(!sim.acq.fatal() && sim.acq.stats().init_fails == 9, "9 failures by 8.5 s (retry every 1 s): %llu",
          (unsigned long long)sim.acq.stats().init_fails);
    sim.run_until(9500 * MS);
    CHECK(sim.acq.fatal(), "fatal after 10 failures");
    CHECK(sim.outs.empty(), "nothing emitted");
}

// ---- 자동 수평 맞춤 (Config::auto_level) ----
static Raw tilted(double pitch_deg, double roll_deg) {  // 센서 축 정지 중력 (차량 축 = 센서 축일 때 기울기)
    const double p = pitch_deg * imu::kPi / 180, r = roll_deg * imu::kPi / 180;
    Raw raw{};
    raw.a[0] = static_cast<int16_t>(std::lround(8192 * std::sin(p)));
    raw.a[1] = static_cast<int16_t>(std::lround(8192 * std::cos(p) * std::sin(r)));
    raw.a[2] = static_cast<int16_t>(std::lround(8192 * std::cos(p) * std::cos(r)));
    raw.g[0] = 131;  // 바이어스 1 °/s
    return raw;
}

static bool orthonormal(const Mat3& R) {
    for (int i = 0; i < 3; i++)
        for (int j = 0; j < 3; j++) {
            double s = 0;
            for (int k = 0; k < 3; k++) s += R[i][k] * R[j][k];
            if (std::fabs(s - (i == j)) > 1e-9) return false;
        }
    return true;
}

static void level_rotation_math() {
    Mat3 L;
    double tilt = -1;
    CHECK(level_rotation({0, 0, kG}, 10, L, tilt) && tilt < 1e-9, "flat: tilt 0");
    CHECK(orthonormal(L) && std::fabs(L[0][0] - 1) < 1e-12 && std::fabs(L[2][2] - 1) < 1e-12, "flat: identity");
    const Vec3 m{0.5, -0.4, 9.78};
    CHECK(level_rotation(m, 10, L, tilt), "3.7 deg accepted");
    Vec3 o{};
    for (int i = 0; i < 3; i++) o[i] = L[i][0] * m[0] + L[i][1] * m[1] + L[i][2] * m[2];
    const double n = std::sqrt(m[0] * m[0] + m[1] * m[1] + m[2] * m[2]);
    CHECK(std::fabs(o[0]) < 1e-9 && std::fabs(o[1]) < 1e-9 && std::fabs(o[2] - n) < 1e-9, "L·m = (0, 0, |m|)");
    CHECK(orthonormal(L), "L is a rotation");
    CHECK(std::fabs(L[1][0] - L[0][1]) < 1e-12, "no yaw: L symmetric in x-y (axis horizontal)");
    CHECK(!level_rotation({3.0, 0, 9.3}, 10, L, tilt) && tilt > 17 && tilt < 18, "18 deg rejected (%.2f)", tilt);
    CHECK(!level_rotation({0, 0, 0}, 10, L, tilt), "zero vector rejected");
}

static void auto_level_off_keeps_tilt() {
    Sim sim;
    sim.mpu.signal = [](uint64_t) { return tilted(3, -2); };
    sim.run_until(3000 * MS);
    const auto s = sim.samples();
    CHECK(!s.empty() && s.back()->row[proto::C_AX] > 0.5, "off: ax keeps gravity leak (%.3f)", s.back()->row[proto::C_AX]);
    CHECK(sim.acq.stats().level == -1, "off: level -1");
    const Out* st = nullptr;
    for (const auto& o : sim.outs)
        if (o.kind == Out::RIDE_START) st = &o;
    CHECK(st && std::isnan(st->row[ring::S_LEVEL]) && std::isnan(st->row[ring::S_MOUNT_R]), "off: start info unchanged");
}

static void auto_level_applied() {
    Acquirer::Config cfg;
    cfg.auto_level = true;
    Sim sim(cfg);
    sim.mpu.signal = [](uint64_t) { return tilted(3, -2); };
    sim.run_until(3000 * MS);
    const auto s = sim.samples();
    CHECK(!s.empty(), "ride started");
    const Row& r = s.back()->row;
    CHECK(std::fabs(r[proto::C_AX]) < 2e-3 && std::fabs(r[proto::C_AY]) < 2e-3, "on: still -> (0, 0, g): %.4f %.4f",
          r[proto::C_AX], r[proto::C_AY]);
    CHECK(std::fabs(r[proto::C_AZ] - kG) < 5e-3, "on: az = |a| (%.4f)", r[proto::C_AZ]);
    CHECK(std::fabs(r[proto::C_GX]) < 1e-12 && std::fabs(r[proto::C_GY]) < 1e-12 && std::fabs(r[proto::C_GZ]) < 1e-12,
          "on: gyro bias still removed");
    const Stats& st = sim.acq.stats();
    CHECK(st.level == 1 && std::fabs(st.level_pitch_deg - 3) < 0.05 && std::fabs(st.level_roll_deg + 2) < 0.05,
          "stats: pitch %.3f roll %.3f", st.level_pitch_deg, st.level_roll_deg);
    CHECK(orthonormal(sim.acq.mount_R()), "applied R is a rotation");
    const Out* so = nullptr;
    for (const auto& o : sim.outs)
        if (o.kind == Out::RIDE_START) so = &o;
    CHECK(so && so->row[ring::S_LEVEL] == 1, "start info: level 1");
    bool same = so != nullptr;
    for (int i = 0; same && i < 9; i++) same = so->row[ring::S_MOUNT_R + i] == sim.acq.mount_R()[i / 3][i % 3];
    CHECK(same, "start info: applied R");
}

static void auto_level_with_mount_axes() {
    // 센서가 90도 돌아간 장착 (센서 y = 차 앞, 센서 −x = 차 왼쪽) + 기울기: L · R_mount
    Acquirer::Config cfg;
    cfg.auto_level = true;
    cfg.mount.R = {{{0, 1, 0}, {-1, 0, 0}, {0, 0, 1}}};
    Sim sim(cfg);
    sim.mpu.signal = [](uint64_t) { return tilted(2, 1); };  // 센서 축 기울기
    sim.run_until(3000 * MS);
    const Stats& st = sim.acq.stats();
    // 센서 x(+2도) = 차 −y,  센서 y(+1도) = 차 +x
    CHECK(st.level == 1 && std::fabs(st.level_pitch_deg - 1) < 0.05 && std::fabs(st.level_roll_deg + 2) < 0.05,
          "car-axis pitch %.3f roll %.3f", st.level_pitch_deg, st.level_roll_deg);
    const Row& r = sim.samples().back()->row;
    CHECK(std::fabs(r[proto::C_AX]) < 2e-3 && std::fabs(r[proto::C_AY]) < 2e-3, "leveled (%.4f %.4f)", r[proto::C_AX],
          r[proto::C_AY]);
    const Mat3& M = sim.acq.mount_R();
    CHECK(M[0][1] > 0.99 && M[1][0] < -0.99, "axes relabel kept (%.3f %.3f)", M[0][1], M[1][0]);
}

static void auto_level_rejects_large_tilt() {
    Acquirer::Config cfg;
    cfg.auto_level = true;
    Sim sim(cfg);
    sim.mpu.signal = [](uint64_t) { return tilted(15, 0); };
    sim.run_until(3000 * MS);
    const Stats& st = sim.acq.stats();
    CHECK(st.level == 0 && std::fabs(st.level_tilt_deg - 15) < 0.05, "15 deg not applied (%.2f)", st.level_tilt_deg);
    CHECK(sim.acq.mount_R()[0][0] == 1 && sim.acq.mount_R()[0][2] == 0, "file R kept");
    CHECK(sim.samples().back()->row[proto::C_AX] > 2.0, "ax keeps gravity leak");
}

static void auto_level_only_first_calibration() {
    Acquirer::Config cfg;
    cfg.auto_level = true;
    {  // 처음에 정지를 못 얻으면 적용하지 않고, 주행 중 재시작 때 정지여도 다시 재지 않는다
        Sim sim(cfg);
        bool moving = true;
        sim.mpu.signal = [&moving](uint64_t i) {
            Raw r = tilted(3, 0);
            if (moving) r.a[2] = static_cast<int16_t>((i % 2) ? 8192 + 3000 : 8192 - 3000);
            return r;
        };
        sim.run_until(31000 * MS);
        CHECK(sim.count(Out::RIDE_START) == 1 && sim.acq.stats().level == 0, "timeout: not applied");
        moving = false;
        sim.mpu.fail_until_ns = 32500 * MS;  // 1.5초 I2C 오류 → 재시작 → 다시 보정
        sim.run_until(36000 * MS);
        CHECK(sim.count(Out::RIDE_START) == 2, "restarted ride");
        CHECK(sim.acq.stats().level == 0 && sim.acq.mount_R()[0][2] == 0, "no leveling on restart");
    }
    {  // 처음에 적용하면, 재시작 뒤에도 같은 R
        Sim sim(cfg);
        sim.mpu.signal = [](uint64_t) { return tilted(3, 0); };
        sim.run_until(3000 * MS);
        const Mat3 first = sim.acq.mount_R();
        sim.mpu.signal = [](uint64_t) { return tilted(0, 0); };  // 재시작 때는 평지 (다른 자리)
        sim.mpu.fail_until_ns = 4500 * MS;
        sim.run_until(8000 * MS);
        CHECK(sim.count(Out::RIDE_START) == 2 && sim.acq.mount_R() == first, "R kept across restart");
    }
}

// ---- 가속도 센서 오차 보정 (Config::accel_cal) ----
static void accel_cal_parse() {
    // sensor_6face.json 형식 그대로 (note 등 다른 키는 무시)
    const AccelCal c = parse_accel_cal(
        "{\"bias\": [0.0, 0.0263, -0.0003], \"scale\": [1.0, 0.99937, 0.98931], \"note\": \"x\", \"measured_axes\": [\"y\", \"z\"]}");
    CHECK(c.bias[1] == 0.0263 && c.scale[2] == 0.98931, "parse 6-face file");
    auto rejects = [](const char* text) {
        try {
            parse_accel_cal(text);
        } catch (const std::exception&) {
            return true;
        }
        return false;
    };
    CHECK(rejects("{\"bias\": [0, 2.0, 0], \"scale\": [1, 1, 1]}"), "bias 2 m/s^2 rejected");
    CHECK(rejects("{\"bias\": [0, 0, 0], \"scale\": [1, 1, 1.2]}"), "scale 1.2 rejected");
    CHECK(rejects("{\"bias\": [0, 0], \"scale\": [1, 1, 1]}"), "2 numbers rejected");
    CHECK(rejects("{\"scale\": [1, 1, 1]}"), "missing bias rejected");
    const Vec3 a = apply_accel_cal(c, {1.0, 1.0, 10.0});
    CHECK(std::fabs(a[1] - (1.0 - 0.0263) * 0.99937) < 1e-12 && std::fabs(a[2] - (10.0 + 0.0003) * 0.98931) < 1e-12,
          "(a - bias) * scale");
}

static void accel_cal_before_rotation() {
    // 센서 z가 차 왼쪽(y)을 보게 붙은 R: 오차는 센서 z축에서 빠져야 한다 (돌린 뒤 차 축에서 빼면 틀림)
    Mount m;
    m.R = {{{1, 0, 0}, {0, 0, 1}, {0, -1, 0}}};
    AccelCal c;
    c.bias = {0, 0, 0.2};
    c.scale = {1, 1, 1.01};
    Raw r{};
    r.a[2] = 8192;  // 센서 z = 1 g
    const Sample s = convert(r, m, {0, 0, 0}, c);
    CHECK(std::fabs(s.a[1] - (kG - 0.2) * 1.01) < 1e-12 && std::fabs(s.a[2]) < 1e-12, "cal in sensor axes, then R (%.5f)",
          s.a[1]);
    const Sample d = convert(r, m, {0, 0, 0});
    CHECK(std::fabs(d.a[1] - kG) < 1e-12, "default: no cal");
}

static void accel_cal_ride() {
    // 차는 수평, 센서 y에 0.3 m/s² 치우침. 보정이 없으면 자동 수평 맞춤이 이를 기울기(약 1.75도)로 잘못 본다
    auto sig = [](uint64_t) {
        Raw raw{};
        raw.a[1] = static_cast<int16_t>(std::lround(0.3 / kG * 8192));
        raw.a[2] = 8192;
        return raw;
    };
    Acquirer::Config cfg;
    cfg.auto_level = true;
    cfg.has_accel_cal = true;
    cfg.accel_cal.bias = {0, std::lround(0.3 / kG * 8192) / imu::kAccelLsbPerG * kG, 0};  // 정수로 반올림된 값 그대로
    Sim sim(cfg);
    sim.mpu.signal = sig;
    sim.run_until(3000 * MS);
    const auto s = sim.samples();
    CHECK(!s.empty() && std::fabs(s.back()->row[proto::C_AY]) < 2e-3, "cal on: ay = 0 (%.4f)", s.back()->row[proto::C_AY]);
    CHECK(sim.acq.stats().level_tilt_deg < 0.02, "cal on: level sees no tilt (%.3f deg)", sim.acq.stats().level_tilt_deg);
    const Out* so = nullptr;
    for (const auto& o : sim.outs)
        if (o.kind == Out::RIDE_START) so = &o;
    CHECK(so && so->row[ring::S_ACAL_BIAS + 1] == cfg.accel_cal.bias[1] && so->row[ring::S_ACAL_SCALE + 2] == 1,
          "start info: accel cal");

    Acquirer::Config off;
    off.auto_level = true;
    Sim sim2(off);
    sim2.mpu.signal = sig;
    sim2.run_until(3000 * MS);
    CHECK(sim2.acq.stats().level_tilt_deg > 1.6, "cal off: bias read as tilt (%.3f deg)", sim2.acq.stats().level_tilt_deg);
    const Out* so2 = nullptr;
    for (const auto& o : sim2.outs)
        if (o.kind == Out::RIDE_START) so2 = &o;
    CHECK(so2 && std::isnan(so2->row[ring::S_ACAL_BIAS]), "cal off: start info NaN");
}

int main() {
    decode_convert();
    mount_parse();
    init_registers();
    normal_ride();
    late_wakeup_no_loss();
    short_i2c_error();
    long_i2c_error_restarts();
    overflow_restarts();
    misaligned_hold();
    calibration_slides_until_still();
    calibration_timeout();
    init_failures_fatal();
    level_rotation_math();
    auto_level_off_keeps_tilt();
    auto_level_applied();
    auto_level_with_mount_axes();
    auto_level_rejects_large_tilt();
    auto_level_only_first_calibration();
    accel_cal_parse();
    accel_cal_before_rotation();
    accel_cal_ride();
    if (failures) {
        std::printf("%d failure(s)\n", failures);
        return 1;
    }
    std::printf("imu: all passed\n");
    return 0;
}
