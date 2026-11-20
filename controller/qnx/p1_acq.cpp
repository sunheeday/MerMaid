// P1 수집: 100 Hz 샘플을 P2에 보내고, 같은 샘플을 공유 메모리 링(P7 로거용)에 쓴다.
//
// 재생:  p1_acq <trip.bin> [--speed S] [--timeout-ms T] [--prio P] [--ring NAME|none]
//   --speed 1 = 실시간(10 ms 간격, CLOCK_MONOTONIC 절대 시각으로 잠듦), 10 = 10배속, 0 = 쉬지 않고.
//   시각은 파일의 t를 그대로 쓴다.
//
// LIVE:  p1_acq --live [--i2c /dev/i2c1] [--i2c-speed 400000] [--mount controller/config/imu_mount.json]
//                      [--state DIR] [--timeout-ms T] [--prio P] [--ring NAME|none] [--duration-s S] [--fake-imu]
//   MPU-6050을 FIFO로 읽는다 (common/imu.hpp, 설계 docs/DATA_LOGGING.md §4). 보정(정지 2초)이 끝나면 RIDE_START,
//   SIGINT/SIGTERM이나 --duration-s가 지나면 RIDE_END를 보내고 끝낸다. 센서 오류로 주행이 끊기면 다시 보정 후 새 주행.
//   --state DIR    자이로 바이어스를 DIR/imu_bias.json에 저장하고, 정지 판정을 못 얻으면 그 값을 쓴다
//   --fake-imu     실제 IMU 대신 가짜 MPU (Mac 시험, IMU 없는 보드 시험). QNX가 아니면 --live에 필수
//
// 링(기본 /mermaid_samples)을 못 만들어도 경고만 하고 계속한다. 끝나면 통계를 JSON 한 줄로 낸다.
#include <signal.h>
#include <time.h>

#include <algorithm>
#include <cmath>
#include <csignal>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <memory>
#include <sstream>
#include <string>
#include <vector>

#include "fake_mpu.hpp"
#include "imu.hpp"
#include "ipc.hpp"
#include "json_min.hpp"
#include "messages.hpp"
#include "mpu_i2c.hpp"
#include "sample_ring.hpp"
#include "store.hpp"

using namespace mm;

namespace {

constexpr uint64_t kEdgeTimeout = 1000000000ULL;  // 주행 경계는 시간에 쫓기지 않으니 길게
constexpr uint32_t kLiveColMask = 0x7F;           // t, ax, ay, az, gx, gy, gz
constexpr uint32_t kAllColMask = 0xFFFFFFFFu;     // 재생: 파일의 칸 전부

volatile std::sig_atomic_t g_stop = 0;
void on_signal(int) { g_stop = 1; }

void sleep_ms(int ms) {
    timespec ts{ms / 1000, (ms % 1000) * 1000000L};
    while (nanosleep(&ts, &ts) == -1 && errno == EINTR) {
    }
}

void sleep_until(uint64_t target) {
    timespec ts{static_cast<time_t>(target / 1000000000ULL), static_cast<long>(target % 1000000000ULL)};
    while (clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &ts, nullptr) == EINTR) {
        if (g_stop) return;
    }
}

void open_ring(ring::Writer& w, const std::string& name, uint32_t mask) {
    if (name == "none") return;
    std::string err;
    if (!w.open(name, ring::kDefaultCap, mask, err))
        std::fprintf(stderr, "p1: ring %s not available (%s), logging off\n", name.c_str(), err.c_str());
}

bool send_edge(ipc::Client& p2, uint16_t type, uint64_t samples) {
    msg::RideEdge e;
    e.h.type = type;
    e.samples = samples;
    return p2.send(&e, sizeof e, nullptr, 0, kEdgeTimeout);
}

// ---------------- 재생 ----------------

int run_replay(int argc, char** argv) {
    double speed = 1, timeout_ms = 5;
    int prio = 40;
    std::string ring_name = ring::kDefaultName;
    for (int i = 2; i + 1 < argc; i += 2) {
        if (!std::strcmp(argv[i], "--speed")) speed = std::atof(argv[i + 1]);
        else if (!std::strcmp(argv[i], "--timeout-ms")) timeout_ms = std::atof(argv[i + 1]);
        else if (!std::strcmp(argv[i], "--prio")) prio = std::atoi(argv[i + 1]);
        else if (!std::strcmp(argv[i], "--ring")) ring_name = argv[i + 1];
    }
    std::ifstream in(argv[1], std::ios::binary);
    double hdr[3];
    if (!in.read(reinterpret_cast<char*>(hdr), sizeof hdr) || int(hdr[0]) != proto::kTripVersion ||
        int(hdr[1]) != proto::kTripCols || hdr[2] != proto::kSampleRateHz) {
        std::fprintf(stderr, "p1: bad trip header in %s\n", argv[1]);
        return 2;
    }
    ipc::set_priority(prio, "p1");
    ring::Writer rw;
    open_ring(rw, ring_name, kAllColMask);
    ipc::Client p2(msg::kNameP2, static_cast<uint64_t>(timeout_ms * 1e6));
    if (!p2.wait_open(30)) {
        std::fprintf(stderr, "p1: %s not found\n", msg::kNameP2);
        return 1;
    }

    const uint64_t start = ipc::now_ns();
    rw.push(ring::RIDE_START, 0, 0, start, Row{});
    if (!send_edge(p2, msg::RIDE_START, 0)) std::fprintf(stderr, "p1: RIDE_START not delivered\n");

    const uint64_t period = speed > 0 ? static_cast<uint64_t>(1e9 / proto::kSampleRateHz / speed) : 0;
    msg::Sample s;
    uint64_t fails = 0, late_1ms = 0, late_max = 0;
    std::vector<uint32_t> late_us;
    while (in.read(reinterpret_cast<char*>(s.row.data()), sizeof(double) * s.row.size())) {
        uint64_t now = 0;
        if (period) {
            const uint64_t target = start + s.seq * period;
            sleep_until(target);
            now = ipc::now_ns();
            const uint64_t late = now > target ? now - target : 0;
            late_max = std::max(late_max, late);
            if (late > 1000000) late_1ms++;
            if (late_us.size() < 1000000) late_us.push_back(static_cast<uint32_t>(late / 1000));
        } else {
            now = ipc::now_ns();
        }
        rw.push(ring::SAMPLE, 0, s.seq, now, s.row);
        if (!p2.send(&s, sizeof s)) fails++;
        s.seq++;
    }
    const uint64_t end = ipc::now_ns();
    const double wall = (end - start) / 1e9;
    rw.push(ring::RIDE_END, 0, s.seq, end, Row{});
    if (!send_edge(p2, msg::RIDE_END, s.seq)) std::fprintf(stderr, "p1: RIDE_END not delivered\n");

    double med = 0;
    if (!late_us.empty()) {
        std::nth_element(late_us.begin(), late_us.begin() + late_us.size() / 2, late_us.end());
        med = late_us[late_us.size() / 2];
    }
    std::printf("{\"proc\": \"p1\", \"mode\": \"replay\", \"samples\": %llu, \"send_fail\": %llu, \"speed\": %g, "
                "\"wall_s\": %.2f, \"wake_late_us_median\": %.0f, \"wake_late_us_max\": %.0f, "
                "\"wake_late_over_1ms\": %llu, \"ring\": %s}\n",
                (unsigned long long)s.seq, (unsigned long long)fails, speed, wall, med, late_max / 1e3,
                (unsigned long long)late_1ms, rw.is_open() ? "true" : "false");
    return fails ? 1 : 0;
}

// ---------------- LIVE ----------------

bool load_bias(const std::string& path, imu::Vec3& b) {
    std::string text;
    if (!store::read_file(path, text)) return false;
    try {
        const json::Value v = json::parse(text);
        const json::Value& a = v.at("gyro_bias_dps");
        if (a.type != json::Value::Array || a.arr.size() != 3) return false;
        for (int i = 0; i < 3; i++) b[i] = a.arr[i].number();
        return true;
    } catch (const std::exception& e) {
        std::fprintf(stderr, "p1: ignoring %s (%s)\n", path.c_str(), e.what());
        return false;
    }
}

void save_bias(const std::string& path, const imu::Vec3& b) {
    char buf[160];
    std::snprintf(buf, sizeof buf, "{\"version\": 1, \"gyro_bias_dps\": [%.6f, %.6f, %.6f]}\n", b[0], b[1], b[2]);
    try {
        store::write_atomic(path, buf);
    } catch (const std::exception& e) {
        std::fprintf(stderr, "p1: cannot save %s (%s)\n", path.c_str(), e.what());
    }
}

int run_live(int argc, char** argv) {
    std::string i2c = "/dev/i2c1", mount_path = "controller/config/imu_mount.json", state_dir, ring_name = ring::kDefaultName;
    uint32_t i2c_speed = 400000;
    double timeout_ms = 5, duration_s = 0;
    int prio = 40;
    bool fake = false;
    for (int i = 2; i < argc; i++) {
        const std::string a = argv[i];
        const bool more = i + 1 < argc;
        if (a == "--fake-imu") fake = true;
        else if (a == "--i2c" && more) i2c = argv[++i];
        else if (a == "--i2c-speed" && more) i2c_speed = static_cast<uint32_t>(std::atol(argv[++i]));
        else if (a == "--mount" && more) mount_path = argv[++i];
        else if (a == "--state" && more) state_dir = argv[++i];
        else if (a == "--timeout-ms" && more) timeout_ms = std::atof(argv[++i]);
        else if (a == "--prio" && more) prio = std::atoi(argv[++i]);
        else if (a == "--ring" && more) ring_name = argv[++i];
        else if (a == "--duration-s" && more) duration_s = std::atof(argv[++i]);
        else {
            std::fprintf(stderr, "p1: unknown option %s\n", a.c_str());
            return 2;
        }
    }

    imu::Acquirer::Config cfg;
    try {
        cfg.mount = imu::load_mount(mount_path);
    } catch (const std::exception& e) {
        std::fprintf(stderr, "p1: %s\n", e.what());
        return 2;
    }
    const std::string bias_path = state_dir.empty() ? "" : state_dir + "/imu_bias.json";
    if (!bias_path.empty()) {
        store::make_dirs(state_dir);
        cfg.has_saved_bias = load_bias(bias_path, cfg.saved_bias_dps);
    }

    std::unique_ptr<imu::Bus> bus;
    imu::FakeMpu* fake_bus = nullptr;
    uint32_t actual_speed = 0;
    if (fake) {
        auto f = std::make_unique<imu::FakeMpu>();
        fake_bus = f.get();
        bus = std::move(f);
    } else {
#ifdef __QNX__
        auto b = std::make_unique<imu::I2cBus>();
        std::string err;
        if (!b->open(i2c, i2c_speed, actual_speed, err)) {
            std::fprintf(stderr, "p1: %s\n", err.c_str());
            return 2;
        }
        if (actual_speed != i2c_speed)
            std::fprintf(stderr, "p1: I2C %u Hz not accepted, using %u Hz\n", i2c_speed, actual_speed);
        bus = std::move(b);
#else
        (void)i2c_speed;
        std::fprintf(stderr, "p1: --live needs QNX I2C; use --fake-imu here\n");
        return 2;
#endif
    }

    std::signal(SIGINT, on_signal);
    std::signal(SIGTERM, on_signal);
    ipc::set_priority(prio, "p1");
    ring::Writer rw;
    open_ring(rw, ring_name, kLiveColMask);
    ipc::Client p2(msg::kNameP2, static_cast<uint64_t>(timeout_ms * 1e6));
    if (!p2.wait_open(30)) {
        std::fprintf(stderr, "p1: %s not found\n", msg::kNameP2);
        return 1;
    }

    imu::Acquirer acq(*bus, cfg, sleep_ms);
    uint64_t fails = 0, edge_fails = 0, late_1ms = 0, late_max = 0, ticks = 0;
    const uint64_t period = static_cast<uint64_t>(1e9 / imu::kRateHz);
    uint64_t ride_t0 = 0;
    auto deliver = [&](const std::vector<imu::Out>& outs, uint64_t now) {
        for (const imu::Out& o : outs) {
            rw.push(o.kind, o.flags, o.seq, now, o.row);
            if (o.kind == imu::Out::SAMPLE) {
                msg::Sample s;
                s.seq = o.seq;
                s.row = o.row;
                if (!p2.send(&s, sizeof s)) fails++;
            } else {
                const bool start = o.kind == imu::Out::RIDE_START;
                if (start) ride_t0 = now;
                if (!send_edge(p2, start ? msg::RIDE_START : msg::RIDE_END, o.seq)) edge_fails++;
                std::fprintf(stderr, "p1: %s%s\n", start ? "RIDE_START" : "RIDE_END",
                             start ? "" : (" after " + std::to_string(o.seq) + " samples").c_str());
            }
        }
    };

    uint64_t next = ipc::now_ns();
    std::string last_err;
    for (;;) {
        next += period;
        sleep_until(next);
        uint64_t now = ipc::now_ns();
        if (now > next + 100000000ULL) next = now;  // 100 ms 넘게 밀리면 (초기화 대기 등) 박자를 다시 맞춘다
        const uint64_t late = now > next ? now - next : 0;
        late_max = std::max(late_max, late);
        if (late > 1000000) late_1ms++;
        ticks++;
        if (fake_bus) fake_bus->set_time(now);

        const bool timeup = duration_s > 0 && acq.riding() && now - ride_t0 >= duration_s * 1e9;
        if (g_stop || timeup) {
            deliver(acq.stop(), now);
            break;
        }
        deliver(acq.step(now), now);
        if (acq.last_error() != last_err) {
            last_err = acq.last_error();
            if (!last_err.empty()) std::fprintf(stderr, "p1: imu: %s\n", last_err.c_str());
        }
        imu::Vec3 b;
        if (acq.take_new_bias(b)) {
            std::fprintf(stderr, "p1: gyro bias %.3f %.3f %.3f dps\n", b[0], b[1], b[2]);
            if (!bias_path.empty()) save_bias(bias_path, b);
        }
        if (acq.fatal()) {
            std::fprintf(stderr, "p1: IMU init failed %d times, giving up\n", imu::kMaxInitFails);
            break;
        }
    }

    const imu::Stats& st = acq.stats();
    std::printf("{\"proc\": \"p1\", \"mode\": \"%s\", \"rides\": %llu, \"samples\": %llu, \"hold\": %llu, "
                "\"send_fail\": %llu, \"edge_fail\": %llu, \"overflow\": %llu, \"misaligned\": %llu, "
                "\"i2c_error_ticks\": %llu, \"restarts\": %llu, \"init_fails\": %llu, \"bias_uncalibrated\": %s, "
                "\"rate_hz\": %.3f, \"i2c_hz\": %u, \"ticks\": %llu, \"wake_late_us_max\": %.0f, "
                "\"wake_late_over_1ms\": %llu, \"ring\": %s}\n",
                fake ? "live-fake" : "live", (unsigned long long)st.rides, (unsigned long long)st.samples,
                (unsigned long long)st.hold, (unsigned long long)fails, (unsigned long long)edge_fails,
                (unsigned long long)st.overflow, (unsigned long long)st.misaligned,
                (unsigned long long)st.i2c_error_ticks, (unsigned long long)st.restarts,
                (unsigned long long)st.init_fails, st.bias_uncalibrated ? "true" : "false", st.rate_hz, actual_speed,
                (unsigned long long)ticks, late_max / 1e3, (unsigned long long)late_1ms,
                rw.is_open() ? "true" : "false");
    if (acq.fatal()) return 3;
    return fails || edge_fails ? 1 : 0;
}

}  // namespace

int main(int argc, char** argv) {
    if (argc < 2) {
        std::fprintf(stderr,
                     "usage: p1_acq <trip.bin> [--speed S] [--timeout-ms T] [--prio P] [--ring NAME|none]\n"
                     "       p1_acq --live [--i2c DEV] [--i2c-speed HZ] [--mount FILE] [--state DIR] [--timeout-ms T]\n"
                     "                     [--prio P] [--ring NAME|none] [--duration-s S] [--fake-imu]\n");
        return 2;
    }
    if (!std::strcmp(argv[1], "--live")) return run_live(argc, argv);
    return run_replay(argc, argv);
}
