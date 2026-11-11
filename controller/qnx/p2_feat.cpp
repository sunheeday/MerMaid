// P2 피처: P1의 100 Hz 샘플 → 10초마다 차량 피처 71개를 P3로 (Mac featurize와 같은 FeatureExtractor).
//   p2_feat [--once] [--prio P]
// 샘플은 받자마자 회신해 P1을 풀어 준다. 주행 경계(RIDE_START/END)는 같은 순서로 P3에 넘긴다.
#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <memory>

#include "ipc.hpp"
#include "messages.hpp"

using namespace mm;

union Inbox {
    msg::Hdr h;
    msg::Sample s;
    msg::RideEdge e;
    _pulse pulse;
    Inbox() {}
};

int main(int argc, char** argv) {
    bool once = false;
    int prio = 30;
    for (int i = 1; i < argc; i++) {
        if (!std::strcmp(argv[i], "--once")) once = true;
        else if (!std::strcmp(argv[i], "--prio") && i + 1 < argc) prio = std::atoi(argv[++i]);
    }
    try {
        ipc::set_priority(prio, "p2");
        ipc::Client p3(msg::kNameP3, 1000000000ULL);
        if (!p3.wait_open(30)) std::fprintf(stderr, "p2: %s not found yet, will retry\n", msg::kNameP3);
        ipc::Server srv(msg::kNameP2);  // P3에 연결된 뒤에 이름을 붙인다
        auto fx = std::make_unique<FeatureExtractor>();
        Inbox in;
        uint64_t expect = 0, received = 0, gaps = 0, veh = 0, veh_fail = 0;
        uint64_t push_max_ns = 0;
        for (;;) {
            const int rcvid = srv.receive(&in, sizeof in);
            const uint16_t type = in.h.type;
            if (type == msg::SAMPLE) {
                const msg::Sample s = in.s;
                ipc::Server::reply(rcvid);
                if (s.seq != expect) gaps += s.seq > expect ? s.seq - expect : 1;
                expect = s.seq + 1;
                received++;
                const uint64_t t0 = ipc::now_ns();
                const bool emit = fx->push(s.row);
                push_max_ns = std::max(push_max_ns, ipc::now_ns() - t0);
                if (emit) {
                    msg::Veh v;
                    v.seq = veh++;
                    v.t = fx->time();
                    v.x = fx->vehicle();
                    v.t_emit_ns = ipc::now_ns();
                    if (!p3.send(&v, sizeof v)) veh_fail++;
                }
            } else if (type == msg::RIDE_START || type == msg::RIDE_END) {
                const msg::RideEdge e = in.e;
                ipc::Server::reply(rcvid);
                if (type == msg::RIDE_START) {
                    fx = std::make_unique<FeatureExtractor>();
                    expect = received = gaps = veh = veh_fail = push_max_ns = 0;
                }
                if (!p3.send(&e, sizeof e)) std::fprintf(stderr, "p2: ride edge not delivered to p3\n");
                if (type == msg::RIDE_END) {
                    std::printf("{\"proc\": \"p2\", \"p1_sent\": %llu, \"received\": %llu, \"seq_gaps\": %llu, "
                                "\"veh_sent\": %llu, \"veh_fail\": %llu, \"push_us_max\": %.1f}\n",
                                (unsigned long long)e.samples, (unsigned long long)received, (unsigned long long)gaps,
                                (unsigned long long)veh, (unsigned long long)veh_fail, push_max_ns / 1e3);
                    std::fflush(stdout);
                    if (once) return (gaps || veh_fail || received != e.samples) ? 1 : 0;
                }
            } else {
                ipc::Server::error(rcvid, EINVAL);
            }
        }
    } catch (const std::exception& ex) {
        std::fprintf(stderr, "p2: %s\n", ex.what());
        return 1;
    }
}
