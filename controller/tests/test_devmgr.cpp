// P5 기기 관리 코어 테스트: 등록, 명령 → 연결 번호, 재연결 재전송, 프로필 회신, DEV_SYNC 회신, 30초 안전 OFF
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

#include "devmgr.hpp"

using namespace mm;
using namespace mm::devmgr;

static int failures = 0;
#define CHECK(cond, ...)                                     \
    do {                                                     \
        if (!(cond)) {                                       \
            failures++;                                      \
            std::printf("FAIL %s:%d: ", __FILE__, __LINE__); \
            std::printf(__VA_ARGS__);                        \
            std::printf("\n");                               \
        }                                                    \
    } while (0)

static const std::string kA = "phone-a";
static const std::string kB = "phone-b";

static msg::DevHello hello(int conn, const std::string& dev, msg::Role role = msg::Role::App) {
    msg::DevHello m;
    m.conn = static_cast<uint8_t>(conn);
    m.role = role;
    m.ver = 1;
    msg::copy_id(m.dev, dev);
    return m;
}
static msg::TierCmd cmd(const std::string& device, int tier, bool on) {
    msg::TierCmd m;
    msg::copy_id(m.device, device);
    m.tier = static_cast<uint8_t>(tier);
    m.on = on;
    return m;
}
static msg::DevBye bye(int conn) {
    msg::DevBye m;
    m.conn = static_cast<uint8_t>(conn);
    return m;
}

// Outs를 읽기 쉬운 글자로: "INT c t on", "STR c on", "PROF c", "DROP c"
static std::vector<std::string> show(const Outs& outs) {
    std::vector<std::string> v;
    for (const auto& o : outs) {
        char b[64];
        if (const auto* i = std::get_if<msg::OutInt>(&o))
            std::snprintf(b, sizeof b, "INT %d %d %d", i->conn, i->tier, i->on);
        else if (const auto* s = std::get_if<msg::OutStream>(&o))
            std::snprintf(b, sizeof b, "STR %d %d", s->conn, s->on);
        else if (const auto* p = std::get_if<msg::OutProf>(&o))
            std::snprintf(b, sizeof b, "PROF %d %.2f %.2f", p->conn, p->mssq, p->sens);
        else if (const auto* d = std::get_if<msg::OutDrop>(&o))
            std::snprintf(b, sizeof b, "DROP %d", d->conn);
        v.push_back(b);
    }
    return v;
}
static std::string join(const std::vector<std::string>& v) {
    std::string s;
    for (const auto& x : v) s += (s.empty() ? "" : ", ") + x;
    return s;
}
#define EXPECT(outs, ...)                                                                           \
    do {                                                                                            \
        const std::vector<std::string> want_ = {__VA_ARGS__};                                       \
        const std::vector<std::string> got_ = show(outs);                                           \
        CHECK(got_ == want_, "got [%s] want [%s]", join(got_).c_str(), join(want_).c_str());       \
    } while (0)

// 프로필 저장소 흉내: phone-a만 저장돼 있다
static bool fake_profile(const std::string& id, Profile& p) {
    if (id != kA) return false;
    p = Profile{20.0, 1.3, 1.5};
    return true;
}

static void hello_sends_state_and_profile() {
    DevMgr m(fake_profile);
    EXPECT(m.on_hello(hello(0, kA)), "INT 0 1 0", "INT 0 2 0", "INT 0 3 0", "STR 0 0", "PROF 0 20.00 1.30");
    EXPECT(m.on_hello(hello(1, kB)), "INT 1 1 0", "INT 1 2 0", "INT 1 3 0", "STR 1 0");  // 저장된 프로필 없음
    EXPECT(m.on_hello(hello(2, "hud-01", msg::Role::Hud)), "INT 2 1 0", "INT 2 2 0", "STR 2 0");
    CHECK(m.conn_of(kA) == 0 && m.conn_of(kB) == 1 && m.conn_of("hud-01") == 2, "registry");
}

static void tier_commands() {
    DevMgr m(fake_profile);
    m.on_hello(hello(0, kA));
    m.on_hello(hello(1, kB));
    m.on_hello(hello(2, "hud-01", msg::Role::Hud));

    // Tier 1: 모두에게 INT, 스트림 없음
    EXPECT(m.on_tier_cmd(cmd("airflow-01", 1, true)), "INT 0 1 1", "INT 1 1 1", "INT 2 1 1");
    // Tier 2: 모두에게 INT, HUD만 스트림 (빛 방향)
    EXPECT(m.on_tier_cmd(cmd("display-01", 2, true)), "INT 0 2 1", "INT 1 2 1", "INT 2 2 1", "STR 2 1");
    // Tier 3: 그 폰에만 INT + 스트림
    EXPECT(m.on_tier_cmd(cmd(kB, 3, true)), "INT 1 3 1", "STR 1 1");
    EXPECT(m.on_tier_cmd(cmd(kB, 3, false)), "INT 1 3 0", "STR 1 0");
    CHECK(m.is_on("airflow-01") && m.is_on("display-01") && !m.is_on(kB), "state kept");

    // 잘못된 명령
    CHECK(m.on_tier_cmd(cmd("airflow-01", 2, true)).empty(), "tier does not match device");
    CHECK(m.on_tier_cmd(cmd("hud-01", 1, true)).empty(), "hud-01 is not an output device");
    CHECK(m.on_tier_cmd(cmd("", 3, true)).empty(), "empty device");
}

static void reconnect_resends_state() {
    DevMgr m(fake_profile);
    m.on_hello(hello(0, kA));
    m.on_tier_cmd(cmd("display-01", 2, true));
    m.on_tier_cmd(cmd(kA, 3, true));

    m.on_bye(bye(0));
    CHECK(m.conn_of(kA) == -1, "removed on bye");
    // 끊겨 있는 동안 온 명령은 기억만
    CHECK(m.on_tier_cmd(cmd("airflow-01", 1, true)).empty(), "nobody connected");

    // 다른 번호로 다시 붙으면 지금 상태 전부 + 프로필
    EXPECT(m.on_hello(hello(4, kA)), "INT 4 1 1", "INT 4 2 1", "INT 4 3 1", "STR 4 1", "PROF 4 20.00 1.30");

    // DISCONNECT 없이 또 다른 번호로: 옛 줄은 지워진다
    m.on_hello(hello(5, kA));
    CHECK(m.conn_of(kA) == 5, "old entry replaced");
    msg::DevSyncReply r = m.on_sync(msg::DevSync{}, 0);
    CHECK(r.n == 1, "only one entry for the same phone: %u", r.n);

    // HUD가 다시 붙으면 앰비언트 스트림도 다시 켠다
    EXPECT(m.on_hello(hello(6, "hud-01", msg::Role::Hud)), "INT 6 1 1", "INT 6 2 1", "STR 6 1");
}

static void sync_reply() {
    DevMgr m(nullptr);
    m.on_hello(hello(3, kA));
    m.on_hello(hello(7, "hud-01", msg::Role::Hud));
    msg::DevSyncReply r = m.on_sync(msg::DevSync{}, 1000);
    CHECK(r.n == 2, "two entries: %u", r.n);
    CHECK(r.n == 2 && kA == r.e[0].dev && r.e[0].role == msg::Role::App && std::string(r.e[1].dev) == "hud-01" &&
              r.e[1].role == msg::Role::Hud,
          "entries in conn order");
}

static void safety_off() {
    DevMgr m(nullptr);
    CHECK(m.on_tick(0).empty(), "clock starts");
    m.on_hello(hello(0, kA));
    m.on_hello(hello(1, "hud-01", msg::Role::Hud));
    m.on_sync(msg::DevSync{}, 1000);
    m.on_tier_cmd(cmd("airflow-01", 1, true));
    m.on_tier_cmd(cmd("display-01", 2, true));
    m.on_tier_cmd(cmd(kA, 3, true));

    CHECK(m.on_tick(30999).empty() && !m.safe_off(), "29.999 s without DEV_SYNC is fine");
    EXPECT(m.on_tick(31000), "INT 0 1 0", "INT 0 2 0", "INT 0 3 0", "STR 0 0", "INT 1 1 0", "INT 1 2 0", "STR 1 0");
    CHECK(m.safe_off() && !m.is_on("airflow-01") && !m.is_on(kA), "everything off");
    CHECK(m.on_tick(40000).empty(), "safe off only once");
    CHECK(m.on_tier_cmd(cmd(kA, 3, true)).empty() && !m.is_on(kA), "ON refused while safe off");
    CHECK(!m.on_tier_cmd(cmd(kA, 3, false)).empty(), "OFF still passes");

    m.on_sync(msg::DevSync{}, 41000);
    CHECK(!m.safe_off(), "DEV_SYNC clears safe off");
    EXPECT(m.on_tier_cmd(cmd(kA, 3, true)), "INT 0 3 1", "STR 0 1");
}

static void startup_grace() {
    DevMgr m(nullptr);
    m.on_tick(5000);  // 프로세스 시작
    CHECK(m.on_tick(34999).empty(), "30 s grace from start");
    CHECK(m.on_tick(35000).empty() && m.safe_off(), "nobody connected: safe off with nothing to send");
}

int main() {
    hello_sends_state_and_profile();
    tier_commands();
    reconnect_resends_state();
    sync_reply();
    safety_off();
    startup_grace();
    if (failures) {
        std::printf("%d failure(s)\n", failures);
        return 1;
    }
    std::printf("devmgr: all passed\n");
    return 0;
}
