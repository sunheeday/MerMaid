// fake_p3: P3 대역 (LightGBM 없이). Mac 시험(pipeline_rf_sim)과 Pi 시연(bringup/demo_phone.sh)용. 진짜 P3처럼 mermaid_p3 이름으로 VEH를 받고,
// 주기마다 P4에 PROFILES를 물은 뒤 정해진 확률로 RISK를 보낸다. 주행 경계는 그대로 넘긴다.
//   fake_p3 [--p P2,P3,P4,P5,P6] [--once]
// stdout: 주기마다 "PROFILES <n> <id>:<mssq>:<sens>:<ev> ..." (P4가 RF 명부를 P3에 넘기는지 확인용)
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <sstream>
#include <string>

#include "ipc.hpp"
#include "messages.hpp"

using namespace mm;

union Inbox {
    msg::Hdr h;
    msg::Veh v;
    msg::RideEdge e;
    _pulse pulse;
    Inbox() {}
};

int main(int argc, char** argv) {
    Risk p{0.9, 0.9, 0.9, 0.9, 0.9};
    bool once = false;
    for (int i = 1; i < argc; i++) {
        if (!std::strcmp(argv[i], "--p") && i + 1 < argc) {
            std::stringstream ss(argv[++i]);
            std::string f;
            for (size_t k = 0; k < p.size() && std::getline(ss, f, ','); k++) p[k] = std::atof(f.c_str());
        } else if (!std::strcmp(argv[i], "--once")) {
            once = true;
        }
    }
    try {
        ipc::Client p4(msg::kNameP4, 1000000000ULL);
        if (!p4.wait_open(30)) std::fprintf(stderr, "fake_p3: %s not found yet\n", msg::kNameP4);
        ipc::Server srv(msg::kNameP3);
        std::printf("fake_p3 up\n");
        std::fflush(stdout);
        Inbox in;
        uint64_t seq = 0;
        for (;;) {
            const int rcvid = srv.receive(&in, sizeof in);
            const uint16_t type = in.h.type;
            if (type == msg::VEH) {
                const msg::Veh v = in.v;
                ipc::Server::reply(rcvid);
                msg::ProfilesReq q;
                msg::ProfilesReply rep;
                if (!p4.send(&q, sizeof q, &rep, sizeof rep)) rep.n = 0;
                std::printf("PROFILES %u", rep.n);
                for (uint32_t i = 0; i < rep.n && i < static_cast<uint32_t>(msg::kMaxPhones); i++)
                    std::printf(" %s:%.2f:%.3f:%s", rep.e[i].id, rep.e[i].mssq, rep.e[i].sens,
                                std::isnan(rep.e[i].ev) ? "nan" : std::to_string(rep.e[i].ev).c_str());
                std::printf("\n");
                std::fflush(stdout);
                msg::RiskMsg out;
                out.seq = seq++;
                out.t = v.t;
                out.t_emit_ns = v.t_emit_ns;
                out.n = rep.n ? rep.n : 1;
                for (uint32_t r = 0; r < out.n; r++) {
                    if (rep.n) std::memcpy(out.id[r], rep.e[r].id, msg::kIdLen);
                    else msg::copy_id(out.id[r], "default");
                    out.p[r] = p;
                }
                p4.send(&out, sizeof out);
            } else if (type == msg::RIDE_START || type == msg::RIDE_END) {
                const msg::RideEdge e = in.e;
                ipc::Server::reply(rcvid);
                if (type == msg::RIDE_START) seq = 0;
                p4.send(&e, sizeof e);
                if (type == msg::RIDE_END && once) return 0;
            } else {
                ipc::Server::error(rcvid, EINVAL);
            }
        }
    } catch (const std::exception& ex) {
        std::fprintf(stderr, "fake_p3: %s\n", ex.what());
        return 1;
    }
}
