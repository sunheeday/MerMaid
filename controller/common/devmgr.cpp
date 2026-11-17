// P5 기기 관리 핵심 로직. 설명은 devmgr.hpp.
#include "devmgr.hpp"

#include <cstring>

#include "protocol_gen.hpp"

namespace mm::devmgr {

namespace {

std::string id_of(const char (&s)[msg::kIdLen]) { return std::string(s, strnlen(s, msg::kIdLen)); }

// P4의 기기 ID → Tier (policy.json의 tier별 기기와 같다)
int tier_of(const std::string& device) {
    if (device == proto::kDeviceAirflow) return 1;
    if (device == proto::kDeviceDisplay) return 2;
    if (device == proto::kDeviceHud || device.empty()) return 0;
    return 3;  // 폰
}

}  // namespace

void DevMgr::push_int(Outs& out, uint8_t conn, int tier, bool on) {
    msg::OutInt m;
    m.conn = conn;
    m.tier = static_cast<uint8_t>(tier);
    m.on = on;
    out.emplace_back(m);
}

void DevMgr::push_stream(Outs& out, uint8_t conn, bool on) {
    msg::OutStream m;
    m.conn = conn;
    m.on = on;
    out.emplace_back(m);
}

int DevMgr::conn_of(const std::string& dev) const {
    for (int i = 0; i < msg::kMaxConn; i++)
        if (e_[i].used && e_[i].dev == dev) return i;
    return -1;
}

bool DevMgr::is_on(const std::string& device) const {
    if (device == proto::kDeviceAirflow) return airflow_;
    if (device == proto::kDeviceDisplay) return display_;
    auto it = dots_.find(device);
    return it != dots_.end() && it->second;
}

// 폰: Tier 1·2(표시용)·3 + 점 찍기 스트림 / HUD: Tier 1·2 + 앰비언트 스트림
void DevMgr::send_state(uint8_t conn, Outs& out) const {
    const Entry& e = e_[conn];
    push_int(out, conn, 1, airflow_);
    push_int(out, conn, 2, display_);
    if (e.role == msg::Role::Hud) {
        push_stream(out, conn, display_);
    } else {
        const bool dots = is_on(e.dev);
        push_int(out, conn, 3, dots);
        push_stream(out, conn, dots);
    }
}

// ============================================================
// P6 → P5
// ============================================================
Outs DevMgr::on_hello(const msg::DevHello& m) {
    Outs out;
    if (m.conn >= msg::kMaxConn) {
        log("bad DEV_HELLO conn");
        return out;
    }
    const std::string dev = id_of(m.dev);
    if (dev.empty()) {
        log("DEV_HELLO without dev");
        return out;
    }
    // 같은 기기가 다른 번호에 남아 있으면 (DISCONNECT를 못 받은 채 다시 붙음) 옛 줄을 지운다
    for (int i = 0; i < msg::kMaxConn; i++) {
        if (i != m.conn && e_[i].used && e_[i].dev == dev) {
            log("reconnect " + dev + ": conn " + std::to_string(i) + " -> " + std::to_string(m.conn));
            e_[i] = Entry{};
        }
    }
    Entry& e = e_[m.conn];
    if (e.used && e.dev != dev) log("conn " + std::to_string(m.conn) + " reused: " + e.dev + " -> " + dev);
    e.used = true;
    e.dev = dev;
    e.role = m.role;
    log("hello conn " + std::to_string(m.conn) + " " + dev + (m.role == msg::Role::Hud ? " (hud)" : " (app)"));

    send_state(m.conn, out);  // 재연결이면 끊기기 전 상태가 그대로 다시 나간다
    if (m.role == msg::Role::App && load_profile_) {
        Profile p;
        if (load_profile_(dev, p)) {
            msg::OutProf op;
            op.conn = m.conn;
            op.mssq = p.mssq;
            op.sens = p.sens_mssq;  // 앱이 보낸 설문 민감도 (M06). 현재 민감도는 컨트롤러에만
            out.emplace_back(op);
        }
    }
    return out;
}

Outs DevMgr::on_bye(const msg::DevBye& m) {
    if (m.conn < msg::kMaxConn && e_[m.conn].used) {
        log("bye conn " + std::to_string(m.conn) + " " + e_[m.conn].dev);
        e_[m.conn] = Entry{};
    }
    return {};  // 끊긴 연결에는 보낼 것이 없다. Tier 3 상태는 기억해 둔다
}

// ============================================================
// P4 → P5
// ============================================================
Outs DevMgr::on_tier_cmd(const msg::TierCmd& m) {
    Outs out;
    const std::string device = id_of(m.device);
    const int tier = tier_of(device);
    if (tier == 0 || tier != m.tier) {
        log("bad TIER_CMD " + device + " tier " + std::to_string(m.tier));
        return out;
    }
    const bool on = m.on != 0;
    if (safe_off_ && on) {
        log("safe off: ignore TIER_CMD " + device + " ON until DEV_SYNC comes back");
        return out;
    }
    if (tier == 1 || tier == 2) {
        (tier == 1 ? airflow_ : display_) = on;
        // 공용 기기: HUD가 실제로 켜고, 폰은 앱 화면에 표시만 한다 → 등록된 모두에게
        for (int i = 0; i < msg::kMaxConn; i++) {
            if (!e_[i].used) continue;
            const auto c = static_cast<uint8_t>(i);
            push_int(out, c, tier, on);
            if (tier == 2 && e_[i].role == msg::Role::Hud) push_stream(out, c, on);  // 앰비언트 빛 방향 = MOT
        }
    } else {
        dots_[device] = on;
        const int c = conn_of(device);
        if (c >= 0) {
            push_int(out, static_cast<uint8_t>(c), 3, on);
            push_stream(out, static_cast<uint8_t>(c), on);
        } else {
            log("TIER_CMD for " + device + " (not connected): remembered");
        }
    }
    return out;
}

msg::DevSyncReply DevMgr::on_sync(const msg::DevSync&, int64_t now_ms) {
    last_sync_ = now_ms;
    if (safe_off_) {
        log("DEV_SYNC back: safe off cleared");
        safe_off_ = false;
    }
    msg::DevSyncReply r;
    for (const Entry& e : e_) {
        if (!e.used) continue;
        msg::DevEntry& d = r.e[r.n++];
        msg::copy_id(d.dev, e.dev);
        d.role = e.role;
    }
    return r;
}

// ============================================================
// 안전 감시: P4가 30초 동안 DEV_SYNC를 안 보내면 모든 기기를 끈다
// ============================================================
Outs DevMgr::on_tick(int64_t now_ms) {
    Outs out;
    if (last_sync_ < 0) last_sync_ = now_ms;  // 시작부터 30초 유예
    if (safe_off_ || now_ms - last_sync_ < safety_ms_) return out;

    safe_off_ = true;
    airflow_ = display_ = false;
    for (auto& d : dots_) d.second = false;
    for (int i = 0; i < msg::kMaxConn; i++)
        if (e_[i].used) send_state(static_cast<uint8_t>(i), out);  // 꺼진 상태를 모두에게
    log("safe off: no DEV_SYNC for " + std::to_string(safety_ms_ / 1000) + " s, all interventions OFF");
    return out;
}

}  // namespace mm::devmgr
