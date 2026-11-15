// P6 통신의 핵심 로직: UART 줄(ESP32) ↔ QNX 메시지 번역. 표준 C++17만 사용 (QNX API 없음 → Mac에서 테스트).
// /dev/ser3를 열고 MsgSend / MsgReceive 하는 일은 QNX 프로세스 qnx/p6_comm.cpp가 한다 (아직 없음).
// 규칙: protocol/PROTOCOL.md §1·§3, docs/RF_MESSAGES.md §4.
//
// 쓰는 법: 일이 생길 때마다 on_*()를 부르면 해야 할 일 목록(Actions)이 돌아온다.
//   UartLine        → '\n'을 붙여 UART에 쓴다
//   DevHello/DevBye → P5에 MsgSend
//   Ctx*            → P4에 MsgSend
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

#include "rf_messages.hpp"

namespace mm::comm {

constexpr int64_t kHelloTimeoutMs = 5000;  // CONNECT 후 이 안에 HELLO가 없으면 DROP
constexpr int64_t kHbPeriodMs = 1000;      // ALL HB 주기
constexpr size_t kMaxFrame = 512;          // UART 한 줄 최대 (PROTOCOL.md §1)

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
using Action = std::variant<UartLine, msg::DevHello, msg::DevBye, msg::CtxScreen, msg::CtxOvr, msg::CtxProf>;
using Actions = std::vector<Action>;

class Comm {
public:
    using LogFn = std::function<void(const std::string&)>;
    explicit Comm(LogFn log = nullptr) : log_(std::move(log)) {}

    // ---- UART 스레드 ----
    Actions on_uart_line(const std::string& line, int64_t now_ms);  // ESP32에서 온 한 줄
    Actions on_tick(int64_t now_ms);                                // 자주 (예: 100 ms마다)

    // ---- 받기 스레드 (P5, P2가 보낸 메시지) ----
    Actions on_out_int(const msg::OutInt& m);
    Actions on_out_stream(const msg::OutStream& m);  // UART로 나가는 것은 없고 스트림 표만 바뀐다
    Actions on_out_prof(const msg::OutProf& m);
    Actions on_out_drop(const msg::OutDrop& m);
    Actions on_motion(const msg::Motion& m);  // 스트림이 켜진 연결마다 MOT 한 줄

    bool registered(int conn) const { return valid(conn) && conn_[conn].registered; }
    bool streaming(int conn) const { return valid(conn) && conn_[conn].stream; }

private:
    struct Conn {
        bool registered = false;  // HELLO 받음
        bool stream = false;      // MOT를 보낼지
        int64_t hello_due = -1;   // -1: 기다리는 중 아님
        msg::Role role = msg::Role::App;
        std::string dev;          // 연결 → 기기 ID (Ctx*의 sender를 채우려고)
    };

    static bool valid(int conn) { return conn >= 0 && conn < msg::kMaxConn; }
    void log(const std::string& s) const {
        if (log_) log_(s);
    }
    void forget(uint8_t conn, Actions& out);
    void on_device_json(uint8_t conn, const std::string& text, Actions& out);

    Conn conn_[msg::kMaxConn];
    int64_t next_hb_ms_ = -1;
    uint64_t hb_seq_ = 0;
    LogFn log_;
};

}  // namespace mm::comm
