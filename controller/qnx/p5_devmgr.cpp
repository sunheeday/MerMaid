// P5 기기 관리: 등록 명부를 들고 P4의 명령을 맞는 연결로 보낸다. 판단은 common/devmgr.cpp, 이 파일은 손발.
//   p5_devmgr --state DIR [--safety-s S] [--prio P]
//   --state     P4와 같은 상태 폴더. profiles/<ID>.json을 읽기만 한다 (쓰기는 P4)
//   --safety-s  DEV_SYNC가 이만큼 없으면 전부 OFF (기본 30 = policy.json safety_timeout_s. 시험에서만 줄임)
//
// 받는 것 (mermaid_p5): P6의 DEV_HELLO·DEV_BYE, P4의 TIER_CMD·DEV_SYNC
// 보내는 것: P6에 OUT_INT·OUT_STREAM·OUT_PROF·OUT_RESYNC (P6의 받기 스레드는 바로 회신하므로 멈추지 않는다)
// 다시 시작: 시작하면 P6에 OUT_RESYNC (전달될 때까지 1초마다) → P6이 등록된 기기를 DEV_HELLO로 다시 알려 준다.
//   그 HELLO에 실린 'P6이 마지막으로 보낸 INT'로 Tier 상태도 되찾는다 (docs/controller/RF_MESSAGES.md §4)
// P4에게는 먼저 보내지 않는다. 명부는 DEV_SYNC의 회신으로만 준다.
// 안전: 1초마다 확인해서 DEV_SYNC가 30초 없으면 모든 개입 OFF.
// 주행 로그: 안전 OFF 진입·해제와 잡아 둔 ON 적용을 P7에 LOG_CTX로 보낸다 (sender "p5", 2 ms 제한).
//   실패하면 세고(log_fail) 남은 것은 다음 기회(1초 박자나 다음 DEV_SYNC)에 다시 보낸다. 64개 넘게 밀리면 새 사건을 버린다.
//   P7은 아무에게도 MsgSend 하지 않으므로 교착 고리가 생기지 않는다. P6에 보낼 것을 다 보낸 뒤, 잠금을 푼 다음에 보낸다.
// 시작 순서: P6 다음, P4 전 (P6 → P5 → P4 → P3 → P2 → P1).
#include <atomic>
#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include "devmgr.hpp"
#include "ipc.hpp"
#include "log_messages.hpp"
#include "rf_messages.hpp"
#include "store.hpp"

using namespace mm;

namespace {

int64_t now_ms() { return static_cast<int64_t>(ipc::now_ns() / 1000000ULL); }

std::mutex g_log_mu;
void log_line(const char* tag, const std::string& s) {
    std::lock_guard<std::mutex> lk(g_log_mu);
    std::printf("%10.3f %s %s\n", static_cast<double>(now_ms()) / 1000.0, tag, s.c_str());
    std::fflush(stdout);
}

std::string show(const devmgr::Out& o) {
    char b[96];
    if (const auto* i = std::get_if<msg::OutInt>(&o))
        std::snprintf(b, sizeof b, "INT conn %d tier %d %s", i->conn, i->tier, i->on ? "ON" : "OFF");
    else if (const auto* s = std::get_if<msg::OutStream>(&o))
        std::snprintf(b, sizeof b, "STREAM conn %d %s", s->conn, s->on ? "ON" : "OFF");
    else if (const auto* p = std::get_if<msg::OutProf>(&o))
        std::snprintf(b, sizeof b, "PROF conn %d mssq %.2f sens %.3f", p->conn, p->mssq, p->sens);
    else if (const auto* d = std::get_if<msg::OutDrop>(&o))
        std::snprintf(b, sizeof b, "DROP conn %d", d->conn);
    else if (std::holds_alternative<msg::OutResync>(o))
        std::snprintf(b, sizeof b, "RESYNC");
    else
        b[0] = 0;
    return b;
}

union Inbox {
    msg::Hdr h;
    msg::DevHello hello;
    msg::DevBye bye;
    msg::DevSync sync;
    msg::TierCmd cmd;
    _pulse pulse;
    Inbox() {}
};

}  // namespace

int main(int argc, char** argv) {
    std::string state_dir;
    int prio = 14;
    int safety_s = 30;
    for (int i = 1; i < argc; i++) {
        const std::string a = argv[i];
        const bool more = i + 1 < argc;
        if (a == "--state" && more) state_dir = argv[++i];
        else if (a == "--safety-s" && more) safety_s = std::atoi(argv[++i]);
        else if (a == "--prio" && more) prio = std::atoi(argv[++i]);
        else {
            std::fprintf(stderr, "usage: p5_devmgr --state DIR [--safety-s S] [--prio P]\n");
            return 2;
        }
    }
    if (state_dir.empty()) {
        std::fprintf(stderr, "p5: --state is required (P4와 같은 폴더)\n");
        return 2;
    }

    try {
        ipc::set_priority(prio, "p5");
        // 저장된 프로필 읽기만. 없거나 깨졌으면 없는 것으로
        auto load_profile = [state_dir](const std::string& id, Profile& p) {
            if (!store::safe_name(id)) return false;
            std::string text;
            if (!store::read_file(state_dir + "/profiles/" + id + ".json", text)) return false;
            try {
                p = store::parse_profile(text);
                return true;
            } catch (const std::exception& e) {
                log_line("ERR", "profile " + id + ": " + e.what());
                return false;
            }
        };
        if (safety_s < 1) safety_s = 1;
        devmgr::DevMgr core(load_profile, [](const std::string& s) { log_line("..", s); }, safety_s * 1000LL);
        std::mutex mu;  // core와 P6 보내기를 함께 보호: 한 사건의 OUT_*가 순서대로 나가게

        // 로그 사건: core가 mu를 잡은 채 pending에 넣고, 각 스레드가 mu를 푼 뒤 자기 Client로 P7에 보낸다
        std::vector<msg::LogCtx> pending;
        core.set_event_sink([&pending](const devmgr::LogEvent& e) {
            if (pending.size() >= 64) return;  // 메모리가 늘지 않게 (사건은 드물다)
            msg::LogCtx m;
            m.mono_ns = ipc::now_ns();
            msg::copy_id(m.sender, std::string("p5"));
            m.kind = e.kind;
            msg::copy_text(m.value, e.value);
            pending.push_back(m);
        });
        std::atomic<uint64_t> log_fail{0};
        ipc::Client p7_main(msg::kNameP7, msg::kLogTimeoutNs);
        ipc::Client p7_tick(msg::kNameP7, msg::kLogTimeoutNs);
        auto take_log = [&] {  // mu를 잡은 채
            std::vector<msg::LogCtx> out;
            out.swap(pending);
            return out;
        };
        auto send_log = [&](ipc::Client& p7, const std::vector<msg::LogCtx>& evs) {  // mu 밖에서
            for (size_t i = 0; i < evs.size(); i++) {
                if (p7.send(&evs[i], sizeof evs[i])) continue;
                // 한 번 실패하면 멈춘다 (P7이 멈춰 있어도 2 ms만). 남은 것은 되돌려 두고 다음에 다시
                log_fail++;
                std::lock_guard<std::mutex> lk(mu);
                pending.insert(pending.begin(), evs.begin() + static_cast<std::ptrdiff_t>(i), evs.end());
                if (pending.size() > 64) pending.resize(64);
                return;
            }
        };

        ipc::Client p6_main(msg::kNameP6, 200 * 1000000ULL);
        ipc::Client p6_tick(msg::kNameP6, 200 * 1000000ULL);  // Client는 스레드마다 하나
        // mu를 잡은 채 부른다
        bool resync_warned = false;
        auto send_all = [&](ipc::Client& p6, const devmgr::Outs& outs) {
            for (const auto& o : outs) {
                // P6이 다시 시작했으면 옛 연결로 보낸 첫 번은 실패하고 연결이 닫힌다 → 바로 한 번 더 (새로 연결)
                const bool ok = std::visit(
                    [&](const auto& m) { return p6.send(&m, sizeof m) || (!p6.connected() && p6.send(&m, sizeof m)); }, o);
                if (std::holds_alternative<msg::OutResync>(o)) {  // P6이 뜰 때까지 1초마다 → 실패 로그는 한 번만
                    if (ok) core.resync_delivered();
                    if (ok || !resync_warned) log_line(ok ? "P6" : "..", ok ? "RESYNC" : "P6 not up yet, RESYNC will retry");
                    resync_warned = true;
                    continue;
                }
                log_line(ok ? "P6" : "ERR", ok ? show(o) : "cannot send to P6: " + show(o));
            }
        };

        ipc::Server srv(msg::kNameP5);
        log_line("..", std::string("p5_devmgr up: name ") + msg::kNameP5 + ", state " + state_dir + ", safety " +
                           std::to_string(safety_s) + " s");

        // 감시 스레드: 1초마다 (첫 번은 바로: OUT_RESYNC를 빨리 보내려고). 서버를 연 뒤라 다시 오는 HELLO를 받을 수 있다
        std::thread tick([&] {
            for (;;) {
                std::vector<msg::LogCtx> evs;
                {
                    std::lock_guard<std::mutex> lk(mu);
                    send_all(p6_tick, core.on_tick(now_ms()));
                    evs = take_log();
                }
                send_log(p7_tick, evs);
                timespec ts{1, 0};
                nanosleep(&ts, nullptr);
            }
        });
        tick.detach();

        Inbox in;
        uint64_t syncs = 0;
        for (;;) {
            const int rcvid = srv.receive(&in, sizeof in);
            const uint16_t type = in.h.type;
            if (type == msg::DEV_SYNC) {
                const msg::DevSync m = in.sync;
                msg::DevSyncReply rep;
                std::vector<msg::LogCtx> evs;
                {
                    std::lock_guard<std::mutex> lk(mu);
                    rep = core.on_sync(m, now_ms());
                    evs = take_log();
                }
                ipc::Server::reply(rcvid, &rep, sizeof rep);
                send_log(p7_main, evs);  // P4에 회신한 뒤에
                if (++syncs % 6 == 1) {  // 1분에 한 번 명부 출력
                    std::string s = "sync #" + std::to_string(m.seq) + ": " + std::to_string(rep.n) + " device(s)";
                    for (uint32_t i = 0; i < rep.n; i++) s += std::string(" ") + rep.e[i].dev;
                    s += ", log_fail " + std::to_string(log_fail.load());
                    log_line("P4", s);
                }
            } else if (type == msg::DEV_HELLO) {
                const msg::DevHello m = in.hello;
                ipc::Server::reply(rcvid);
                std::lock_guard<std::mutex> lk(mu);
                send_all(p6_main, core.on_hello(m));
            } else if (type == msg::DEV_BYE) {
                const msg::DevBye m = in.bye;
                ipc::Server::reply(rcvid);
                std::lock_guard<std::mutex> lk(mu);
                send_all(p6_main, core.on_bye(m));
            } else if (type == msg::TIER_CMD) {
                const msg::TierCmd m = in.cmd;
                ipc::Server::reply(rcvid);
                log_line("P4", std::string("TIER_CMD ") + m.device + " tier " + std::to_string(m.tier) +
                                   (m.on ? " ON" : " OFF"));
                std::lock_guard<std::mutex> lk(mu);
                send_all(p6_main, core.on_tier_cmd(m));
            } else {
                ipc::Server::error(rcvid, EINVAL);
            }
        }
    } catch (const std::exception& e) {
        std::fprintf(stderr, "p5: %s\n", e.what());
        return 1;
    }
}
