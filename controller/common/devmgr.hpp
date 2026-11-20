// P5 기기 관리의 핵심 로직: 등록 명부, P4 명령 → 연결 번호, 재연결 시 상태 재전송, 30초 안전 OFF.
// 표준 C++17만 사용 (QNX API 없음 → Mac에서 테스트). 프로세스는 qnx/p5_devmgr.cpp.
// 규칙: docs/RF_MESSAGES.md §4.
//
// P4는 기기를 ID(airflow-01, display-01, 폰 UUID)로, 브리지는 연결 번호(0~8)로 구분한다.
// 이 클래스가 둘을 잇는다. 돌려주는 Outs는 P6에 보낼 메시지 목록이다 (보내는 일은 프로세스가).
#pragma once
#include <cstdint>
#include <functional>
#include <map>
#include <string>
#include <variant>
#include <vector>

#include "log_messages.hpp"
#include "policy.hpp"
#include "rf_messages.hpp"

namespace mm::devmgr {

constexpr int64_t kSafetyTimeoutMs = 30000;  // policy.json safety_timeout_s

using Out = std::variant<msg::OutInt, msg::OutStream, msg::OutProf, msg::OutDrop, msg::OutResync>;
using Outs = std::vector<Out>;

// 주행 로그(P7)에 남길 사건: 안전 OFF 진입(SAFE_OFF)·해제(SAFE_CLEAR), 잡아 둔 ON 적용(HELD_ON).
// 보내는 일은 프로세스가 한다 (docs/DATA_LOGGING.md §6-2). ctx 표의 sender는 "p5"
struct LogEvent {
    msg::CtxKind kind;
    std::string value;  // JSON
};

class DevMgr {
public:
    using LogFn = std::function<void(const std::string&)>;
    using EventFn = std::function<void(const LogEvent&)>;
    // 저장된 프로필 읽기 (P4가 쓴 state/profiles/<ID>.json). 없으면 false. P5는 읽기만 한다
    using ProfileFn = std::function<bool(const std::string& phone, Profile& out)>;

    DevMgr(ProfileFn load_profile, LogFn log = nullptr, int64_t safety_ms = kSafetyTimeoutMs)
        : load_profile_(std::move(load_profile)), log_(std::move(log)), safety_ms_(safety_ms) {}

    // 로그 사건을 받을 곳. on_*() 안에서 불린다 (멈추지 않는 일만: 목록에 넣기)
    void set_event_sink(EventFn f) { event_ = std::move(f); }

    Outs on_hello(const msg::DevHello& m);    // P6: 기기 등록
    Outs on_bye(const msg::DevBye& m);        // P6: 기기 해제
    Outs on_tier_cmd(const msg::TierCmd& m);  // P4: 기기 하나 ON/OFF
    msg::DevSyncReply on_sync(const msg::DevSync& m, int64_t now_ms);  // P4: 10초마다, 회신 = 명부
    // 1초마다: P6에 OUT_RESYNC가 전달될 때까지 다시 보냄 + DEV_SYNC가 30초 없으면 전부 OFF
    Outs on_tick(int64_t now_ms);
    // OUT_RESYNC를 P6에 보내는 데 성공하면 부른다. 그 뒤로는 on_tick이 OUT_RESYNC를 내지 않는다
    void resync_delivered() { resync_done_ = true; }

    // ---- 조회 (테스트·로그) ----
    int conn_of(const std::string& dev) const;  // 없으면 -1
    bool is_on(const std::string& device) const;
    bool safe_off() const { return safe_off_; }

private:
    struct Entry {
        bool used = false;
        std::string dev;
        msg::Role role = msg::Role::App;
    };

    void log(const std::string& s) const {
        if (log_) log_(s);
    }
    void event(msg::CtxKind kind, const std::string& value) const {
        if (event_) event_(LogEvent{kind, value});
    }
    void send_state(uint8_t conn, Outs& out) const;  // 그 연결에 지금 상태 전부
    static void push_int(Outs& out, uint8_t conn, int tier, bool on);
    static void push_stream(Outs& out, uint8_t conn, bool on);

    Entry e_[msg::kMaxConn];
    bool airflow_ = false;               // Tier 1 (공용)
    bool display_ = false;               // Tier 2 (공용)
    // Tier 1·2를 아는지. P5가 시작하면 모른다 → 첫 DEV_HELLO에 실린 'P6이 마지막으로 보낸 INT'로 되찾는다
    // (P5만 다시 시작했을 때 P4는 바뀐 것이 없으면 명령을 다시 보내지 않으므로). 모르는 채면 OFF로 보낸다
    bool airflow_known_ = false;
    bool display_known_ = false;
    bool resync_done_ = false;           // OUT_RESYNC가 P6에 전달됨
    std::map<std::string, bool> dots_;   // Tier 3, 폰 ID별. 끊겨도 기억 (재연결 때 다시 보냄). 없으면 모름
    int64_t last_sync_ = -1;             // -1: 아직 시계 시작 전
    bool safe_off_ = false;
    std::map<std::string, msg::TierCmd> held_;  // 안전 OFF 중에 온 ON 명령 (기기별 마지막). 풀리면 적용
    ProfileFn load_profile_;
    LogFn log_;
    EventFn event_;
    int64_t safety_ms_;
};

}  // namespace mm::devmgr
