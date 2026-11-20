// P6 통신의 핵심 로직: UART 줄(ESP32) ↔ QNX 메시지 번역. 표준 C++17만 사용 (QNX API 없음 → Mac에서 테스트).
// /dev/ser3를 열고 MsgSend / MsgReceive 하는 일은 QNX 프로세스 qnx/p6_comm.cpp가 한다 (아직 없음).
// 규칙: protocol/PROTOCOL.md §1·§3, docs/RF_MESSAGES.md §4.
//
// 쓰는 법: 일이 생길 때마다 on_*()를 부르면 해야 할 일 목록(Actions)이 돌아온다.
//   UartLine        → '\n'을 붙여 UART에 쓴다
//   DevHello/DevBye → P5에 MsgSend
//   Ctx*            → P4에 MsgSend
//   CtxResend       → 기억해 둔 SCR·PROF를 P4에 다시 MsgSend (조용히). 결과는 p4_result()로 알려 준다
// 시각은 인자로 받는다 (테스트에서 시간을 마음대로 돌리려고). 단위 ms, 단조 시계.
//
// 스레드: 이 클래스는 잠그지 않는다. 프로세스에서 뮤텍스를 잡고 on_*()를 부르고,
// 뮤텍스를 푼 뒤 Actions를 실행한다 (MsgSend는 멈출 수 있으므로 잠근 채 하지 않는다).
#pragma once
#include <cstdint>
#include <functional>
#include <string>
#include <string_view>
#include <variant>
#include <vector>

#include "log_messages.hpp"
#include "rf_messages.hpp"

namespace mm::comm {

constexpr int64_t kHelloTimeoutMs = 5000;  // CONNECT 후 이 안에 HELLO가 없으면 DROP
constexpr int64_t kHbPeriodMs = 1000;      // ALL HB 주기
constexpr int64_t kLinkTimeoutMs = 5000;   // 등록된 기기에서 이만큼 아무것도 안 오면 끊긴 것으로 봄 (PROTOCOL.md §3)
constexpr size_t kMaxFrame = 512;          // UART 한 줄 최대 (PROTOCOL.md §1)
constexpr int64_t kCtxRefreshMs = 10000;   // 연결마다 마지막 SCR·PROF를 P4에 다시 보내는 주기 (P4가 조용히 재시작한 경우)

// UART 바이트 → 줄. '\n'까지 이어 붙이고 '\r'은 버린다. kMaxFrame보다 긴 줄은 다음 '\n'까지 버린다.
class LineAssembler {
public:
    void feed(const char* data, size_t n, const std::function<void(const std::string&)>& on_line);

private:
    std::string buf_;
    bool overflow_ = false;
};

// "<id>|<payload>" (ESP32 → Pi 방향이라 id는 0~8만)
struct Frame {
    uint8_t conn = 0;
    std::string payload;
};
bool parse_frame(std::string_view line, Frame& out);

struct UartLine {
    std::string text;  // '\n' 없이
};
// 기기는 SCR를 바뀔 때만, PROF를 연결 직후 한 번만 보낸다. P4가 그때 없었거나 뒤에 다시 떴으면 P4는 모른다.
// 그래서 연결마다 마지막 값을 기억했다가 다시 보낸다 (OVR는 동작이라 다시 보내지 않는다).
struct CtxResend {
    bool has_scr = false, has_prof = false;
    msg::CtxScreen scr;
    msg::CtxProf prof;
};
// M08 SYM: 앱이 보낸 멀미 점수 (0~10 정수, 규칙 통과). P7에 LOG_SYM으로만 보낸다 (P4에는 안 보냄: 기록 전용)
struct Sym {
    std::string phone;
    int misc = 0;
};
using Action = std::variant<UartLine, msg::DevHello, msg::DevBye, msg::CtxScreen, msg::CtxOvr, msg::CtxProf, CtxResend, Sym>;
using Actions = std::vector<Action>;

// 주행 로그(P7)에 남길 사건. 규칙을 아는 코어가 만들고, 보내는 일은 프로세스가 한다 (docs/DATA_LOGGING.md §6-2).
// Actions와 따로 둔 이유: 받기 스레드에서도 생기는데 그 스레드는 MsgSend를 하지 않는다 → 프로세스가 큐에 넣고
// UART 스레드가 비운다. 기기가 보낸 HELLO·BYE·SCR·OVR·PROF는 Actions를 실행하는 쪽이 따로 남긴다
struct LogEvent {
    std::string sender;  // 기기 ID, 모르면 "conn<N>", 전체는 "all", 줄이 깨졌으면 "uart"
    msg::CtxKind kind;
    std::string value;   // JSON, 191자 이하 (LogCtx.value에 잘리지 않고 들어감)
};
constexpr int kMaxRejectsPerSec = 10;  // REJECT 사건은 1초(HB 박자)에 이만큼만. 쓰레기 줄이 쏟아져도 로그 길이 막히지 않게

class Comm {
public:
    using LogFn = std::function<void(const std::string&)>;
    using EventFn = std::function<void(const LogEvent&)>;
    // link_timeout_ms = 0: 기기 쪽 무응답 감시를 끔 (nRF Connect로 손으로 시험할 때. 앱은 HB를 1초마다 보냄)
    explicit Comm(LogFn log = nullptr, int64_t link_timeout_ms = kLinkTimeoutMs)
        : log_(std::move(log)), link_timeout_ms_(link_timeout_ms) {}

    // 로그 사건을 받을 곳. on_*() 안에서 (프로세스의 잠금을 잡은 채) 불리므로 멈추지 않는 일만 해야 한다 (큐에 넣기)
    void set_event_sink(EventFn f) { event_ = std::move(f); }

    // ---- UART 스레드 ----
    // 시작할 때 한 번: 브리지의 모든 연결을 끊고(0~8|DROP) P5 명부도 비운다(DEV_BYE ALL).
    // P6이 다시 시작하면 표가 비어 있으므로, 기기가 다시 붙어 HELLO를 보내게 하는 것이 유일한 복구 방법이다
    Actions on_start();
    Actions on_uart_line(const std::string& line, int64_t now_ms);  // ESP32에서 온 한 줄
    Actions on_tick(int64_t now_ms);                                // 자주 (예: 100 ms마다)
    // DEV_HELLO를 P5에 보내는 데 성공하면 부른다. 못 보낸 HELLO는 1초마다 다시 나온다 (P5가 늦게 뜬 경우)
    void hello_delivered(uint8_t conn, const char* dev);
    // Ctx*·CtxResend를 P4에 보낸 결과. 실패하면 HB 박자(1초)마다 CtxResend가 나오고, 성공하면 kCtxRefreshMs마다로 돌아간다
    void p4_result(bool ok) { p4_down_ = !ok; }
    bool p4_down() const { return p4_down_; }

    // ---- 받기 스레드 (P5, P2가 보낸 메시지) ----
    Actions on_out_int(const msg::OutInt& m);
    Actions on_out_stream(const msg::OutStream& m);  // UART로 나가는 것은 없고 스트림 표만 바뀐다
    Actions on_out_prof(const msg::OutProf& m);
    Actions on_out_drop(const msg::OutDrop& m);
    // P5가 다시 시작함: 등록된 연결을 모두 'P5에 아직 못 알림'으로 되돌린다. 다음 on_tick(1초 박자)이 DEV_HELLO를
    // 다시 내보낸다 (받기 스레드는 MsgSend 하지 않으므로 표만 바꾼다)
    void on_resync();
    Actions on_motion(const msg::Motion& m);  // 스트림이 켜진 연결마다 MOT 한 줄

    bool registered(int conn) const { return valid(conn) && conn_[conn].registered; }
    bool streaming(int conn) const { return valid(conn) && conn_[conn].stream; }

private:
    struct Conn {
        bool registered = false;  // HELLO 받음
        bool stream = false;      // MOT를 보낼지
        int64_t hello_due = -1;   // -1: 기다리는 중 아님
        int64_t last_rx = 0;      // 이 연결에서 마지막으로 받은 시각
        bool delivered = false;   // DEV_HELLO가 P5에 전달됨
        uint8_t int_known = 0;    // 이 연결에 보낸 INT (bit t-1 = Tier t). DEV_HELLO에 실어 P5 재시작 때 쓴다
        uint8_t int_on = 0;
        msg::Role role = msg::Role::App;
        std::string dev;          // 연결 → 기기 ID (Ctx*의 sender를 채우려고)
        bool has_scr = false, has_prof = false;  // 마지막으로 받은 SCR·PROF (P4 재전송용)
        msg::CtxScreen scr;
        msg::CtxProf prof;
    };

    static bool valid(int conn) { return conn >= 0 && conn < msg::kMaxConn; }
    void log(const std::string& s) const {
        if (log_) log_(s);
    }
    void forget(uint8_t conn, Actions& out);
    void event(const std::string& sender, msg::CtxKind kind, const std::string& value) const {
        if (event_) event_(LogEvent{sender, kind, value});
    }
    std::string who(int conn) const;  // 등록된 연결은 기기 ID, 아니면 "conn<N>"
    void reject(int conn, const std::string& reason, const std::string& raw);
    msg::DevHello make_hello(uint8_t conn) const;
    void on_device_json(uint8_t conn, const std::string& text, Actions& out);

    Conn conn_[msg::kMaxConn];
    int64_t next_hb_ms_ = -1;
    int64_t next_refresh_ms_ = -1;
    bool p4_down_ = false;
    uint64_t hb_seq_ = 0;
    int rejects_ = 0;  // 이번 HB 박자에 남긴 REJECT 수
    LogFn log_;
    EventFn event_;
    int64_t link_timeout_ms_;
};

}  // namespace mm::comm
