// P6 통신: ESP32 브리지(UART)와 Pi 안 프로세스(QNX 메시지) 사이 번역. 판단은 common/comm.cpp가 하고
// 이 파일은 손발만 한다: /dev/ser3 읽고 쓰기, P4·P5에 보내기, P5·P2에게서 받기.
//   p6_comm [--dev /dev/ser3] [--no-p4] [--no-link-timeout] [--verbose] [--prio P]
//   --no-p4            CTX_*를 P4에 보내지 않고 출력만 (P4가 아직 CTX_*를 받지 않을 때)
//   --no-link-timeout  기기가 5초 조용해도 끊지 않음 (HB를 안 보내는 nRF Connect로 손으로 시험할 때)
//   --verbose          HB, MOT 줄까지 모두 출력
//
// 스레드 두 개 (교착 방지, docs/controller/RF_MESSAGES.md §2-2):
//   UART 스레드(main): UART 읽기 → 규칙 → UART 쓰기 / P5·P4에 MsgSend. 100 ms마다 시계(HB, HELLO 5초, 무응답 5초)
//   받기 스레드: mermaid_p6로 P5(OUT_*)와 P2(MOTION)를 받아 바로 회신 → 규칙 → UART 쓰기. MsgSend 하지 않는다
//   GPS (#18): 앱의 M09 GPS는 UART 스레드가 P7(LOG_GPS)과 P2(GPS_SPEED, 3 ms 제한, 실패는 gps_fail로 세기만)에 보낸다.
//     P2도 P6에 MOTION을 보내지만 P6 쪽 받는 이는 받기 스레드라 고리가 없다 (RF_MESSAGES.md §2-2)
// 주행 로그 (docs/controller/DATA_LOGGING.md §6-2): 폰·HUD와 오간 사건을 모두 P7에 LOG_CTX로 보낸다 (HB·MOT는 빼고).
//   기기가 보낸 것: HELLO(P5에 전달됨), BYE, SCR, OVR, PROF / 링크: CONNECT, DISCONNECT, DROP + 이유 /
//   Pi가 내보낸 것: INT, PROF 회신 / 버린 것: REJECT + 이유와 원문 앞부분. 멀미 점수 SYM은 LOG_SYM으로.
//   10초마다 다시 보내는 SCR·PROF(CtxResend)는 사건이 아니라서 보내지 않는다.
//   사건은 생긴 자리(어느 스레드든)에서 시각을 찍어 작은 큐(256개)에 넣고, UART 스레드만 P7에 보낸다:
//   받기 스레드는 MsgSend를 하지 않는다는 규칙을 지키려는 것. P7에는 2 ms 제한. 한 번 실패하면(log_fail) 거기서 멈추고
//   남은 것은 큐에 둔 채 다음 바퀴에 다시 보낸다 → P7이 없거나 멈춰도 UART 스레드는 한 바퀴에 2 ms 넘게 잡히지 않고,
//   P7이 잠깐 늦은 것으로는 사건을 잃지 않는다. 큐가 차면 가장 오래된 것부터 버리고 센다 (log_drop).
// 시작 순서: P6이 가장 먼저 (P5, P2가 P6에 보낸다). P5가 늦게 떠도 등록은 1초마다 다시 보낸다.
// 다시 시작 (docs/controller/RF_MESSAGES.md §6):
//   P6이 다시 시작 → 시작할 때 0~8|DROP으로 브리지의 모든 연결을 끊고 P5에 DEV_BYE ALL. 기기가 다시 붙어 HELLO
//   P5가 다시 시작 → P5가 OUT_RESYNC를 보냄 → 등록된 기기의 DEV_HELLO를 다음 1초 박자에 다시 보냄
#include <atomic>
#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <mutex>
#include <string>
#include <thread>
#include <variant>

#include "comm.hpp"
#include "ipc.hpp"
#include "log_messages.hpp"
#include "rf_messages.hpp"

// termios.h는 프로젝트 헤더 뒤에: termios.h가 B0, B50 같은 보율 이름을 매크로로 정의해서
// 앞에 오면 filters.hpp의 지역 변수 B0, B1, B2가 깨진다
#include <fcntl.h>
#include <poll.h>
#include <termios.h>
#include <unistd.h>

using namespace mm;

namespace {

int64_t now_ms() { return static_cast<int64_t>(ipc::now_ns() / 1000000ULL); }

std::mutex g_log_mu;
void log_line(const char* tag, const std::string& s) {
    std::lock_guard<std::mutex> lk(g_log_mu);
    std::printf("%10.3f %s %s\n", static_cast<double>(now_ms()) / 1000.0, tag, s.c_str());
    std::fflush(stdout);
}

bool quiet_line(const std::string& s) {
    return s.find("\"t\":\"HB\"") != std::string::npos || s.find("\"t\":\"MOT\"") != std::string::npos;
}

// /dev/ser3 raw 모드 (pi_test/p3_uart_test.cpp에서 확인한 설정)
int open_uart(const char* dev) {
    const int fd = open(dev, O_RDWR | O_NOCTTY);
    if (fd < 0) return -1;
    termios t;
    if (tcgetattr(fd, &t) < 0) return -1;
    t.c_iflag &= ~(IGNBRK | BRKINT | PARMRK | ISTRIP | INLCR | IGNCR | ICRNL | IXON | IXOFF);
    t.c_oflag &= ~OPOST;
    t.c_lflag &= ~(ECHO | ECHONL | ICANON | ISIG | IEXTEN);
    t.c_cflag &= ~(CSIZE | PARENB | CSTOPB);
    t.c_cflag |= CS8 | CLOCAL | CREAD;
    // VMIN 0이면 QNX 직렬 드라이버가 '읽어도 안 막힘'을 '읽을 것 있음'으로 알려 poll이 바로 돌아오고, UART 스레드가
    // 쉬지 않고 돈다 (Pi에서 CPU 한 코어의 약 40% + 드라이버). 1바이트 이상 있을 때만 깨어나게 한다
    t.c_cc[VMIN] = 1;
    t.c_cc[VTIME] = 0;
    cfsetispeed(&t, B115200);
    cfsetospeed(&t, B115200);
    if (tcsetattr(fd, TCSANOW, &t) < 0) return -1;
    tcflush(fd, TCIOFLUSH);
    return fd;
}

// P7에 보낼 사건 큐. 두 스레드가 넣고 UART 스레드만 꺼낸다. 차면 가장 오래된 것을 버린다 (메모리가 늘지 않게)
class LogQueue {
public:
    using Item = std::variant<msg::LogCtx, msg::LogSym, msg::LogGps>;
    static constexpr size_t kMax = 256;

    // mono_ns = 0: 지금. 기기가 보낸 사건은 줄을 받은 시각을 넘겨 준다 (P4·P5에 전달한 뒤에 넣더라도)
    void ctx(const std::string& sender, msg::CtxKind kind, const std::string& value, uint64_t mono_ns = 0) {
        msg::LogCtx m;
        m.mono_ns = mono_ns ? mono_ns : ipc::now_ns();  // 사건이 생긴 시각 (보내는 시각이 아니라)
        msg::copy_id(m.sender, sender);
        m.kind = kind;
        msg::copy_text(m.value, value);
        push(m);
    }
    void sym(const std::string& phone, int misc, uint64_t mono_ns = 0) {
        msg::LogSym m;
        m.mono_ns = mono_ns ? mono_ns : ipc::now_ns();
        msg::copy_id(m.phone, phone);
        m.misc = misc;
        push(m);
    }
    void gps(const comm::Gps& g, uint64_t mono_ns) {
        msg::LogGps m;
        m.mono_ns = mono_ns ? mono_ns : ipc::now_ns();
        msg::copy_id(m.phone, g.phone);
        m.spd = g.spd;
        m.brg = g.brg;
        m.acc = g.acc;
        m.alt = g.alt;
        push(m);
    }
    std::deque<Item> take() {
        std::deque<Item> out;
        std::lock_guard<std::mutex> lk(mu_);
        out.swap(q_);
        return out;
    }
    // 못 보낸 것을 앞에 되돌린다 (그 사이 들어온 새 사건보다 먼저). 넘치면 가장 오래된 것부터 버린다
    void put_back(std::deque<Item>& rest) {
        std::lock_guard<std::mutex> lk(mu_);
        q_.insert(q_.begin(), rest.begin(), rest.end());
        while (q_.size() > kMax) {
            q_.pop_front();
            dropped_++;
        }
    }
    uint64_t dropped() const { return dropped_.load(); }

private:
    void push(const Item& it) {
        std::lock_guard<std::mutex> lk(mu_);
        if (q_.size() >= kMax) {
            q_.pop_front();
            dropped_++;
        }
        q_.push_back(it);
    }
    std::mutex mu_;
    std::deque<Item> q_;
    std::atomic<uint64_t> dropped_{0};
};

struct Shared {
    int fd = -1;
    bool verbose = false;
    std::mutex core_mu;   // comm::Comm 보호 (규칙 계산만, 짧게)
    std::mutex write_mu;  // UART 쓰기 보호 (두 스레드가 같이 쓴다)
    comm::Comm* core = nullptr;
    std::atomic<uint64_t> rx_lines{0}, tx_lines{0}, mot_lines{0};
};

void uart_write(Shared& sh, const std::string& line) {
    const std::string out = line + "\n";
    {
        std::lock_guard<std::mutex> lk(sh.write_mu);
        size_t off = 0;
        while (off < out.size()) {
            const ssize_t n = write(sh.fd, out.data() + off, out.size() - off);
            if (n < 0) {
                if (errno == EINTR) continue;
                log_line("ERR", std::string("uart write: ") + std::strerror(errno));
                return;
            }
            off += static_cast<size_t>(n);
        }
    }
    sh.tx_lines++;
    if (line.find("\"t\":\"MOT\"") != std::string::npos) sh.mot_lines++;
    if (sh.verbose || !quiet_line(line)) log_line("TX", line);
}

// ---------- 받기 스레드: P5와 P2가 보낸 것 ----------
union Inbox {
    msg::Hdr h;
    msg::OutInt out_int;
    msg::OutStream out_stream;
    msg::OutProf out_prof;
    msg::OutDrop out_drop;
    msg::OutResync out_resync;
    msg::Motion motion;
    _pulse pulse;
    Inbox() {}
};

void receive_loop(Shared& sh, ipc::Server& srv) {
    Inbox in;
    for (;;) {
        const int rcvid = srv.receive(&in, sizeof in);
        const uint16_t type = in.h.type;
        comm::Actions acts;
        // 먼저 복사하고 회신한다 (보낸 쪽을 바로 풀어 줌). 회신 뒤에 처리
        if (type == msg::OUT_INT) {
            const msg::OutInt m = in.out_int;
            ipc::Server::reply(rcvid);
            std::lock_guard<std::mutex> lk(sh.core_mu);
            acts = sh.core->on_out_int(m);
        } else if (type == msg::OUT_STREAM) {
            const msg::OutStream m = in.out_stream;
            ipc::Server::reply(rcvid);
            log_line("P5", "stream conn " + std::to_string(m.conn) + (m.on ? " ON" : " OFF"));
            std::lock_guard<std::mutex> lk(sh.core_mu);
            acts = sh.core->on_out_stream(m);
        } else if (type == msg::OUT_PROF) {
            const msg::OutProf m = in.out_prof;
            ipc::Server::reply(rcvid);
            std::lock_guard<std::mutex> lk(sh.core_mu);
            acts = sh.core->on_out_prof(m);
        } else if (type == msg::OUT_DROP) {
            const msg::OutDrop m = in.out_drop;
            ipc::Server::reply(rcvid);
            std::lock_guard<std::mutex> lk(sh.core_mu);
            acts = sh.core->on_out_drop(m);
        } else if (type == msg::OUT_RESYNC) {
            ipc::Server::reply(rcvid);
            log_line("P5", "RESYNC (P5 restarted)");
            std::lock_guard<std::mutex> lk(sh.core_mu);
            sh.core->on_resync();  // 표만 바꾼다. HELLO는 UART 스레드의 on_tick이 보낸다
        } else if (type == msg::MOTION) {
            const msg::Motion m = in.motion;
            ipc::Server::reply(rcvid);
            std::lock_guard<std::mutex> lk(sh.core_mu);
            acts = sh.core->on_motion(m);
        } else {
            ipc::Server::error(rcvid, EINVAL);
            continue;
        }
        // 이 스레드가 만드는 것은 UART 줄뿐이다 (MsgSend 없음)
        for (const auto& a : acts)
            if (const auto* u = std::get_if<comm::UartLine>(&a)) uart_write(sh, u->text);
    }
}

}  // namespace

int main(int argc, char** argv) {
    std::string dev = "/dev/ser3";
    bool no_p4 = false, no_link_timeout = false, verbose = false;
    int prio = 12;
    for (int i = 1; i < argc; i++) {
        const std::string a = argv[i];
        const bool more = i + 1 < argc;
        if (a == "--dev" && more) dev = argv[++i];
        else if (a == "--no-p4") no_p4 = true;
        else if (a == "--no-link-timeout") no_link_timeout = true;
        else if (a == "--verbose") verbose = true;
        else if (a == "--prio" && more) prio = std::atoi(argv[++i]);
        else {
            std::fprintf(stderr, "usage: p6_comm [--dev /dev/ser3] [--no-p4] [--no-link-timeout] [--verbose] [--prio P]\n");
            return 2;
        }
    }

    try {
        ipc::set_priority(prio, "p6");
        Shared sh;
        sh.verbose = verbose;
        sh.fd = open_uart(dev.c_str());
        if (sh.fd < 0) {
            std::fprintf(stderr, "p6: cannot open %s: %s (uart3.sh 실행했나? 다른 프로그램이 열고 있나?)\n", dev.c_str(),
                         std::strerror(errno));
            return 1;
        }
        comm::Comm core([](const std::string& s) { log_line("..", s); }, no_link_timeout ? 0 : comm::kLinkTimeoutMs);
        sh.core = &core;
        LogQueue logq;
        // core_mu를 잡은 채 불린다 (두 스레드 모두). 큐에 넣기만 하므로 멈추지 않는다
        core.set_event_sink([&logq](const comm::LogEvent& e) { logq.ctx(e.sender, e.kind, e.value); });

        ipc::Server srv(msg::kNameP6);
        log_line("..", "p6_comm up: " + dev + ", name " + msg::kNameP6 + (no_p4 ? ", P4 off" : "") +
                           (no_link_timeout ? ", link timeout off" : ""));
        std::thread rx([&] {
            try {
                receive_loop(sh, srv);
            } catch (const std::exception& e) {
                log_line("ERR", std::string("receive thread: ") + e.what());
                std::exit(1);
            }
        });
        rx.detach();

        // UART 스레드 (main)
        ipc::Client p5(msg::kNameP5, 200 * 1000000ULL);
        ipc::Client p4(msg::kNameP4, 200 * 1000000ULL);
        // 상대가 다시 시작했으면 옛 연결로 보낸 첫 번은 실패하고 연결이 닫힌다 → 바로 한 번 더 (새로 연결).
        // 시간 초과(상대가 바쁨)는 연결이 남으므로 다시 보내지 않는다
        auto send_to = [](ipc::Client& c, const void* m, size_t n) {
            return c.send(m, n) || (!c.connected() && c.send(m, n));
        };
        ipc::Client p7(msg::kNameP7, msg::kLogTimeoutNs);
        uint64_t log_fail = 0, log_sent = 0;
        // P2에 GPS 속도 (#18). 짧게 기다리고 실패는 세기만 (P2가 없거나 바쁘면 그 값은 버린다: 1~10 Hz로 다시 온다)
        ipc::Client p2(msg::kNameP2, msg::kGpsSpeedTimeoutNs);
        uint64_t gps_rx = 0, gps_sent = 0, gps_fail = 0;
        // 기기가 보낸 사건(HELLO·BYE·SCR·OVR·PROF)의 시각 = 그 줄을 받아 규칙을 통과한 때 (run이 시작할 때 한 번 찍는다).
        // P4·P5에 전달한 뒤에 찍으면, 그 결과로 받기 스레드가 내보낸 INT가 원인인 OVR·HELLO보다 앞 시각으로 남는다
        uint64_t batch_ns = 0;
        auto log_ctx = [&](const std::string& sender, msg::CtxKind kind, const std::string& value) {
            logq.ctx(sender, kind, value, batch_ns);
        };
        // 큐를 비워 P7에 보낸다 (UART 스레드만). 한 번 실패하면 멈추고 남은 것은 큐에 되돌린다:
        // P7이 멈춰 있을 때 사건 수 × 2 ms를 기다리지 않게. 다음 바퀴(100 ms 안)에 다시 한 번 보내 본다
        auto flush_log = [&] {
            std::deque<LogQueue::Item> batch = logq.take();
            while (!batch.empty()) {
                if (!std::visit([&](const auto& m) { return p7.send(&m, sizeof m); }, batch.front())) {
                    log_fail++;
                    logq.put_back(batch);
                    return;
                }
                log_sent++;
                batch.pop_front();
            }
        };
        auto jstr = [](const char* s) {  // ID는 safe_name 규칙이라 따옴표만 막으면 된다
            std::string out = "\"";
            for (const char* c = s; *c; c++)
                if (*c != '"' && *c != '\\') out += *c;
            return out + "\"";
        };
        bool p5_warned = false, p4_warned = false;
        auto send_p5 = [&](const void* m, size_t n, const std::string& what) {
            if (send_to(p5, m, n)) {
                if (p5_warned) log_line("..", "P5 reachable again");
                p5_warned = false;
                return true;
            }
            if (!p5_warned) log_line("ERR", "cannot send to P5 (" + what + "), will retry");
            p5_warned = true;
            return false;
        };
        auto p4_result = [&](bool ok) {
            std::lock_guard<std::mutex> lk(sh.core_mu);
            core.p4_result(ok);
        };
        // quiet: 기억해 둔 SCR·PROF를 다시 보낼 때 (10초마다라 성공하면 로그를 남기지 않는다)
        auto send_p4 = [&](const void* m, size_t n, const std::string& what, bool quiet = false) {
            if (!quiet || verbose) log_line("P4", what);
            if (no_p4) return true;
            if (send_to(p4, m, n)) {
                if (p4_warned) log_line("..", "P4 reachable again (" + what + ")");
                p4_warned = false;
                p4_result(true);
                return true;
            }
            if (!p4_warned) log_line("ERR", "cannot send to P4 (" + what + "), will resend screen and profile");
            p4_warned = true;
            p4_result(false);
            return false;
        };
        auto run = [&](const comm::Actions& acts) {
            batch_ns = ipc::now_ns();
            for (const auto& a : acts) {
                if (const auto* u = std::get_if<comm::UartLine>(&a)) {
                    uart_write(sh, u->text);
                } else if (const auto* h = std::get_if<msg::DevHello>(&a)) {
                    const std::string what = "HELLO conn " + std::to_string(h->conn) + " " + h->dev;
                    if (send_p5(h, sizeof *h, what)) {
                        log_line("P5", what);
                        log_ctx(h->dev, msg::CtxKind::HELLO,
                                "{\"conn\":" + std::to_string(h->conn) + ",\"role\":" +
                                    std::to_string(static_cast<int>(h->role)) + ",\"ver\":" + std::to_string(h->ver) + "}");
                        std::lock_guard<std::mutex> lk(sh.core_mu);
                        core.hello_delivered(h->conn, h->dev);
                    }
                } else if (const auto* b = std::get_if<msg::DevBye>(&a)) {
                    const std::string what =
                        b->conn == msg::kConnAll ? std::string("BYE ALL") : "BYE conn " + std::to_string(b->conn);
                    if (send_p5(b, sizeof *b, what)) log_line("P5", what);
                    log_ctx(b->conn == msg::kConnAll ? std::string("all") : "conn" + std::to_string(b->conn), msg::CtxKind::BYE,
                            "{\"conn\":" + std::to_string(b->conn) + "}");
                } else if (const auto* s = std::get_if<msg::CtxScreen>(&a)) {
                    send_p4(s, sizeof *s,
                            std::string("SCR ") + s->sender + " on=" + std::to_string(s->on) + " att=" + std::to_string(s->att));
                    log_ctx(s->sender, msg::CtxKind::SCR,
                            "{\"on\":" + std::to_string(s->on) + ",\"att\":" + std::to_string(s->att) + "}");
                } else if (const auto* o = std::get_if<msg::CtxOvr>(&a)) {
                    send_p4(o, sizeof *o, std::string("OVR ") + o->sender + " -> " + o->device + " on=" + std::to_string(o->on));
                    log_ctx(o->sender, msg::CtxKind::OVR,
                            "{\"device\":" + jstr(o->device) + ",\"on\":" + std::to_string(o->on) + "}");
                } else if (const auto* p = std::get_if<msg::CtxProf>(&a)) {
                    char buf[160];
                    std::snprintf(buf, sizeof buf, "PROF %s mssq=%.2f sens=%.3f", p->phone, p->mssq, p->sens);
                    send_p4(p, sizeof *p, buf);
                    std::snprintf(buf, sizeof buf, "{\"mssq\":%.9g,\"sens\":%.9g}", p->mssq, p->sens);
                    log_ctx(p->phone, msg::CtxKind::PROF, buf);
                } else if (const auto* r = std::get_if<comm::CtxResend>(&a)) {
                    bool ok = true;
                    if (r->has_scr)
                        ok = send_p4(&r->scr, sizeof r->scr,
                                     std::string("resend SCR ") + r->scr.sender + " on=" + std::to_string(r->scr.on) +
                                         " att=" + std::to_string(r->scr.att),
                                     true);
                    if (ok && r->has_prof) {
                        char buf[160];
                        std::snprintf(buf, sizeof buf, "resend PROF %s mssq=%.2f sens=%.3f", r->prof.phone, r->prof.mssq,
                                      r->prof.sens);
                        send_p4(&r->prof, sizeof r->prof, buf, true);
                    }
                } else if (const auto* y = std::get_if<comm::Sym>(&a)) {
                    log_line("P7", "SYM " + y->phone + " misc=" + std::to_string(y->misc));
                    logq.sym(y->phone, y->misc, batch_ns);  // P4에는 보내지 않는다 (기록 전용)
                } else if (const auto* g = std::get_if<comm::Gps>(&a)) {
                    gps_rx++;
                    logq.gps(*g, batch_ns);
                    msg::GpsSpeed gs;
                    gs.mono_ns = batch_ns;
                    gs.spd = g->spd;
                    gs.acc = g->acc;
                    msg::copy_id(gs.phone, g->phone);
                    if (p2.send(&gs, sizeof gs) || (!p2.connected() && p2.send(&gs, sizeof gs))) gps_sent++;
                    else gps_fail++;
                    if (verbose) {
                        char buf[160];
                        std::snprintf(buf, sizeof buf, "GPS %s spd=%.2f acc=%.1f", g->phone.c_str(), g->spd, g->acc);
                        log_line("P2", buf);
                    }
                }
            }
            flush_log();  // 받기 스레드가 넣은 사건도 여기서 나간다 (적어도 100 ms마다)
        };

        // 시작: 브리지의 모든 연결을 끊는다 (P6만 다시 시작했을 때 기기가 다시 HELLO 하게)
        {
            comm::Actions acts;
            {
                std::lock_guard<std::mutex> lk(sh.core_mu);
                acts = core.on_start(now_ms());
            }
            run(acts);
        }

        comm::LineAssembler asm_;
        int64_t next_stat = now_ms() + 10000;
        for (;;) {
            pollfd pfd{sh.fd, POLLIN, 0};
            const int r = poll(&pfd, 1, 100);
            if (r > 0 && (pfd.revents & POLLIN)) {
                char buf[256];
                const ssize_t n = read(sh.fd, buf, sizeof buf);
                if (n > 0) {
                    asm_.feed(buf, static_cast<size_t>(n), [&](const std::string& line) {
                        sh.rx_lines++;
                        if (sh.verbose || !quiet_line(line)) log_line("RX", line);
                        comm::Actions acts;
                        {
                            std::lock_guard<std::mutex> lk(sh.core_mu);
                            acts = core.on_uart_line(line, now_ms());
                        }
                        run(acts);  // MsgSend는 잠금 밖에서
                    });
                }
            }
            comm::Actions acts;
            {
                std::lock_guard<std::mutex> lk(sh.core_mu);
                acts = core.on_tick(now_ms());
            }
            run(acts);
            if (now_ms() >= next_stat) {
                next_stat += 10000;
                log_line("..", "stat: rx " + std::to_string(sh.rx_lines.load()) + " lines, tx " +
                                   std::to_string(sh.tx_lines.load()) + " (MOT " + std::to_string(sh.mot_lines.load()) +
                                   "), log_sent " + std::to_string(log_sent) + ", log_fail " + std::to_string(log_fail) +
                                   ", log_drop " + std::to_string(logq.dropped()) + ", gps " + std::to_string(gps_rx) +
                                   " (to P2 " + std::to_string(gps_sent) + ", fail " + std::to_string(gps_fail) + ")");
            }
        }
    } catch (const std::exception& e) {
        std::fprintf(stderr, "p6: %s\n", e.what());
        return 1;
    }
}
