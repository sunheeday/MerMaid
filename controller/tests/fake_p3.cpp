// fake_p3: P3 대역 (LightGBM 없이). Mac 시험(pipeline_rf_sim)과 Pi 시연(bringup/demo_phone.sh)용. 진짜 P3처럼 mermaid_p3 이름으로 VEH를 받고,
// 주기마다 P4에 PROFILES를 물은 뒤 정해진 확률로 RISK를 보낸다. 주행 경계는 그대로 넘긴다.
//   fake_p3 [--p P2,P3,P4,P5,P6] [--once]
// 진짜 P3처럼 P7에 LOG_INFER도 보낸다 (x = 차량 피처 71칸 + 승객 칸 NaN, 모델 "fake"). 실패 수는 stdout 마지막 줄 log_fail.
// stdout: 주기마다 "PROFILES <n> <id>:<mssq>:<sens>:<ev>:<cal_a>:<cal_b> ..." (P4가 RF 명부를 P3에 넘기는지 확인용)
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <sstream>
#include <string>

#include "ipc.hpp"
#include "log_messages.hpp"
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
        ipc::Client p7(msg::kNameP7, msg::kLogTimeoutNs);
        static msg::LogInfer lg;
        uint64_t log_fail = 0;
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
                    std::printf(" %s:%.2f:%.3f:%s:%.3f:%.3f", rep.e[i].id, rep.e[i].mssq, rep.e[i].sens,
                                std::isnan(rep.e[i].ev) ? "nan" : std::to_string(rep.e[i].ev).c_str(), rep.e[i].cal_a,
                                rep.e[i].cal_b);
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
                lg = msg::LogInfer{};
                lg.seq = out.seq;
                lg.t = out.t;
                msg::copy_text(lg.model, std::string("fake"));
                lg.n = out.n;
                for (uint32_t r = 0; r < out.n; r++) {
                    std::memcpy(lg.id[r], out.id[r], msg::kIdLen);
                    for (int f = 0; f < proto::kNumFeatures; f++)
                        lg.x[r][f] = f < proto::kNumVehicle ? v.x[static_cast<size_t>(f)] : std::nan("");
                    lg.p[r] = out.p[r];
                    lg.cal_a[r] = rep.n ? rep.e[r].cal_a : 1;  // 대역은 보정을 적용하지 않고 받은 값만 남긴다
                    lg.cal_b[r] = rep.n ? rep.e[r].cal_b : 0;
                }
                lg.mono_ns = ipc::now_ns();
                if (!p7.send(&lg, sizeof lg)) log_fail++;
            } else if (type == msg::RIDE_START || type == msg::RIDE_END) {
                const msg::RideEdge e = in.e;
                ipc::Server::reply(rcvid);
                if (type == msg::RIDE_START) seq = log_fail = 0;
                p4.send(&e, sizeof e);
                if (type == msg::RIDE_END) {
                    std::printf("{\"proc\": \"fake_p3\", \"cycles\": %llu, \"log_fail\": %llu}\n",
                                (unsigned long long)seq, (unsigned long long)log_fail);
                    std::fflush(stdout);
                    if (once) return 0;
                }
            } else {
                ipc::Server::error(rcvid, EINVAL);
            }
        }
    } catch (const std::exception& ex) {
        std::fprintf(stderr, "fake_p3: %s\n", ex.what());
        return 1;
    }
}
