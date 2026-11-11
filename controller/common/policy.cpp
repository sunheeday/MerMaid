#include "policy.hpp"

#include <algorithm>
#include <cmath>
#include <fstream>
#include <sstream>
#include <stdexcept>

#include "json_min.hpp"
#include "protocol_gen.hpp"

namespace mm {

// ---------------------------------------------------------------- 설정
PolicyConfig parse_policy_config(const std::string& policy_json, const std::string& thresholds_json) {
    const json::Value pol = json::parse(policy_json);
    const json::Value th = json::parse(thresholds_json);
    PolicyConfig c;
    const char* names[3] = {"1", "2", "3"};
    for (int t = 0; t < 3; t++) {
        const json::Value& x = pol.at("tiers").at(names[t]);
        c.tier[t].device = x.at("device").string();
        c.tier[t].input_k = x.at("input_k").integer();
        c.tier[t].shared = x.at("shared").boolean();
        c.tier[t].hold = x.at("hold_cycles").integer();
        c.tier[t].gate = x.at("gate").string();
        c.base_th[t] = th.at("base_th").at(names[t]).number();
        if (c.tier[t].input_k < 2 || c.tier[t].input_k > 6) throw std::runtime_error("policy.json: input_k must be 2..6");
        if (c.tier[t].hold < 1) throw std::runtime_error("policy.json: hold_cycles must be >= 1");
        if (!(c.base_th[t] > 0 && c.base_th[t] < 1)) throw std::runtime_error("thresholds.json: base_th must be in (0, 1)");
    }
    if (c.tier[0].device != proto::kDeviceAirflow || c.tier[1].device != proto::kDeviceDisplay)
        throw std::runtime_error("policy.json: tier 1/2 device IDs differ from constants.json");
    if (th.has("placeholder") && th.at("placeholder").boolean())
        throw std::runtime_error("thresholds.json is a placeholder: run ml/train/train.py");
    if (th.at("feature_order_version").integer() != proto::kFeatureVersion)
        throw std::runtime_error("thresholds.json: feature_order_version differs from the controller build");
    c.off_margin = pol.at("off_margin").number();
    c.off_hold = pol.at("off_hold_cycles").integer();
    c.cooldown_s = pol.at("cooldown_s").number();
    c.quick_off_s = pol.at("quick_off_s").number();
    c.max_quick_offs = pol.at("max_quick_offs").integer();
    c.tol_scale = pol.at("tolerance").at("scale").number();
    c.tol_divisor = pol.at("tolerance").at("divisor").number();
    c.n_min = pol.at("tolerance").at("n_min").integer();
    c.sens_on_step = pol.at("sensitivity").at("on_step").number();
    c.sens_max = pol.at("sensitivity").at("max").number();
    c.ride_end_return = pol.at("sensitivity").at("ride_end_return").number();
    return c;
}

static std::string read_file(const std::string& path) {
    std::ifstream f(path, std::ios::binary);
    if (!f) throw std::runtime_error("cannot open " + path);
    std::ostringstream ss;
    ss << f.rdbuf();
    return ss.str();
}

PolicyConfig load_policy_config(const std::string& policy_path, const std::string& thresholds_path) {
    return parse_policy_config(read_file(policy_path), read_file(thresholds_path));
}

// ---------------------------------------------------------------- 정책
Policy::Policy(PolicyConfig cfg) : cfg_(std::move(cfg)) {}

void Policy::restore_tolerance(const std::string& device, int n) { n_[device] = std::max(n, cfg_.n_min); }
void Policy::restore_profile(const std::string& phone, const Profile& p) { prof_[phone] = p; }

void Policy::set_hud_connected(bool connected) {
    hud_ = connected;
    if (!connected) {  // 기기가 사라지면 꺼진 것으로 본다. 주행별 끄기 횟수, 비활성화는 유지
        state_[proto::kDeviceAirflow].on = false;
        state_[proto::kDeviceDisplay].on = false;
    }
}

void Policy::add_phone(const std::string& phone) {
    if (!is_phone(phone)) phones_.push_back(phone);
    riders_.insert(phone);
}

void Policy::remove_phone(const std::string& phone) {
    phones_.erase(std::remove(phones_.begin(), phones_.end(), phone), phones_.end());
    state_[phone].on = false;
}

void Policy::on_ride_start() {
    state_.clear();
    riders_.clear();
    riders_.insert(phones_.begin(), phones_.end());
}

void Policy::on_ride_end() {
    for (const auto& id : riders_) {
        auto it = prof_.find(id);
        if (it == prof_.end()) continue;
        Profile& p = it->second;
        p.sens += cfg_.ride_end_return * (p.sens_mssq - p.sens);
        dirty_prof_.insert(id);
    }
}

void Policy::on_screen(const std::string& sender, bool screen, bool att) { ctx_[sender] = {screen, att}; }

void Policy::on_profile(const std::string& phone, double mssq, double sens0) {
    auto it = prof_.find(phone);
    if (it == prof_.end()) {
        prof_[phone] = {mssq, sens0, sens0};  // 처음 본 사람
    } else if (mssq != it->second.mssq) {
        it->second = {mssq, sens0, sens0};    // 설문을 다시 한 경우만. 같은 설문값이면 현재 민감도 유지
    } else {
        return;
    }
    dirty_prof_.insert(phone);
}

std::vector<std::string> Policy::present() const {
    std::vector<std::string> d;
    if (hud_) {
        d.push_back(proto::kDeviceAirflow);
        d.push_back(proto::kDeviceDisplay);
    }
    d.insert(d.end(), phones_.begin(), phones_.end());
    return d;
}

bool Policy::is_phone(const std::string& id) const {
    return std::find(phones_.begin(), phones_.end(), id) != phones_.end();
}

int Policy::tier_of(const std::string& device) const {
    if (hud_ && device == proto::kDeviceAirflow) return 1;
    if (hud_ && device == proto::kDeviceDisplay) return 2;
    return is_phone(device) ? 3 : 0;
}

bool Policy::gate(const std::string& device, int tier) const {
    const std::string& g = cfg_.tier[tier - 1].gate;
    if (g == "none") return true;
    if (g == "hud_screen_on") {
        auto it = ctx_.find(proto::kDeviceHud);
        return it != ctx_.end() && it->second.screen;
    }
    if (g == "screen_on_and_att") {
        auto it = ctx_.find(device);
        return it != ctx_.end() && it->second.screen && it->second.att;
    }
    return false;  // 모르는 게이트는 닫힘
}

double Policy::tol(const std::string& device) const {
    return cfg_.tol_scale * std::tanh(tolerance_count(device) / cfg_.tol_divisor);
}

int Policy::tolerance_count(const std::string& device) const {
    auto it = n_.find(device);
    return it == n_.end() ? 0 : it->second;
}

const Profile* Policy::profile(const std::string& phone) const {
    auto it = prof_.find(phone);
    return it == prof_.end() ? nullptr : &it->second;
}

bool Policy::is_on(const std::string& device) const {
    auto it = state_.find(device);
    return it != state_.end() && it->second.on;
}

bool Policy::is_enabled(const std::string& device) const {
    auto it = state_.find(device);
    return it == state_.end() || it->second.enabled;
}

void Policy::set(const std::string& device, bool on, double now, std::vector<Command>& out) {
    DevState& s = state_[device];
    s.on = on;
    if (on) s.on_time = now;
    out.push_back({device, tier_of(device), on});
}

std::vector<Command> Policy::on_risk(double now, const std::map<std::string, Risk>& p) {
    std::vector<Command> out;
    for (const std::string& d : present()) {
        const int tier = tier_of(d);
        const TierCfg& tc = cfg_.tier[tier - 1];
        const size_t col = static_cast<size_t>(tc.input_k - 2);
        double r = 0;  // 확률이 없으면 위험 낮음으로 본다 (켜지 않고, 켜져 있으면 꺼지는 쪽)
        if (tc.shared) {
            for (const auto& kv : p) r = std::max(r, kv.second[col]);
        } else {
            auto it = p.find(d);
            if (it != p.end()) r = it->second[col];
        }
        const double th = cfg_.base_th[tier - 1] + tol(d);
        const bool ok = gate(d, tier);
        DevState& s = state_[d];
        s.cnt = (r >= th && ok) ? s.cnt + 1 : 0;
        s.low = (r < th - cfg_.off_margin) ? s.low + 1 : 0;
        if (!s.on && s.cnt >= tc.hold && now >= s.cool_until && s.enabled) {
            set(d, true, now, out);
        } else if (s.on && (s.low >= cfg_.off_hold || !ok)) {
            set(d, false, now, out);
        }
    }
    return out;
}

std::vector<Command> Policy::on_override(double now, const std::string& sender, const std::string& device, bool on) {
    std::vector<Command> out;
    if (tier_of(device) == 0) return out;  // 등록되지 않은 기기
    DevState& s = state_[device];
    if (!on && s.on) {
        if (now - s.on_time <= cfg_.quick_off_s) {
            n_[device] = tolerance_count(device) + 1;
            dirty_tol_.insert(device);
            if (++s.quick >= cfg_.max_quick_offs) s.enabled = false;
        }
        s.cool_until = now + cfg_.cooldown_s;
        set(device, false, now, out);
    } else if (on && !s.on) {
        n_[device] = std::max(tolerance_count(device) - 1, cfg_.n_min);
        dirty_tol_.insert(device);
        // 자기 폰에서 화면 큐(자기 폰)나 앰비언트(display)를 켤 때만 민감도를 올린다.
        // 송풍 켜기(더워서일 수 있음)와 HUD 버튼(누가 눌렀는지 모름)은 바꾸지 않는다.
        if (is_phone(sender) && (device == proto::kDeviceDisplay || device == sender)) {
            auto it = prof_.find(sender);
            if (it != prof_.end()) {
                it->second.sens = std::min(it->second.sens * cfg_.sens_on_step, cfg_.sens_max);
                dirty_prof_.insert(sender);
            }
        }
        set(device, true, now, out);
    }
    return out;
}

std::vector<std::string> Policy::take_dirty_tolerance() {
    std::vector<std::string> v(dirty_tol_.begin(), dirty_tol_.end());
    dirty_tol_.clear();
    return v;
}

std::vector<std::string> Policy::take_dirty_profiles() {
    std::vector<std::string> v(dirty_prof_.begin(), dirty_prof_.end());
    dirty_prof_.clear();
    return v;
}

}  // namespace mm
