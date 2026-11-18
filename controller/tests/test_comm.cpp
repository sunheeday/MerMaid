// P6 통신 코어 테스트: 줄 조립, 프레임, HELLO 5초, HB 1초, 기기 메시지 → P4/P5 메시지, P5/P2 메시지 → UART 줄,
// 시작 DROP, P5 재시작(OUT_RESYNC → HELLO 재전송, INT 기록)
#include <cmath>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

#include "comm.hpp"

using namespace mm;
using namespace mm::comm;

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

static std::vector<std::string> lines(const Actions& a) {
    std::vector<std::string> v;
    for (const auto& x : a)
        if (const auto* u = std::get_if<UartLine>(&x)) v.push_back(u->text);
    return v;
}
static bool has_line(const Actions& a, const std::string& s) {
    for (const auto& l : lines(a))
        if (l == s) return true;
    return false;
}
template <class T>
static const T* only(const Actions& a) {
    return a.size() == 1 ? std::get_if<T>(&a[0]) : nullptr;
}

static const std::string kPhone = "5f3a9c1e-1111-4222-8333-444455556666";
static std::string hello(const std::string& role, const std::string& dev, int v = 1) {
    return "{\"t\":\"HELLO\",\"role\":\"" + role + "\",\"dev\":\"" + dev + "\",\"v\":" + std::to_string(v) + "}";
}

static void line_assembler() {
    LineAssembler la;
    std::vector<std::string> got;
    auto cb = [&](const std::string& s) { got.push_back(s); };
    const std::string a = "0|CONN", b = "ECT\r\n1|{\"t\":\"HB\",\"seq\":1}\n\n2|DIS";
    la.feed(a.data(), a.size(), cb);
    CHECK(got.empty(), "no line before newline");
    la.feed(b.data(), b.size(), cb);
    CHECK(got.size() == 2 && got[0] == "0|CONNECT" && got[1] == "1|{\"t\":\"HB\",\"seq\":1}", "pieces joined, CR dropped");

    got.clear();
    std::string big(600, 'x');
    big += "\n3|CONNECT\n";
    la.feed(big.data(), big.size(), cb);
    CHECK(got.size() == 1 && got[0] == "3|CONNECT", "line over 512 B dropped, next line kept");
}

static void frames() {
    Frame f;
    CHECK(parse_frame("0|CONNECT", f) && f.conn == 0 && f.payload == "CONNECT", "0|CONNECT");
    CHECK(parse_frame("8|{\"a\":\"x|y\"}", f) && f.conn == 8 && f.payload == "{\"a\":\"x|y\"}", "bar inside payload");
    CHECK(!parse_frame("9|CONNECT", f), "conn 9 refused");
    CHECK(!parse_frame("ALL|x", f), "ALL is Pi -> ESP32 only");
    CHECK(!parse_frame("0|", f) && !parse_frame("", f) && !parse_frame("[STAT] up 3s", f), "junk refused");
}

static void hello_timeout() {
    Comm c(nullptr, 0);  // 기기 무응답 감시 끔
    c.on_tick(0);  // 첫 HB
    CHECK(c.on_uart_line("0|CONNECT", 1000).empty(), "CONNECT alone sends nothing");
    CHECK(!has_line(c.on_tick(5999), "0|DROP"), "no DROP before 5 s");
    CHECK(has_line(c.on_tick(6000), "0|DROP"), "DROP at 5 s");
    CHECK(!has_line(c.on_tick(6100), "0|DROP"), "DROP only once");
    CHECK(c.on_uart_line("0|DISCONNECT", 6200).empty(), "unregistered DISCONNECT: nothing for P5");

    c.on_uart_line("1|CONNECT", 7000);
    Actions a = c.on_uart_line("1|" + hello("app", kPhone), 8000);
    const auto* h = only<msg::DevHello>(a);
    CHECK(h && h->h.type == msg::DEV_HELLO && h->conn == 1 && h->role == msg::Role::App && h->ver == 1 &&
              kPhone == h->dev,
          "HELLO -> DEV_HELLO");
    CHECK(c.registered(1) && !has_line(c.on_tick(20000), "1|DROP"), "registered, no DROP");

    a = c.on_uart_line("1|DISCONNECT", 21000);
    const auto* bye = only<msg::DevBye>(a);
    CHECK(bye && bye->h.type == msg::DEV_BYE && bye->conn == 1 && !c.registered(1), "DISCONNECT -> DEV_BYE");

    // 잘못된 HELLO는 등록하지 않는다 → 5초 뒤 DROP
    c.on_uart_line("2|CONNECT", 30000);
    CHECK(c.on_uart_line("2|" + hello("car", "x"), 30001).empty(), "bad role");
    CHECK(c.on_uart_line("2|" + hello("app", kPhone, 2), 30002).empty(), "v must be 1");
    CHECK(c.on_uart_line("2|" + hello("app", "../evil"), 30003).empty(), "unsafe file name");
    CHECK(c.on_uart_line("2|" + hello("app", std::string(64, 'a')), 30004).empty(), "id too long for kIdLen");
    CHECK(c.on_uart_line("2|" + hello("app", "display-01"), 30005).empty(), "phone cannot take a shared ID");
    CHECK(c.on_uart_line("2|{\"t\":\"HELLO\",\"role\":\"app\",\"dev\":\"abc\"}", 30006).empty(), "missing v");
    CHECK(!c.registered(2) && has_line(c.on_tick(35000), "2|DROP"), "bad HELLO ends in DROP");
}

static void hud_id() {
    Comm c(nullptr, 0);  // 기기 무응답 감시 끔
    c.on_uart_line("3|CONNECT", 0);
    Actions a = c.on_uart_line("3|" + hello("hud", "some-browser-uuid"), 10);
    const auto* h = only<msg::DevHello>(a);
    CHECK(h && h->role == msg::Role::Hud && std::string(h->dev) == "hud-01", "HUD is always hud-01");
}

static void reconnect_same_conn() {
    Comm c(nullptr, 0);  // 기기 무응답 감시 끔
    c.on_uart_line("0|CONNECT", 0);
    c.on_uart_line("0|" + hello("app", kPhone), 1);
    msg::OutStream s;
    s.conn = 0;
    s.on = 1;
    c.on_out_stream(s);
    Actions a = c.on_uart_line("0|CONNECT", 1000);
    CHECK(only<msg::DevBye>(a) && !c.registered(0) && !c.streaming(0), "CONNECT without DISCONNECT clears old state");
}

static void device_messages() {
    Comm c(nullptr, 0);  // 기기 무응답 감시 끔
    c.on_uart_line("0|CONNECT", 0);
    CHECK(c.on_uart_line("0|{\"t\":\"SCR\",\"on\":true,\"att\":true}", 1).empty(), "dropped before HELLO");
    c.on_uart_line("0|" + hello("app", kPhone), 2);
    c.on_uart_line("1|CONNECT", 2);
    c.on_uart_line("1|" + hello("hud", "hud-01"), 3);

    // SCR
    Actions a = c.on_uart_line("0|{\"t\":\"SCR\",\"on\":true,\"att\":true}", 10);
    const auto* scr = only<msg::CtxScreen>(a);
    CHECK(scr && scr->h.type == msg::CTX_SCREEN && kPhone == scr->sender && scr->on == 1 && scr->att == 1, "phone SCR");
    a = c.on_uart_line("0|{\"t\":\"SCR\",\"on\":false,\"att\":true}", 11);
    scr = only<msg::CtxScreen>(a);
    CHECK(scr && scr->on == 0 && scr->att == 0, "screen off means no attention");
    a = c.on_uart_line("1|{\"t\":\"SCR\",\"on\":true,\"att\":true}", 12);
    scr = only<msg::CtxScreen>(a);
    CHECK(scr && std::string(scr->sender) == "hud-01" && scr->on == 1 && scr->att == 0, "HUD SCR, att always 0");
    CHECK(c.on_uart_line("0|{\"t\":\"SCR\",\"on\":true}", 13).empty(), "att is required");
    CHECK(c.on_uart_line("0|{\"t\":\"SCR\",\"on\":1,\"att\":0}", 14).empty(), "booleans must be true/false");

    // OVR
    a = c.on_uart_line("0|{\"t\":\"OVR\",\"dev\":\"" + kPhone + "\",\"on\":false}", 20);
    const auto* ovr = only<msg::CtxOvr>(a);
    CHECK(ovr && ovr->h.type == msg::CTX_OVR && kPhone == ovr->sender && kPhone == ovr->device && ovr->on == 0,
          "phone turns off its own dots");
    a = c.on_uart_line("0|{\"t\":\"OVR\",\"dev\":\"airflow-01\",\"on\":true}", 21);
    ovr = only<msg::CtxOvr>(a);
    CHECK(ovr && std::string(ovr->device) == "airflow-01" && ovr->on == 1, "phone turns on airflow");
    a = c.on_uart_line("1|{\"t\":\"OVR\",\"dev\":\"display-01\",\"on\":false}", 22);
    ovr = only<msg::CtxOvr>(a);
    CHECK(ovr && std::string(ovr->sender) == "hud-01" && std::string(ovr->device) == "display-01", "HUD button");
    CHECK(c.on_uart_line("1|{\"t\":\"OVR\",\"dev\":\"" + kPhone + "\",\"on\":true}", 23).empty(),
          "HUD cannot switch a phone's dots");
    CHECK(c.on_uart_line("0|{\"t\":\"OVR\",\"dev\":\"other-phone\",\"on\":true}", 24).empty(),
          "phone cannot switch another phone");

    // PROF
    a = c.on_uart_line("0|{\"t\":\"PROF\",\"mssq\":12.5,\"sens\":1.2}", 30);
    const auto* prof = only<msg::CtxProf>(a);
    CHECK(prof && prof->h.type == msg::CTX_PROF && kPhone == prof->phone && prof->mssq == 12.5 && prof->sens == 1.2,
          "PROF");
    CHECK(c.on_uart_line("0|{\"t\":\"PROF\",\"mssq\":60,\"sens\":1.2}", 31).empty(), "mssq 0~54");
    CHECK(c.on_uart_line("0|{\"t\":\"PROF\",\"mssq\":10,\"sens\":3}", 32).empty(), "sens 0.5~2.0");
    CHECK(c.on_uart_line("0|{\"t\":\"PROF\",\"mssq\":10}", 33).empty(), "sens required");
    CHECK(c.on_uart_line("1|{\"t\":\"PROF\",\"mssq\":10,\"sens\":1}", 34).empty(), "HUD has no profile");

    // 나머지
    CHECK(c.on_uart_line("0|{\"t\":\"HB\",\"seq\":5}", 40).empty(), "HB ends in P6");
    CHECK(c.on_uart_line("0|{\"t\":\"MISC\",\"v\":3}", 41).empty(), "M04 removed");
    CHECK(c.on_uart_line("0|{\"t\":\"SCR\",\"on\":tru", 42).empty(), "broken JSON");
    CHECK(c.on_uart_line("0|[1,2]", 43).empty(), "not an object");
    a = c.on_uart_line("0|{\"x\":{\"a\":[1,\"}\"]},\"t\":\"SCR\",\"on\":true,\"att\":false}", 44);
    CHECK(only<msg::CtxScreen>(a) != nullptr, "unknown fields ignored");
}

static void heartbeat() {
    Comm c;
    auto a = lines(c.on_tick(0));
    CHECK(a.size() == 1 && a[0] == "ALL|{\"t\":\"HB\",\"seq\":0}", "first HB");
    CHECK(lines(c.on_tick(500)).empty(), "none at 0.5 s");
    a = lines(c.on_tick(1040));
    CHECK(a.size() == 1 && a[0] == "ALL|{\"t\":\"HB\",\"seq\":1}", "HB at 1 s");
    a = lines(c.on_tick(2000));
    CHECK(a.size() == 1 && a[0] == "ALL|{\"t\":\"HB\",\"seq\":2}", "keeps the beat after a late tick");
    CHECK(lines(c.on_tick(10000)).size() == 1, "no burst after a long pause");
    CHECK(lines(c.on_tick(10500)).empty(), "restart beat from the pause");
}

static void outgoing() {
    Comm c(nullptr, 0);  // 기기 무응답 감시 끔
    msg::OutInt i;
    i.conn = 0;
    i.tier = 3;
    i.on = 1;
    auto a = lines(c.on_out_int(i));
    CHECK(a.size() == 1 && a[0] == "0|{\"t\":\"INT\",\"tier\":3,\"on\":true}", "INT");
    i.conn = msg::kConnAll;
    i.tier = 1;
    i.on = 0;
    a = lines(c.on_out_int(i));
    CHECK(a.size() == 1 && a[0] == "ALL|{\"t\":\"INT\",\"tier\":1,\"on\":false}", "INT ALL");
    i.tier = 4;
    CHECK(c.on_out_int(i).empty(), "tier 4 refused");
    i.tier = 1;
    i.conn = 9;
    CHECK(c.on_out_int(i).empty(), "conn 9 refused");

    msg::OutProf p;
    p.conn = 2;
    p.mssq = 12.9;
    p.sens = 1.15;
    a = lines(c.on_out_prof(p));
    CHECK(a.size() == 1 && a[0] == "2|{\"t\":\"PROF\",\"mssq\":12.90,\"sens\":1.150}", "PROF: %s",
          a.empty() ? "" : a[0].c_str());

    msg::OutDrop d;
    d.conn = 4;
    a = lines(c.on_out_drop(d));
    CHECK(a.size() == 1 && a[0] == "4|DROP", "DROP");
}

static void motion_stream() {
    Comm c(nullptr, 0);  // 기기 무응답 감시 끔
    c.on_uart_line("0|CONNECT", 0);
    c.on_uart_line("0|" + hello("app", kPhone), 1);
    c.on_uart_line("2|CONNECT", 0);
    c.on_uart_line("2|" + hello("hud", "hud-01"), 1);

    msg::Motion m;
    m.ax = 0.12;
    m.ay = -0.31;
    m.pax = m.pay = std::nan("");
    CHECK(c.on_motion(m).empty(), "no stream, no MOT");

    msg::OutStream s;
    s.conn = 0;
    s.on = 1;
    CHECK(c.on_out_stream(s).empty(), "OUT_STREAM writes nothing");
    auto a = lines(c.on_motion(m));
    CHECK(a.size() == 1 && a[0] == "0|{\"t\":\"MOT\",\"ax\":0.120,\"ay\":-0.310}", "MOT without preview");

    s.conn = 2;
    c.on_out_stream(s);
    m.pax = 0.5;
    m.pay = 0.0;
    a = lines(c.on_motion(m));
    CHECK(a.size() == 2 && a[0] == "0|{\"t\":\"MOT\",\"ax\":0.120,\"ay\":-0.310,\"pax\":0.500,\"pay\":0.000}" &&
              a[1].rfind("2|", 0) == 0,
          "MOT with preview to both");

    msg::Motion bad = m;
    bad.ax = std::nan("");
    CHECK(c.on_motion(bad).empty(), "NaN acceleration not sent");

    c.on_uart_line("0|DISCONNECT", 100);
    a = lines(c.on_motion(m));
    CHECK(a.size() == 1 && a[0].rfind("2|", 0) == 0, "stream stops on disconnect");
    s.conn = 0;
    s.on = 1;
    c.on_out_stream(s);
    CHECK(!c.streaming(0), "late OUT_STREAM cannot open an empty conn");

    s.conn = msg::kConnAll;
    s.on = 0;
    c.on_out_stream(s);
    CHECK(c.on_motion(m).empty(), "ALL off");
}


static void link_timeout() {
    Comm c;  // 감시 켬 (5초)
    c.on_uart_line("0|CONNECT", 0);
    c.on_uart_line("0|" + hello("app", kPhone), 100);
    c.on_uart_line("0|{\"t\":\"HB\",\"seq\":1}", 3000);
    CHECK(!has_line(c.on_tick(7999), "0|DROP"), "HB at 3 s keeps the link until 8 s");
    Actions a = c.on_tick(8000);
    bool bye = false;
    for (const auto& x : a)
        if (const auto* b = std::get_if<msg::DevBye>(&x)) bye = b->conn == 0;
    CHECK(has_line(a, "0|DROP") && bye && !c.registered(0), "silent 5 s: DROP and DEV_BYE");
    CHECK(c.on_uart_line("0|DISCONNECT", 8100).empty(), "later DISCONNECT is a no-op");
}

static void hello_resend() {
    Comm c(nullptr, 0);
    c.on_tick(0);
    c.on_uart_line("0|CONNECT", 10);
    c.on_uart_line("0|" + hello("app", kPhone), 20);  // P5가 아직 없어서 못 보냈다고 치자
    auto count_hello = [](const Actions& a) {
        int n = 0;
        for (const auto& x : a) n += std::holds_alternative<msg::DevHello>(x);
        return n;
    };
    CHECK(count_hello(c.on_tick(500)) == 0, "no resend between beats");
    CHECK(count_hello(c.on_tick(1000)) == 1, "resend with the next HB");
    c.hello_delivered(0, "someone-else");
    CHECK(count_hello(c.on_tick(2000)) == 1, "delivery of another dev does not count");
    c.hello_delivered(0, kPhone.c_str());
    CHECK(count_hello(c.on_tick(3000)) == 0, "no resend after delivery");
}

// P6 시작: 브리지의 모든 연결을 끊고 P5 명부도 비운다
static void start_drops_all() {
    Comm c(nullptr, 0);
    const Actions a = c.on_start();
    const std::vector<std::string> l = lines(a);
    CHECK(l.size() == 9 && l[0] == "0|DROP" && l[8] == "8|DROP", "0~8|DROP");
    CHECK(std::holds_alternative<msg::DevBye>(a.back()) && std::get<msg::DevBye>(a.back()).conn == msg::kConnAll,
          "then DEV_BYE ALL");
}

// P5 재시작: OUT_RESYNC → 다음 박자에 등록된 연결마다 DEV_HELLO, 보낸 INT 기록을 싣는다
static void resync() {
    Comm c(nullptr, 0);
    c.on_tick(0);
    c.on_uart_line("0|CONNECT", 10);
    c.on_uart_line("0|" + hello("app", kPhone), 20);
    c.on_uart_line("1|CONNECT", 30);
    c.on_uart_line("1|" + hello("hud", "hud-01"), 40);
    c.on_uart_line("2|CONNECT", 50);  // HELLO 전: 다시 알릴 것 없음
    c.hello_delivered(0, kPhone.c_str());
    c.hello_delivered(1, "hud-01");
    auto hellos = [](const Actions& a) {
        std::vector<msg::DevHello> v;
        for (const auto& x : a)
            if (const auto* h = std::get_if<msg::DevHello>(&x)) v.push_back(*h);
        return v;
    };
    const Actions first = c.on_uart_line("3|CONNECT", 60);
    CHECK(hellos(first).empty(), "nothing yet");

    // 새 HELLO의 기록은 비어 있다
    c.on_uart_line("4|CONNECT", 70);
    std::vector<msg::DevHello> h = hellos(c.on_uart_line("4|" + hello("app", "phone-z"), 80));
    CHECK(h.size() == 1 && h[0].int_known == 0 && h[0].int_on == 0, "fresh HELLO has no INT record");
    c.hello_delivered(4, "phone-z");

    msg::OutInt i;
    i.conn = 0, i.tier = 1, i.on = 1;
    c.on_out_int(i);
    i.tier = 3;
    c.on_out_int(i);
    i.conn = msg::kConnAll, i.tier = 2, i.on = 0;
    c.on_out_int(i);
    i.conn = 1, i.tier = 1, i.on = 1;
    c.on_out_int(i);
    i.conn = 1, i.on = 0;
    c.on_out_int(i);
    i.conn = 2, i.tier = 1, i.on = 1;  // 등록 안 된 연결: 기록하지 않음
    c.on_out_int(i);

    CHECK(hellos(c.on_tick(1000)).empty(), "no HELLO before RESYNC");
    c.on_resync();
    h = hellos(c.on_tick(1500));
    CHECK(h.empty(), "HELLO waits for the next beat");
    h = hellos(c.on_tick(2000));
    CHECK(h.size() == 3, "HELLO for each registered conn: %zu", h.size());
    if (h.size() == 3) {
        CHECK(h[0].conn == 0 && kPhone == h[0].dev && h[0].int_known == 0b111 && h[0].int_on == 0b101,
              "phone: Tier 1 on, 2 off, 3 on (%d %d)", h[0].int_known, h[0].int_on);
        CHECK(h[1].conn == 1 && h[1].role == msg::Role::Hud && h[1].int_known == 0b011 && h[1].int_on == 0,
              "HUD: Tier 1 off (last), 2 off");
        CHECK(h[2].conn == 4 && h[2].int_known == 0b010 && h[2].int_on == 0, "phone-z: Tier 2 off from ALL");
    }
    c.hello_delivered(0, kPhone.c_str());
    c.hello_delivered(1, "hud-01");
    c.hello_delivered(4, "phone-z");
    CHECK(hellos(c.on_tick(3000)).empty(), "no more HELLO after delivery");

    // 다시 CONNECT 하면 기록도 지운다 (다른 기기일 수 있으므로)
    c.on_uart_line("0|CONNECT", 3100);
    h = hellos(c.on_uart_line("0|" + hello("app", kPhone), 3200));
    CHECK(h.size() == 1 && h[0].int_known == 0, "record cleared on CONNECT");
}

static void ctx_resend() {
    Comm c(nullptr, 0);
    c.on_tick(0);
    c.on_uart_line("0|CONNECT", 10);
    c.on_uart_line("0|" + hello("app", kPhone), 20);
    c.on_uart_line("1|CONNECT", 30);
    c.on_uart_line("1|" + hello("hud", "hud-01"), 40);
    c.on_uart_line("0|{\"t\":\"SCR\",\"on\":true,\"att\":true}", 50);
    c.on_uart_line("0|{\"t\":\"PROF\",\"mssq\":30.0,\"sens\":1.6}", 60);
    c.on_uart_line("1|{\"t\":\"SCR\",\"on\":true,\"att\":false}", 70);
    c.on_uart_line("0|{\"t\":\"OVR\",\"dev\":\"" + kPhone + "\",\"on\":false}", 80);
    auto resends = [](const Actions& a) {
        std::vector<CtxResend> v;
        for (const auto& x : a)
            if (const auto* r = std::get_if<CtxResend>(&x)) v.push_back(*r);
        return v;
    };
    c.p4_result(false);  // P4가 아직 없어서 SCR·PROF를 못 보냈다
    CHECK(resends(c.on_tick(500)).empty(), "no resend between beats");
    auto r = resends(c.on_tick(1000));
    CHECK(r.size() == 2, "resend for phone and HUD with the next HB (%zu)", r.size());
    if (r.size() == 2) {
        CHECK(r[0].has_scr && r[0].has_prof && std::string(r[0].scr.sender) == kPhone && r[0].scr.on && r[0].scr.att,
              "phone: last SCR");
        CHECK(std::string(r[0].prof.phone) == kPhone && r[0].prof.mssq == 30.0 && std::fabs(r[0].prof.sens - 1.6) < 1e-9,
              "phone: last PROF");
        CHECK(r[1].has_scr && !r[1].has_prof && !r[1].scr.att, "HUD: SCR only");
    }
    CHECK(resends(c.on_tick(2000)).size() == 2, "again each second while P4 is down");
    c.p4_result(true);
    bool quiet = true;
    for (int t = 3000; t < 12000; t += 1000) quiet = quiet && resends(c.on_tick(t)).empty();
    CHECK(quiet, "no resend for 10 s after P4 answers");
    CHECK(resends(c.on_tick(12000)).size() == 2, "refresh every 10 s (P4 may have restarted quietly)");
    c.on_uart_line("0|DISCONNECT", 12100);
    c.p4_result(false);
    r = resends(c.on_tick(13000));
    CHECK(r.size() == 1 && !r[0].has_prof && std::string(r[0].scr.sender) == "hud-01", "disconnect forgets the phone");
    c.on_uart_line("0|CONNECT", 13100);
    c.on_uart_line("0|" + hello("app", kPhone), 13200);
    r = resends(c.on_tick(14000));
    CHECK(r.size() == 1, "reconnected phone has nothing to resend until it sends SCR/PROF again");
}

int main() {
    line_assembler();
    frames();
    hello_timeout();
    hud_id();
    reconnect_same_conn();
    device_messages();
    heartbeat();
    outgoing();
    motion_stream();
    link_timeout();
    hello_resend();
    start_drops_all();
    resync();
    ctx_resend();
    if (failures) {
        std::printf("%d failure(s)\n", failures);
        return 1;
    }
    std::printf("comm: all passed\n");
    return 0;
}
