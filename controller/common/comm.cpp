// P6 통신 핵심 로직. 설명은 comm.hpp.
#include "comm.hpp"

#include <cmath>
#include <cstdio>
#include <exception>

#include "json_min.hpp"
#include "protocol_gen.hpp"
#include "store.hpp"

namespace mm::comm {

// ============================================================
// 줄 조립, 프레임
// ============================================================
void LineAssembler::feed(const char* data, size_t n, const std::function<void(const std::string&)>& on_line) {
    for (size_t i = 0; i < n; i++) {
        const char c = data[i];
        if (c == '\n') {
            if (!overflow_ && !buf_.empty()) on_line(buf_);
            buf_.clear();
            overflow_ = false;
        } else if (c != '\r' && !overflow_) {
            buf_ += c;
            if (buf_.size() > kMaxFrame) {
                buf_.clear();
                overflow_ = true;
            }
        }
    }
}

bool parse_frame(std::string_view line, Frame& out) {
    // 연결 번호는 한 자리(0~8)라 '|'는 반드시 둘째 글자
    if (line.size() < 3 || line[1] != '|') return false;
    const char d = line[0];
    if (d < '0' || d >= '0' + msg::kMaxConn) return false;
    out.conn = static_cast<uint8_t>(d - '0');
    out.payload = std::string(line.substr(2));
    return true;
}

// ============================================================
// 도우미
// ============================================================
namespace {

std::string prefix(uint8_t conn) { return conn == msg::kConnAll ? std::string("ALL|") : std::to_string(conn) + "|"; }

const char* tf(bool b) { return b ? "true" : "false"; }

// 기기 ID: 파일 이름으로 안전하고(P4가 profiles/<ID>.json으로 저장) kIdLen에 NUL까지 들어가야 한다
bool good_id(const std::string& id) { return store::safe_name(id) && id.size() < static_cast<size_t>(msg::kIdLen); }

bool shared_device(const std::string& id) { return id == proto::kDeviceAirflow || id == proto::kDeviceDisplay; }

// 로그 사건 value(JSON)에 넣을 문자열: 따옴표·역슬래시는 이스케이프, 제어 문자와 ASCII 밖 바이트는 '?'.
// 결과가 max자를 넘지 않게 자른다 (이스케이프 중간이나 UTF-8 글자 중간에서 잘리지 않음 → 항상 올바른 JSON·TEXT)
std::string jesc(const std::string& s, size_t max) {
    std::string out;
    for (const char ch : s) {
        const auto u = static_cast<unsigned char>(ch);
        const bool esc = ch == '"' || ch == '\\';
        if (out.size() + (esc ? 2 : 1) > max) break;
        if (esc) out += '\\';
        out += (u < 0x20 || u >= 0x7F) ? '?' : ch;
    }
    return out;
}

std::string conn_json(int conn) { return "{\"conn\":" + std::to_string(conn); }

// 기기 JSON의 "t" (못 읽으면 "")
std::string msg_type(const std::string& text) {
    try {
        return json::parse(text).at("t").string();
    } catch (const std::exception&) {
        return "";
    }
}

}  // namespace

std::string Comm::who(int conn) const {
    return valid(conn) && conn_[conn].registered ? conn_[conn].dev : "conn" + std::to_string(conn);
}

// 기기가 보냈지만 버린 것. conn < 0: 연결 번호도 못 읽은 줄
void Comm::reject(int conn, const std::string& reason, const std::string& raw) {
    if (rejects_ >= kMaxRejectsPerSec) return;
    rejects_++;
    event(conn < 0 ? std::string("uart") : who(conn), msg::CtxKind::REJECT,
          (conn < 0 ? std::string("{\"conn\":null") : conn_json(conn)) + ",\"reason\":\"" + jesc(reason, 60) +
              "\",\"raw\":\"" + jesc(raw, 90) + "\"}");
}

// ============================================================
// ESP32 → P6
// ============================================================
void Comm::forget(uint8_t conn, Actions& out) {
    if (conn_[conn].registered) {
        msg::DevBye bye;
        bye.conn = conn;
        out.emplace_back(bye);
    }
    conn_[conn] = Conn{};
}

msg::DevHello Comm::make_hello(uint8_t conn) const {
    const Conn& c = conn_[conn];
    msg::DevHello m;
    m.conn = conn;
    m.role = c.role;
    m.ver = 1;
    msg::copy_id(m.dev, c.dev);
    m.int_known = c.int_known;
    m.int_on = c.int_on;
    return m;
}

Actions Comm::on_start(int64_t now_ms) {
    Actions out;
    for (int i = 0; i < msg::kMaxConn; i++) {
        out.emplace_back(UartLine{std::to_string(i) + "|DROP"});  // 브리지는 빈 번호의 DROP을 무시한다
        conn_[i] = Conn{};
        unknown_dropped_[i] = false;
    }
    redrop_due_ms_ = now_ms + kStartRedropMs;
    msg::DevBye bye;
    bye.conn = msg::kConnAll;
    out.emplace_back(bye);
    log("start: drop all bridge connections, devices will reconnect and HELLO");
    event("all", msg::CtxKind::DROP, "{\"conn\":\"all\",\"reason\":\"p6_start\"}");
    return out;
}

Actions Comm::on_uart_line(const std::string& line, int64_t now_ms) {
    Actions out;
    Frame f;
    if (!parse_frame(line, f)) {
        log("bad line: " + line);
        reject(-1, "bad line", line);
        return out;
    }
    if (f.payload == "CONNECT") {
        forget(f.conn, out);  // DISCONNECT를 놓쳤을 때를 대비해 옛 등록을 먼저 지운다
        event(who(f.conn), msg::CtxKind::CONNECT, conn_json(f.conn) + "}");
        conn_[f.conn].connected = true;
        conn_[f.conn].hello_due = now_ms + kHelloTimeoutMs;
        conn_[f.conn].last_rx = now_ms;
    } else if (f.payload == "DISCONNECT") {
        event(who(f.conn), msg::CtxKind::DISCONNECT,
              conn_json(f.conn) + ",\"registered\":" + (conn_[f.conn].registered ? "1" : "0") + "}");
        forget(f.conn, out);
    } else {
        Conn& c = conn_[f.conn];
        c.last_rx = now_ms;  // 무엇이든 받으면 링크는 살아 있음 (HB 포함)
        // 모르는 연결: P6보다 먼저 붙어 있던 기기 (시작 DROP이 브리지에서 안 먹힘). HB만 계속 오고 HELLO는
        // 다시 오지 않으므로, 그 연결을 끊어 기기가 다시 붙고(CONNECT) HELLO 하게 한다 (#16). HELLO는 그대로 받는다
        const bool is_hello = msg_type(f.payload) == "HELLO";
        if (!c.connected && !c.registered && !is_hello &&
            (!unknown_dropped_[f.conn] || now_ms - unknown_drop_ms_[f.conn] >= kUnknownDropMs)) {
            unknown_dropped_[f.conn] = true;
            unknown_drop_ms_[f.conn] = now_ms;
            out.emplace_back(UartLine{std::to_string(f.conn) + "|DROP"});
            log("line from unknown connection " + std::to_string(f.conn) + " (no CONNECT, no HELLO), drop it");
            event(who(f.conn), msg::CtxKind::DROP, conn_json(f.conn) + ",\"reason\":\"unknown_conn\"}");
        }
        on_device_json(f.conn, f.payload, out);
    }
    return out;
}

void Comm::on_device_json(uint8_t conn, const std::string& text, Actions& out) {
    Conn& c = conn_[conn];
    const std::string from = std::to_string(conn);
    std::string t;
    try {
        const json::Value v = json::parse(text);
        t = v.at("t").string();

        if (t == "HB") return;  // 링크 확인용. P6에서 끝

        if (t == "HELLO") {
            const std::string role = v.at("role").string();
            std::string dev = v.at("dev").string();
            const int ver = v.at("v").integer();
            if (ver != 1) throw std::runtime_error("unsupported v " + std::to_string(ver));
            if (role == "hud") {
                // HUD 연결은 하나뿐이고 P4는 hud-01로 안다 (PROTOCOL.md §4)
                if (dev != proto::kDeviceHud) log("HUD dev " + dev + " registered as " + proto::kDeviceHud);
                dev = proto::kDeviceHud;
            } else if (role == "app") {
                if (!good_id(dev) || shared_device(dev) || dev == proto::kDeviceHud)
                    throw std::runtime_error("bad dev " + dev);
            } else {
                throw std::runtime_error("bad role " + role);
            }
            if (c.registered && c.dev != dev) c.int_known = c.int_on = 0;  // 다른 기기: 보낸 INT 기록은 무효
            c.registered = true;
            c.delivered = false;
            c.hello_due = -1;
            c.role = role == "hud" ? msg::Role::Hud : msg::Role::App;
            c.dev = dev;
            out.emplace_back(make_hello(conn));
            return;
        }

        // 여기부터는 보낸 기기를 알아야 한다 → HELLO 전이면 버린다
        if (!c.registered) {
            log(t + " before HELLO from " + from);
            reject(conn, t + " before HELLO", text);
            return;
        }
        const bool hud = c.role == msg::Role::Hud;

        if (t == "SCR") {
            const bool on = v.at("on").boolean();
            const bool att = v.at("att").boolean();
            msg::CtxScreen m;
            msg::copy_id(m.sender, c.dev);
            m.on = on;
            m.att = !hud && on && att;  // HUD는 응시 없음. 화면이 꺼져 있으면 응시도 아님
            c.scr = m;
            c.has_scr = true;
            out.emplace_back(m);
        } else if (t == "OVR") {
            const std::string dev = v.at("dev").string();
            const bool on = v.at("on").boolean();
            // 공용 기기는 누구나, 폰의 점 찍기는 그 폰만 (다른 폰이나 HUD가 남의 화면을 바꾸지 않게)
            if (!shared_device(dev) && dev != c.dev) throw std::runtime_error("OVR target " + dev + " not allowed");
            msg::CtxOvr m;
            msg::copy_id(m.sender, c.dev);
            msg::copy_id(m.device, dev);
            m.on = on;
            out.emplace_back(m);
        } else if (t == "PROF") {
            if (hud) throw std::runtime_error("PROF from HUD");
            const double mssq = v.at("mssq").number();
            const double sens = v.at("sens").number();
            if (!(mssq >= 0 && mssq <= 54) || !(sens >= proto::kSensMin && sens <= proto::kSensMax))
                throw std::runtime_error("PROF out of range");
            msg::CtxProf m;
            msg::copy_id(m.phone, c.dev);
            m.mssq = mssq;
            m.sens = sens;
            c.prof = m;
            c.has_prof = true;
            out.emplace_back(m);
        } else if (t == "SYM") {
            // M08 멀미 점수: 앱만, 0~10 정수만. 기록 전용이라 P4에는 가지 않는다 (PROTOCOL.md §3)
            if (hud) throw std::runtime_error("SYM from HUD");
            const double x = v.at("misc").number();
            if (!(x >= 0 && x <= 10) || x != std::floor(x)) throw std::runtime_error("SYM misc not an integer 0..10");
            out.emplace_back(Sym{c.dev, static_cast<int>(x)});
        } else {
            log("unknown t " + t + " from " + from);
            reject(conn, "unknown t " + t, text);
        }
    } catch (const std::exception& e) {
        log("bad message from " + from + " (" + e.what() + "): " + text);
        reject(conn, e.what(), text);
    }
}

// ============================================================
// 타이머: HB 1초, HELLO 5초
// ============================================================
void Comm::hello_delivered(uint8_t conn, const char* dev) {
    if (valid(conn) && conn_[conn].registered && conn_[conn].dev == dev) conn_[conn].delivered = true;
}

Actions Comm::on_tick(int64_t now_ms) {
    Actions out;
    if (redrop_due_ms_ >= 0 && now_ms >= redrop_due_ms_) {
        // 시작 DROP 한 번 더: 그 사이 CONNECT나 HELLO가 온 연결(이미 다시 붙은 기기)은 건드리지 않는다
        redrop_due_ms_ = -1;
        std::string list;
        for (int i = 0; i < msg::kMaxConn; i++) {
            if (conn_[i].connected || conn_[i].registered) continue;
            out.emplace_back(UartLine{std::to_string(i) + "|DROP"});
            list += (list.empty() ? "" : ",") + std::to_string(i);
        }
        if (!list.empty()) {
            log("start: drop again " + list);
            event("all", msg::CtxKind::DROP, "{\"conn\":[" + list + "],\"reason\":\"p6_start_retry\"}");
        }
    }
    if (next_hb_ms_ < 0 || now_ms >= next_hb_ms_) {
        rejects_ = 0;
        out.emplace_back(UartLine{"ALL|{\"t\":\"HB\",\"seq\":" + std::to_string(hb_seq_++) + "}"});
        // P5에 아직 못 알린 등록을 1초마다 다시 (P5가 P6보다 늦게 뜨거나 잠깐 응답이 없을 때)
        for (int i = 0; i < msg::kMaxConn; i++) {
            const Conn& c = conn_[i];
            if (!c.registered || c.delivered) continue;
            out.emplace_back(make_hello(static_cast<uint8_t>(i)));
        }
        // 마지막 SCR·PROF를 P4에: 못 보냈으면 1초마다, 아니면 10초마다 (P4가 재시작해도 10초 안에 다시 안다)
        if (next_refresh_ms_ < 0) next_refresh_ms_ = now_ms + kCtxRefreshMs;
        if (p4_down_ || now_ms >= next_refresh_ms_) {
            next_refresh_ms_ = now_ms + kCtxRefreshMs;
            for (const Conn& c : conn_) {
                if (!c.registered || !(c.has_scr || c.has_prof)) continue;
                CtxResend r;
                r.has_scr = c.has_scr;
                r.has_prof = c.has_prof;
                r.scr = c.scr;
                r.prof = c.prof;
                out.emplace_back(r);
            }
        }
        // 조금 늦으면 원래 박자를 지키고, 한 주기 넘게 밀리면 몰아서 보내지 않고 지금부터 다시
        if (next_hb_ms_ < 0 || now_ms - next_hb_ms_ >= kHbPeriodMs) next_hb_ms_ = now_ms + kHbPeriodMs;
        else next_hb_ms_ += kHbPeriodMs;
    }
    for (int i = 0; i < msg::kMaxConn; i++) {
        Conn& c = conn_[i];
        if (c.hello_due >= 0 && now_ms >= c.hello_due) {
            c.hello_due = -1;  // 한 번만. 브리지가 끊으면 DISCONNECT가 온다
            out.emplace_back(UartLine{std::to_string(i) + "|DROP"});
            log("no HELLO in 5 s, drop " + std::to_string(i));
            event(who(i), msg::CtxKind::DROP, conn_json(i) + ",\"reason\":\"no_hello_5s\"}");
        }
        // 등록된 기기가 5초 동안 조용하면 (앱은 HB를 1초마다 보냄) 끊긴 것으로 본다.
        // 브리지가 재시작되면 DISCONNECT가 오지 않으므로 이것이 유일한 정리 방법이다
        if (link_timeout_ms_ > 0 && c.registered && now_ms - c.last_rx >= link_timeout_ms_) {
            out.emplace_back(UartLine{std::to_string(i) + "|DROP"});
            log("no message in 5 s, drop " + std::to_string(i) + " (" + c.dev + ")");
            event(c.dev, msg::CtxKind::DROP, conn_json(i) + ",\"reason\":\"silent_5s\"}");
            forget(static_cast<uint8_t>(i), out);
        }
    }
    return out;
}

// ============================================================
// P5 → UART
// ============================================================
Actions Comm::on_out_int(const msg::OutInt& m) {
    Actions out;
    if ((m.conn != msg::kConnAll && !valid(m.conn)) || m.tier < 1 || m.tier > 3) {
        log("bad OUT_INT");
        return out;
    }
    out.emplace_back(UartLine{prefix(m.conn) + "{\"t\":\"INT\",\"tier\":" + std::to_string(m.tier) +
                              ",\"on\":" + tf(m.on) + "}"});
    // 무엇을 보냈는지 기억 (P5가 다시 시작하면 DEV_HELLO에 실어 돌려준다)
    const auto bit = static_cast<uint8_t>(1u << (m.tier - 1));
    const std::string tail = ",\"tier\":" + std::to_string(m.tier) + ",\"on\":" + (m.on ? "1" : "0");
    int reached = 0;
    for (int i = 0; i < msg::kMaxConn; i++) {
        Conn& c = conn_[i];
        if (!c.registered || (m.conn != msg::kConnAll && m.conn != i)) continue;
        c.int_known |= bit;
        c.int_on = static_cast<uint8_t>(m.on ? (c.int_on | bit) : (c.int_on & ~bit));
        // 로그: 받은 기기마다 한 줄 (ALL로 나간 것은 "all":1)
        event(c.dev, msg::CtxKind::INT, conn_json(i) + tail + (m.conn == msg::kConnAll ? ",\"all\":1}" : "}"));
        reached++;
    }
    if (reached == 0)  // 등록된 기기가 없는 번호나 ALL로 나간 줄도 남긴다
        event(m.conn == msg::kConnAll ? std::string("all") : who(m.conn), msg::CtxKind::INT,
              (m.conn == msg::kConnAll ? std::string("{\"conn\":\"all\"") : conn_json(m.conn)) + tail + "}");
    return out;
}

void Comm::on_resync() {
    int n = 0;
    for (auto& c : conn_) {
        if (!c.registered) continue;
        c.delivered = false;
        n++;
    }
    log("P5 resync: will resend HELLO for " + std::to_string(n) + " device(s)");
}

Actions Comm::on_out_stream(const msg::OutStream& m) {
    // 등록 안 된(이미 나간) 연결은 켜지 않는다: 그 번호를 다음에 받은 기기로 MOT가 새지 않게
    if (m.conn == msg::kConnAll) {
        for (auto& c : conn_) c.stream = m.on && c.registered;
    } else if (valid(m.conn)) {
        conn_[m.conn].stream = m.on && conn_[m.conn].registered;
    } else {
        log("bad OUT_STREAM");
    }
    return {};
}

Actions Comm::on_out_prof(const msg::OutProf& m) {
    Actions out;
    if (!valid(m.conn) || !std::isfinite(m.mssq) || !std::isfinite(m.sens)) {
        log("bad OUT_PROF");
        return out;
    }
    char buf[96];
    std::snprintf(buf, sizeof buf, "{\"t\":\"PROF\",\"mssq\":%.2f,\"sens\":%.3f}", m.mssq, m.sens);
    out.emplace_back(UartLine{prefix(m.conn) + buf});
    char ev[96];
    std::snprintf(ev, sizeof ev, ",\"mssq\":%.9g,\"sens\":%.9g}", m.mssq, m.sens);
    event(who(m.conn), msg::CtxKind::PROF_OUT, conn_json(m.conn) + ev);
    return out;
}

Actions Comm::on_out_drop(const msg::OutDrop& m) {
    Actions out;
    if (!valid(m.conn)) {
        log("bad OUT_DROP");
        return out;
    }
    out.emplace_back(UartLine{std::to_string(m.conn) + "|DROP"});
    event(who(m.conn), msg::CtxKind::DROP, conn_json(m.conn) + ",\"reason\":\"p5\"}");
    return out;
}

// ============================================================
// P2 → UART (10 Hz)
// ============================================================
Actions Comm::on_motion(const msg::Motion& m) {
    Actions out;
    if (!std::isfinite(m.ax) || !std::isfinite(m.ay)) return out;  // NaN은 JSON에 쓸 수 없다
    char buf[128];
    if (std::isfinite(m.pax) && std::isfinite(m.pay))
        std::snprintf(buf, sizeof buf, "{\"t\":\"MOT\",\"ax\":%.3f,\"ay\":%.3f,\"pax\":%.3f,\"pay\":%.3f}", m.ax, m.ay,
                      m.pax, m.pay);
    else
        std::snprintf(buf, sizeof buf, "{\"t\":\"MOT\",\"ax\":%.3f,\"ay\":%.3f}", m.ax, m.ay);
    for (int i = 0; i < msg::kMaxConn; i++)
        if (conn_[i].stream) out.emplace_back(UartLine{std::to_string(i) + "|" + buf});
    return out;
}

}  // namespace mm::comm
