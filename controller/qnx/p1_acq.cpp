// P1 수집 (재생 모드): trip.bin을 100 Hz로 P2에 보낸다. 실제 IMU(LIVE) 모드는 나중에.
//   p1_acq <trip.bin> [--speed S] [--timeout-ms T] [--prio P]
// --speed 1 = 실시간(10 ms 간격, CLOCK_MONOTONIC 절대 시각으로 잠듦), 10 = 10배속, 0 = 쉬지 않고.
// 시각은 파일의 t를 그대로 쓴다. 끝나면 지연과 전송 실패 통계를 JSON 한 줄로 낸다.
#include <time.h>

#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <string>
#include <vector>

#include "ipc.hpp"
#include "messages.hpp"

using namespace mm;

int main(int argc, char** argv) {
    if (argc < 2) {
        std::fprintf(stderr, "usage: p1_acq <trip.bin> [--speed S] [--timeout-ms T] [--prio P]\n");
        return 2;
    }
    double speed = 1, timeout_ms = 5;
    int prio = 40;
    for (int i = 2; i + 1 < argc; i += 2) {
        if (!std::strcmp(argv[i], "--speed")) speed = std::atof(argv[i + 1]);
        else if (!std::strcmp(argv[i], "--timeout-ms")) timeout_ms = std::atof(argv[i + 1]);
        else if (!std::strcmp(argv[i], "--prio")) prio = std::atoi(argv[i + 1]);
    }
    std::ifstream in(argv[1], std::ios::binary);
    double hdr[3];
    if (!in.read(reinterpret_cast<char*>(hdr), sizeof hdr) || int(hdr[0]) != proto::kTripVersion ||
        int(hdr[1]) != proto::kTripCols || hdr[2] != proto::kSampleRateHz) {
        std::fprintf(stderr, "p1: bad trip header in %s\n", argv[1]);
        return 2;
    }
    ipc::set_priority(prio, "p1");
    ipc::Client p2(msg::kNameP2, static_cast<uint64_t>(timeout_ms * 1e6));
    if (!p2.wait_open(30)) {
        std::fprintf(stderr, "p1: %s not found\n", msg::kNameP2);
        return 1;
    }

    msg::RideEdge edge;
    edge.h.type = msg::RIDE_START;
    const uint64_t edge_timeout = 1000000000ULL;  // 주행 경계는 시간에 쫓기지 않으니 길게
    if (!p2.send(&edge, sizeof edge, nullptr, 0, edge_timeout)) std::fprintf(stderr, "p1: RIDE_START not delivered\n");

    const uint64_t period = speed > 0 ? static_cast<uint64_t>(1e9 / proto::kSampleRateHz / speed) : 0;
    const uint64_t start = ipc::now_ns();
    msg::Sample s;
    uint64_t fails = 0, late_1ms = 0, late_max = 0;
    std::vector<uint32_t> late_us;
    while (in.read(reinterpret_cast<char*>(s.row.data()), sizeof(double) * s.row.size())) {
        if (period) {
            const uint64_t target = start + s.seq * period;
            timespec ts{static_cast<time_t>(target / 1000000000ULL), static_cast<long>(target % 1000000000ULL)};
            while (clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &ts, nullptr) == EINTR) {
            }
            const uint64_t now = ipc::now_ns();
            const uint64_t late = now > target ? now - target : 0;
            late_max = std::max(late_max, late);
            if (late > 1000000) late_1ms++;
            if (late_us.size() < 1000000) late_us.push_back(static_cast<uint32_t>(late / 1000));
        }
        if (!p2.send(&s, sizeof s)) fails++;
        s.seq++;
    }
    const double wall = (ipc::now_ns() - start) / 1e9;
    edge.h.type = msg::RIDE_END;
    edge.samples = s.seq;
    if (!p2.send(&edge, sizeof edge, nullptr, 0, edge_timeout)) std::fprintf(stderr, "p1: RIDE_END not delivered\n");

    double med = 0;
    if (!late_us.empty()) {
        std::nth_element(late_us.begin(), late_us.begin() + late_us.size() / 2, late_us.end());
        med = late_us[late_us.size() / 2];
    }
    std::printf("{\"proc\": \"p1\", \"samples\": %llu, \"send_fail\": %llu, \"speed\": %g, \"wall_s\": %.2f, "
                "\"wake_late_us_median\": %.0f, \"wake_late_us_max\": %.0f, \"wake_late_over_1ms\": %llu}\n",
                (unsigned long long)s.seq, (unsigned long long)fails, speed, wall, med, late_max / 1e3,
                (unsigned long long)late_1ms);
    return fails ? 1 : 0;
}
