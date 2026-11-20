// P7 로거: P1의 공유 메모리 링(원본 IMU 100 Hz)과 P3·P4·P6의 LOG_*를 주행마다 SQLite 파일 하나로 남긴다.
//   p7_logger [--dir ~/mermaid/logs] [--device-id pi01] [--min-free-mb 1024] [--config DIR] [--ring NAME]
//             [--prio 11] [--once]
//   --config DIR  imu_mount.json, policy.json, thresholds.json을 주행 파일 meta에 그대로 적는다
//   --once        첫 주행 파일을 닫으면 끝 (시험용)
// 설계: docs/DATA_LOGGING.md §6. 판단과 SQLite 쓰기는 common/ride_log.cpp.
//
// 스레드 두 개 (로그는 제어를 막지 않는다):
//   받기 스레드(main, 우선순위 --prio): mermaid_p7로 LOG_*를 받아 큐에 복사하고 바로 회신. 디스크 I/O 없음
//   쓰기 스레드(우선순위 --prio − 2): 200 ms마다 링을 비우고 큐를 꺼내 SQLite에. 트랜잭션은 1초에 한 번
// P7은 아무에게도 MsgSend 하지 않는다. 시작 순서: P6 → P5 → P7 → P4 → P3 → P2 → P1 (P1이 링을 만들면 1초 안에 연다)
// SIGINT·SIGTERM: 열린 주행을 'shutdown'으로 닫고 끝낸다.
#include <signal.h>
#include <unistd.h>

#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <thread>
#include <vector>

#include "ipc.hpp"
#include "log_messages.hpp"
#include "ride_log.hpp"
#include "sample_ring.hpp"
#include "store.hpp"

#ifndef MM_GIT_COMMIT
#define MM_GIT_COMMIT "unknown"
#endif

using namespace mm;

namespace {

std::atomic<bool> g_stop{false};
void on_signal(int) { g_stop = true; }

// 받은 메시지의 크기는 종류로 정한다 (모두 고정 크기)
size_t size_of(uint16_t type) {
    switch (type) {
        case msg::LOG_INFER: return sizeof(msg::LogInfer);
        case msg::LOG_DECISION: return sizeof(msg::LogDecision);
        case msg::LOG_CTX: return sizeof(msg::LogCtx);
        case msg::LOG_SYM: return sizeof(msg::LogSym);
        default: return 0;
    }
}

std::string trimmed_file(const std::string& path) {
    std::string t;
    if (!store::read_file(path, t)) return "";
    while (!t.empty() && (t.back() == '\n' || t.back() == '\r' || t.back() == ' ')) t.pop_back();
    return t;
}

}  // namespace

int main(int argc, char** argv) {
    std::string dir, config_dir, device_id = "pi01", ring_name = ring::kDefaultName;
    double min_free_mb = 1024;
    int prio = 11;
    bool once = false;
    for (int i = 1; i < argc; i++) {
        const std::string a = argv[i];
        const bool more = i + 1 < argc;
        if (a == "--dir" && more) dir = argv[++i];
        else if (a == "--config" && more) config_dir = argv[++i];
        else if (a == "--device-id" && more) device_id = argv[++i];
        else if (a == "--ring" && more) ring_name = argv[++i];
        else if (a == "--min-free-mb" && more) min_free_mb = std::atof(argv[++i]);
        else if (a == "--prio" && more) prio = std::atoi(argv[++i]);
        else if (a == "--once") once = true;
        else {
            std::fprintf(stderr,
                         "usage: p7_logger [--dir DIR] [--device-id ID] [--min-free-mb MB] [--config DIR] [--ring NAME] "
                         "[--prio P] [--once]\n");
            return 2;
        }
    }
    if (dir.empty()) {
        const char* home = std::getenv("HOME");
        dir = std::string(home ? home : ".") + "/mermaid/logs";
    }
    if (!store::safe_name(device_id)) {
        std::fprintf(stderr, "p7: bad device id %s\n", device_id.c_str());
        return 2;
    }

    try {
        ipc::set_priority(prio, "p7");
        std::atomic<bool> done{false};
        rlog::Config cfg;
        cfg.dir = dir;
        cfg.device_id = device_id;
        cfg.min_free_mb = min_free_mb;
        cfg.meta["git_commit"] = MM_GIT_COMMIT;
        if (!config_dir.empty()) {
            const std::pair<const char*, const char*> files[] = {
                {"imu_mount", "imu_mount.json"}, {"policy_json", "policy.json"}, {"thresholds", "thresholds.json"}};
            for (const auto& f : files) {
                const std::string t = trimmed_file(config_dir + "/" + f.second);
                if (!t.empty()) cfg.meta[f.first] = t;
                else std::fprintf(stderr, "p7: %s/%s not found, not in meta\n", config_dir.c_str(), f.second);
            }
        }
        cfg.on_closed = [&](const std::string& path, const std::string& reason) {
            std::printf("RIDE_CLOSED %s %s\n", path.c_str(), reason.c_str());
            std::fflush(stdout);
            if (once) done = true;
        };
        rlog::Logger lg(cfg);
        lg.start();
        if (lg.stats().recovered || lg.stats().broken)
            std::fprintf(stderr, "p7: recovered %llu, broken %llu ride file(s)\n", (unsigned long long)lg.stats().recovered,
                         (unsigned long long)lg.stats().broken);
        rlog::MsgQueue queue;
        ipc::Server srv(msg::kNameP7);

        struct sigaction sa {};
        sa.sa_handler = on_signal;
        sigemptyset(&sa.sa_mask);
        sigaction(SIGINT, &sa, nullptr);
        sigaction(SIGTERM, &sa, nullptr);

        std::fprintf(stderr, "p7: logging to %s/rides (session %llu)\n", dir.c_str(), (unsigned long long)lg.session());

        std::thread writer([&] {
            ipc::set_priority(prio > 2 ? prio - 2 : 1, "p7 writer");
            ring::Reader rd;
            bool first_open = true;
            uint64_t next_open = 0, lost_seen = 0;
            std::vector<ring::Item> items;
            std::vector<char> m;
            for (;;) {
                const uint64_t now = ipc::now_ns();
                if (!rd.is_open() && now >= next_open) {
                    std::string err;
                    next_open = now + 1000000000ULL;
                    if (rd.open(ring_name, err)) {
                        if (first_open) rd.skip_to_head();  // 지난 실행의 칸은 이미 기록했거나 복구했다
                        first_open = false;
                        lost_seen = rd.stats().lost + rd.stats().torn;
                    }
                }
                if (rd.is_open()) {
                    const uint64_t ec = rd.stats().epoch_changes;
                    items.clear();
                    rd.poll(items, 1000000);
                    if (rd.stats().epoch_changes != ec) lg.on_p1_restart(now);
                    const uint64_t lost = rd.stats().lost + rd.stats().torn;
                    if (lost > lost_seen) lg.on_ring_lost(lost - lost_seen);
                    lost_seen = lost;
                    const uint32_t mask = rd.col_mask();
                    for (const ring::Item& it : items) lg.on_item(it, mask, now);
                }
                while (queue.pop(m)) lg.on_msg(m.data(), m.size(), now, queue.dropped());
                lg.tick(now);
                if (g_stop || done) {
                    lg.shutdown(ipc::now_ns());
                    const rlog::Stats& s = lg.stats();
                    std::printf("{\"proc\": \"p7\", \"session\": %llu, \"rides_opened\": %llu, \"rides_closed\": %llu, "
                                "\"rides_refused\": %llu, \"samples\": %llu, \"hold_samples\": %llu, \"ring_lost\": %llu, "
                                "\"infer_rows\": %llu, \"decision_rows\": %llu, \"ctx_rows\": %llu, \"label_rows\": %llu, "
                                "\"queue_drop\": %llu, \"outside_ride\": %llu, \"bad_msgs\": %llu, \"sqlite_errors\": %llu, "
                                "\"recovered\": %llu, \"broken\": %llu, \"dup_msgs\": %llu}\n",
                                (unsigned long long)lg.session(), (unsigned long long)s.rides_opened,
                                (unsigned long long)s.rides_closed, (unsigned long long)s.rides_refused,
                                (unsigned long long)s.samples, (unsigned long long)s.hold_samples,
                                (unsigned long long)s.ring_lost, (unsigned long long)s.infer_rows,
                                (unsigned long long)s.decision_rows, (unsigned long long)s.ctx_rows,
                                (unsigned long long)s.label_rows, (unsigned long long)queue.dropped(),
                                (unsigned long long)s.outside_ride, (unsigned long long)s.bad_msgs,
                                (unsigned long long)s.sqlite_errors, (unsigned long long)s.recovered,
                                (unsigned long long)s.broken, (unsigned long long)s.dup_msgs);
                    std::fflush(stdout);
                    std::fflush(stderr);
                    _exit(0);  // 받기 스레드는 MsgReceive에 묶여 있다
                }
                timespec ts{0, 200 * 1000000};
                nanosleep(&ts, nullptr);
            }
        });
        writer.detach();

        // 받기 스레드 (main)
        alignas(8) static char buf[msg::kMaxLogBytes];
        for (;;) {
            const int rcvid = srv.receive(buf, sizeof buf);
            msg::Hdr h;
            std::memcpy(&h, buf, sizeof h);
            const size_t n = size_of(h.type);
            if (n == 0) {
                ipc::Server::error(rcvid, EINVAL);
                continue;
            }
            queue.push(buf, n);
            ipc::Server::reply(rcvid);
        }
    } catch (const std::exception& ex) {
        std::fprintf(stderr, "p7: %s\n", ex.what());
        return 1;
    }
}
