// rf_stub: P5·P6 시험용 P4(정책)·P2(MOT) 대역. 진짜 P4·P2가 RF 메시지를 쓰게 되면 필요 없다.
//   rf_stub [--cycle] [--motion] [--period S] [--stop-sync-after S]
//   (기본)              S초(기본 10)마다 DEV_SYNC를 보내고 명부를 출력. 기기의 OVR를 받으면 그대로 TIER_CMD로 돌려준다
//   --cycle             S초마다 차례로: Tier 1 ON → Tier 2 ON → 등록된 폰 모두 Tier 3 ON → 모두 OFF
//   --motion            10 Hz로 가짜 가속도(사인파)를 P6에 MOTION으로 (P2 대역)
//   --stop-sync-after S S초 뒤 DEV_SYNC와 명령을 멈춤 (P4가 죽은 상황: 30초 뒤 P5가 전부 OFF 하는지)
//
// mermaid_p4 이름을 쓰므로 진짜 P4와 동시에 띄우지 않는다.
// 보내는 방향은 진짜 P4·P2와 같다: P4 → P5, P2 → P6. 받는 것은 P6의 CTX_* (바로 회신).
#include <cerrno>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include "ipc.hpp"
#include "protocol_gen.hpp"
#include "rf_messages.hpp"

using namespace mm;

namespace {

int64_t now_ms() { return static_cast<int64_t>(ipc::now_ns() / 1000000ULL); }

std::mutex g_log_mu;
void log_line(const char* tag, const std::string& s) {
    std::lock_guard<std::mutex> lk(g_log_mu);
    std::printf("%10.3f %s %s\n", static_cast<double>(now_ms()) / 1000.0, tag, s.c_str());
    std::fflush(stdout);
}

void sleep_ms(int64_t ms) {
    timespec ts{static_cast<time_t>(ms / 1000), static_cast<long>((ms % 1000) * 1000000)};
    nanosleep(&ts, nullptr);
}

int tier_of(const std::string& device) {
    if (device == proto::kDeviceAirflow) return 1;
    if (device == proto::kDeviceDisplay) return 2;
    return 3;
}

bool send_cmd(ipc::Client& p5, const std::string& device, bool on) {
    msg::TierCmd m;
    msg::copy_id(m.device, device);
    m.tier = static_cast<uint8_t>(tier_of(device));
    m.on = on;
    const bool ok = p5.send(&m, sizeof m);
    log_line(ok ? "P5" : "ERR", std::string(ok ? "" : "cannot send ") + "TIER_CMD " + device + " tier " +
                                    std::to_string(m.tier) + (on ? " ON" : " OFF"));
    return ok;
}

union Inbox {
    msg::Hdr h;
    msg::CtxScreen scr;
    msg::CtxOvr ovr;
    msg::CtxProf prof;
    _pulse pulse;
    Inbox() {}
};

}  // namespace

int main(int argc, char** argv) {
    bool cycle = false, motion = false;
    int period_s = 10, stop_after_s = -1;
    for (int i = 1; i < argc; i++) {
        const std::string a = argv[i];
        const bool more = i + 1 < argc;
        if (a == "--cycle") cycle = true;
        else if (a == "--motion") motion = true;
        else if (a == "--period" && more) period_s = std::atoi(argv[++i]);
        else if (a == "--stop-sync-after" && more) stop_after_s = std::atoi(argv[++i]);
        else {
            std::fprintf(stderr, "usage: rf_stub [--cycle] [--motion] [--period S] [--stop-sync-after S]\n");
            return 2;
        }
    }
    if (period_s < 1) period_s = 1;

    try {
        ipc::Server srv(msg::kNameP4);
        log_line("..", std::string("rf_stub up as ") + msg::kNameP4 + (cycle ? ", cycle" : "") +
                           (motion ? ", motion" : "") +
                           (stop_after_s >= 0 ? ", stop sync after " + std::to_string(stop_after_s) + " s" : ""));
        const int64_t t0 = now_ms();

        // ---- P4 대역: DEV_SYNC + (선택) 차례로 켜고 끄기 ----
        std::thread policy([&] {
            ipc::Client p5(msg::kNameP5, 200 * 1000000ULL);
            uint64_t seq = 0;
            int step = 0;
            bool stopped = false;
            for (;;) {
                if (stop_after_s >= 0 && now_ms() - t0 >= stop_after_s * 1000LL) {
                    if (!stopped) log_line("..", "stopping DEV_SYNC (P5 should go safe off in 30 s)");
                    stopped = true;
                    sleep_ms(1000);
                    continue;
                }
                msg::DevSync s;
                s.seq = seq++;
                msg::DevSyncReply rep;
                std::vector<std::string> phones;
                if (p5.send(&s, sizeof s, &rep, sizeof rep)) {
                    std::string line = "registry: " + std::to_string(rep.n) + " device(s)";
                    for (uint32_t i = 0; i < rep.n && i < static_cast<uint32_t>(msg::kMaxConn); i++) {
                        line += std::string(" ") + rep.e[i].dev + (rep.e[i].role == msg::Role::Hud ? "(hud)" : "");
                        if (rep.e[i].role == msg::Role::App) phones.emplace_back(rep.e[i].dev);
                    }
                    log_line("P5", line);
                } else {
                    log_line("ERR", "cannot reach P5 (DEV_SYNC)");
                }
                if (cycle) {
                    switch (step++ % 4) {
                        case 0: send_cmd(p5, proto::kDeviceAirflow, true); break;
                        case 1: send_cmd(p5, proto::kDeviceDisplay, true); break;
                        case 2:
                            for (const auto& p : phones) send_cmd(p5, p, true);
                            break;
                        default:
                            send_cmd(p5, proto::kDeviceAirflow, false);
                            send_cmd(p5, proto::kDeviceDisplay, false);
                            for (const auto& p : phones) send_cmd(p5, p, false);
                    }
                }
                sleep_ms(period_s * 1000LL);
            }
        });
        policy.detach();

        // ---- P2 대역: 10 Hz MOTION ----
        if (motion) {
            std::thread mot([&] {
                ipc::Client p6(msg::kNameP6, 50 * 1000000ULL);
                int64_t next = now_ms();
                bool warned = false;
                for (;;) {
                    const double t = static_cast<double>(now_ms() - t0) / 1000.0;
                    msg::Motion m;
                    m.t = t;
                    m.ax = 1.5 * std::sin(2 * mm::kPi * t / 6.0);   // 6초 주기 가감속
                    m.ay = 1.0 * std::sin(2 * mm::kPi * t / 10.0);  // 10초 주기 좌우
                    m.pax = 1.5 * std::sin(2 * mm::kPi * (t + 1.0) / 6.0);  // 1초 앞 (예측 흉내)
                    m.pay = 1.0 * std::sin(2 * mm::kPi * (t + 1.0) / 10.0);
                    const bool ok = p6.send(&m, sizeof m);
                    if (!ok && !warned) log_line("ERR", "cannot send MOTION to P6");
                    warned = !ok;
                    next += 100;
                    const int64_t wait = next - now_ms();
                    if (wait > 0) sleep_ms(wait);
                    else next = now_ms();  // 밀리면 몰아 보내지 않음
                }
            });
            mot.detach();
        }

        // ---- 받기: P6의 CTX_* (P4 대역). OVR는 정책 없이 그대로 명령으로 ----
        ipc::Client p5_rx(msg::kNameP5, 200 * 1000000ULL);
        Inbox in;
        for (;;) {
            const int rcvid = srv.receive(&in, sizeof in);
            const uint16_t type = in.h.type;
            if (type == msg::CTX_SCREEN) {
                const msg::CtxScreen m = in.scr;
                ipc::Server::reply(rcvid);
                log_line("P6", std::string("SCR ") + m.sender + " on=" + std::to_string(m.on) +
                                   " att=" + std::to_string(m.att));
            } else if (type == msg::CTX_OVR) {
                const msg::CtxOvr m = in.ovr;
                ipc::Server::reply(rcvid);
                log_line("P6", std::string("OVR ") + m.sender + " -> " + m.device + (m.on ? " ON" : " OFF"));
                send_cmd(p5_rx, m.device, m.on != 0);
            } else if (type == msg::CTX_PROF) {
                const msg::CtxProf m = in.prof;
                ipc::Server::reply(rcvid);
                char b[160];
                std::snprintf(b, sizeof b, "PROF %s mssq=%.2f sens=%.3f (진짜 P4라면 저장)", m.phone, m.mssq, m.sens);
                log_line("P6", b);
            } else {
                ipc::Server::error(rcvid, EINVAL);  // PROFILES, RISK 등은 대역이 받지 않는다
            }
        }
    } catch (const std::exception& e) {
        std::fprintf(stderr, "rf_stub: %s\n", e.what());
        return 1;
    }
}
