// P2 피처: P1의 100 Hz 샘플 → 10초마다 차량 피처 46개를 P3로 (Mac featurize와 같은 FeatureExtractor).
//   p2_feat [--once] [--motion] [--prio P]
// 샘플은 받자마자 회신해 P1을 풀어 준다. 주행 경계(RIDE_START/END)는 같은 순서로 P3에 넘긴다.
// --motion: 10샘플(100 ms, constants.json mot_period_ms)마다 P6(mermaid_p6)에 MOTION을 보낸다.
//   ax, ay = Wf 가중 가속도, pax, pay = trip 열 pred_ax_10s, pred_ay_10s (docs/controller/RF_MESSAGES.md §5).
//   P6이 없거나 늦으면 20 ms 뒤 포기하고 센다 (피처 계산은 계속).
#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <memory>

#include "ipc.hpp"
#include "messages.hpp"
#include "rf_messages.hpp"

using namespace mm;

union Inbox {
    msg::Hdr h;
    msg::Sample s;
    msg::RideEdge e;
    _pulse pulse;
    Inbox() {}
};

int main(int argc, char** argv) {
    bool once = false, motion = false;
    int prio = 30;
    for (int i = 1; i < argc; i++) {
        if (!std::strcmp(argv[i], "--once")) once = true;
        else if (!std::strcmp(argv[i], "--motion")) motion = true;
        else if (!std::strcmp(argv[i], "--prio") && i + 1 < argc) prio = std::atoi(argv[++i]);
    }
    try {
        ipc::set_priority(prio, "p2");
        ipc::Client p3(msg::kNameP3, 1000000000ULL);
        if (!p3.wait_open(30)) std::fprintf(stderr, "p2: %s not found yet, will retry\n", msg::kNameP3);
        ipc::Client p6(msg::kNameP6, 20 * 1000000ULL);
        const int mot_every = proto::kMotPeriodMs / 10;  // 100 Hz 샘플 수
        uint64_t mot_sent = 0, mot_fail = 0;
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
                if (motion && received % mot_every == 0) {
                    msg::Motion m;
                    m.t = s.row[proto::C_T];
                    m.ax = fx->weighted_ax();
                    m.ay = fx->weighted_ay();
                    m.pax = s.row[proto::C_PRED_AX_10S];
                    m.pay = s.row[proto::C_PRED_AY_10S];
                    if (p6.send(&m, sizeof m)) mot_sent++;
                    else mot_fail++;
                }
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
                    expect = received = gaps = veh = veh_fail = push_max_ns = mot_sent = mot_fail = 0;
                }
                if (!p3.send(&e, sizeof e)) std::fprintf(stderr, "p2: ride edge not delivered to p3\n");
                if (type == msg::RIDE_END) {
                    std::printf("{\"proc\": \"p2\", \"p1_sent\": %llu, \"received\": %llu, \"seq_gaps\": %llu, "
                                "\"veh_sent\": %llu, \"veh_fail\": %llu, \"push_us_max\": %.1f",
                                (unsigned long long)e.samples, (unsigned long long)received, (unsigned long long)gaps,
                                (unsigned long long)veh, (unsigned long long)veh_fail, push_max_ns / 1e3);
                    if (motion)
                        std::printf(", \"mot_sent\": %llu, \"mot_fail\": %llu", (unsigned long long)mot_sent,
                                    (unsigned long long)mot_fail);
                    std::printf("}\n");
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
