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
    // P5가 다시 시작해서 모르는 Tier는 P6이 그 기기에 마지막으로 보낸 INT로 되찾는다 (OUT_RESYNC → DEV_HELLO).
    // 아는 Tier는 P5가 기준이다. 새 연결은 P6 기록이 비어 있으므로 아무것도 바뀌지 않는다
    auto adopt = [&](int tier, bool& known, bool& value, const char* name) {
        const auto bit = static_cast<uint8_t>(1u << (tier - 1));
        if (!known && (m.int_known & bit)) {
            value = (m.int_on & bit) && !safe_off_;  // 안전 OFF 중에는 ON을 되살리지 않는다
            log(std::string("resync: ") + name + (value ? " ON" : " OFF") + " from conn " + std::to_string(m.conn));
        }
        known = true;  // 이제 이 연결에 보낼 것이므로 (모르면 OFF)
    };
    adopt(1, airflow_known_, airflow_, "Tier 1");
    adopt(2, display_known_, display_, "Tier 2");
    if (m.role == msg::Role::App && !dots_.count(dev) && (m.int_known & 4)) {
        dots_[dev] = (m.int_on & 4) && !safe_off_;
        log("resync: Tier 3 " + dev + (dots_[dev] ? " ON" : " OFF"));
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
    if (m.conn == msg::kConnAll) {  // P6이 다시 시작해서 모든 연결을 끊었다. 기기는 다시 붙으면 HELLO
        log("bye all (P6 restarted)");
        for (auto& e : e_) e = Entry{};
        return {};
    }
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
    if (safe_off_) {
        // P4가 다시 뜨면 첫 주기에 TIER_CMD를 먼저, DEV_SYNC를 그 뒤에 보낸다. 여기서 ON을 버리면 P4는 켰다고 믿고
        // 기기는 꺼진 채로 남는다 → 잡아 두었다가 DEV_SYNC로 안전 OFF가 풀린 뒤 다음 박자에 적용한다
        held_.erase(device);
        if (on) {
            held_[device] = m;
            log("safe off: hold TIER_CMD " + device + " ON until DEV_SYNC comes back");
            return out;
        }
    }
    if (tier == 1 || tier == 2) {
        (tier == 1 ? airflow_ : display_) = on;
        (tier == 1 ? airflow_known_ : display_known_) = true;
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
    // 시작: P6에게 등록된 기기를 다시 알려 달라고 한다 (P5만 다시 시작했을 때 명부를 되찾음). 전달될 때까지 매번
    if (!resync_done_) out.emplace_back(msg::OutResync{});
    if (last_sync_ < 0) last_sync_ = now_ms;  // 시작부터 30초 유예
    if (!safe_off_ && !held_.empty()) {  // 안전 OFF가 풀림: 그동안 잡아 둔 ON을 적용
        const std::map<std::string, msg::TierCmd> held = std::move(held_);
        held_.clear();
        for (const auto& h : held) {
            log("safe off cleared: apply held TIER_CMD " + h.first + " ON");
            Outs o = on_tier_cmd(h.second);
            out.insert(out.end(), o.begin(), o.end());
        }
    }
    if (safe_off_ || now_ms - last_sync_ < safety_ms_) return out;

    safe_off_ = true;
    held_.clear();
    airflow_ = display_ = false;
    airflow_known_ = display_known_ = true;
    for (auto& d : dots_) d.second = false;
    for (int i = 0; i < msg::kMaxConn; i++)
        if (e_[i].used) send_state(static_cast<uint8_t>(i), out);  // 꺼진 상태를 모두에게
    log("safe off: no DEV_SYNC for " + std::to_string(safety_ms_ / 1000) + " s, all interventions OFF");
    return out;
}

}  // namespace mm::devmgr
