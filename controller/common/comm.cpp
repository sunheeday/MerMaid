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

}  // namespace

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

Actions Comm::on_uart_line(const std::string& line, int64_t now_ms) {
    Actions out;
    Frame f;
    if (!parse_frame(line, f)) {
        log("bad line: " + line);
        return out;
    }
    if (f.payload == "CONNECT") {
        forget(f.conn, out);  // DISCONNECT를 놓쳤을 때를 대비해 옛 등록을 먼저 지운다
        conn_[f.conn].hello_due = now_ms + kHelloTimeoutMs;
        conn_[f.conn].last_rx = now_ms;
    } else if (f.payload == "DISCONNECT") {
        forget(f.conn, out);
    } else {
        conn_[f.conn].last_rx = now_ms;  // 무엇이든 받으면 링크는 살아 있음 (HB 포함)
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
            c.registered = true;
            c.delivered = false;
            c.hello_due = -1;
            c.role = role == "hud" ? msg::Role::Hud : msg::Role::App;
            c.dev = dev;
            msg::DevHello m;
            m.conn = conn;
            m.role = c.role;
            m.ver = static_cast<uint16_t>(ver);
            msg::copy_id(m.dev, dev);
            out.emplace_back(m);
            return;
        }

        // 여기부터는 보낸 기기를 알아야 한다 → HELLO 전이면 버린다
        if (!c.registered) {
            log(t + " before HELLO from " + from);
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
            out.emplace_back(m);
        } else {
            log("unknown t " + t + " from " + from);
        }
    } catch (const std::exception& e) {
        log("bad message from " + from + " (" + e.what() + "): " + text);
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
    if (next_hb_ms_ < 0 || now_ms >= next_hb_ms_) {
        out.emplace_back(UartLine{"ALL|{\"t\":\"HB\",\"seq\":" + std::to_string(hb_seq_++) + "}"});
        // P5에 아직 못 알린 등록을 1초마다 다시 (P5가 P6보다 늦게 뜨거나 잠깐 응답이 없을 때)
        for (int i = 0; i < msg::kMaxConn; i++) {
            const Conn& c = conn_[i];
            if (!c.registered || c.delivered) continue;
            msg::DevHello m;
            m.conn = static_cast<uint8_t>(i);
            m.role = c.role;
            m.ver = 1;
            msg::copy_id(m.dev, c.dev);
            out.emplace_back(m);
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
        }
        // 등록된 기기가 5초 동안 조용하면 (앱은 HB를 1초마다 보냄) 끊긴 것으로 본다.
        // 브리지가 재시작되면 DISCONNECT가 오지 않으므로 이것이 유일한 정리 방법이다
        if (link_timeout_ms_ > 0 && c.registered && now_ms - c.last_rx >= link_timeout_ms_) {
            out.emplace_back(UartLine{std::to_string(i) + "|DROP"});
            log("no message in 5 s, drop " + std::to_string(i) + " (" + c.dev + ")");
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
    return out;
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
    return out;
}

Actions Comm::on_out_drop(const msg::OutDrop& m) {
    Actions out;
    if (!valid(m.conn)) {
        log("bad OUT_DROP");
        return out;
    }
    out.emplace_back(UartLine{std::to_string(m.conn) + "|DROP"});
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
