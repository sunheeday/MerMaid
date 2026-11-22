// P1 LIVE IMU 코어 테스트 (가짜 MPU, 시각은 직접 진행): 변환, 장착 행렬, 초기화 레지스터, 보정, 주행 중 복구 규칙.
// 설계: docs/controller/DATA_LOGGING.md §4
#include <cmath>
#include <cstdio>
#include <string>
#include <vector>

#include "fake_mpu.hpp"
#include "imu.hpp"

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
    if (failures) {
        std::printf("%d failure(s)\n", failures);
        return 1;
    }
    std::printf("imu: all passed\n");
    return 0;
}
