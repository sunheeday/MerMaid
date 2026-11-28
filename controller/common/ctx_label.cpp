#include "ctx_label.hpp"

#include <algorithm>
#include <tuple>

namespace mm::ctx {

void Labeler::ride_start(uint64_t seed) {
    cyc_.clear();
    ev_.clear();
    rng_.seed(seed);
}

void Labeler::record_cycle(uint64_t seq, double t, const std::string& passenger, const bool eligible[kDevices]) {
    Cyc c{seq, t, {eligible[0], eligible[1], eligible[2]}};
    cyc_[passenger].push_back(c);
}

void Labeler::on_user(double t, const std::string& sender, int device, bool on) {
    if (device < 0 || device >= kDevices) return;
    ev_.push_back({t, device, sender, on, true});
}

void Labeler::on_auto(double t, int device, const std::string& owner, bool on) {
    if (device < 0 || device >= kDevices) return;
    ev_.push_back({t, device, device == kDots ? owner : std::string(), on, false});
}

std::vector<Pick> Labeler::picks(double t_end) {
    std::vector<Pick> out;
    std::vector<Ev> ev = ev_;
    std::stable_sort(ev.begin(), ev.end(), [](const Ev& a, const Ev& b) { return a.t < b.t; });
    for (auto& kv : cyc_) {
        const std::string& p = kv.first;
        const std::vector<Cyc>& C = kv.second;
        for (int j = 0; j < kDevices; j++) {
            const bool shared = j != kDots;
            auto mine = [&](const Ev& e) { return e.device == j && (shared || e.who == p); };
            enum Kind { POS, NEG, EXCL };
            std::vector<std::pair<double, Kind>> ons;  // 이 승객·장치에 해당하는 켜기 사건
            std::vector<double> offs;                  // 끈 시각 (끈 직후 구간을 음성에서 뺀다)
            for (size_t i = 0; i < ev.size(); i++) {
                const Ev& e = ev[i];
                if (!mine(e)) continue;
                if (!e.on) {
                    offs.push_back(e.t);
                    continue;
                }
                Kind k = EXCL;
                if (e.user) {
                    k = e.who == p ? POS : EXCL;
                } else {
                    const Ev* off = nullptr;  // 이 자동 켜짐 뒤 첫 끄기
                    for (size_t q = i + 1; q < ev.size(); q++)
                        if (ev[q].device == j && !ev[q].on && (shared || ev[q].who == p)) {
                            off = &ev[q];
                            break;
                        }
                    const bool quick = off && off->t - e.t <= cfg_.quick_off_s;
                    if (quick && off->user && off->who == p) k = NEG;   // 켜 줬는데 바로 끔: '아니오'
                    else if (!quick && !shared) k = POS;                // 내 폰의 dots를 그대로 둠: 받아들임
                    else k = EXCL;                                      // 누구 몫인지 / 뜻을 모름
                }
                ons.emplace_back(e.t, k);
            }
            // 사건 시각 이하의 마지막 자격 있는 주기 (같은 시각 = 그 주기의 결정 / 그 주기 뒤의 OVR)
            auto anchor = [&](double te) -> int {
                int best = -1;
                for (int c = 0; c < static_cast<int>(C.size()); c++)
                    if (C[c].t <= te && C[c].ok[j]) best = c;
                return best;
            };
            std::vector<char> used(C.size(), 0);
            int pos = -1;
            for (const auto& o : ons)
                if (o.second == POS) {
                    const int a = anchor(o.first);
                    if (a >= 0) pos = a;  // 마지막 양성 사건
                }
            if (pos >= 0) {
                out.push_back({p, C[pos].seq, j, 1});
                used[pos] = 1;
            }
            int neg = 0;
            for (auto it = ons.rbegin(); it != ons.rend() && neg < cfg_.neg_per_ride; ++it)
                if (it->second == NEG) {
                    const int a = anchor(it->first);
                    if (a >= 0 && !used[a]) {
                        out.push_back({p, C[a].seq, j, 0});
                        used[a] = 1;
                        neg++;
                    }
                }
            if (neg < cfg_.neg_per_ride) {
                std::vector<int> clean;
                for (int c = 0; c < static_cast<int>(C.size()); c++) {
                    const double t = C[c].t;
                    if (!C[c].ok[j] || used[c] || t >= t_end - cfg_.horizon_s) continue;
                    bool bad = false;
                    for (const auto& o : ons)
                        if (o.first >= t && o.first <= t + cfg_.horizon_s) bad = true;
                    for (double f : offs)
                        if (t >= f && t < f + cfg_.after_off_s) bad = true;
                    if (!bad) clean.push_back(c);
                }
                std::shuffle(clean.begin(), clean.end(), rng_);
                for (size_t q = 0; q < clean.size() && neg < cfg_.neg_per_ride; q++, neg++) {
                    out.push_back({p, C[clean[q]].seq, j, 0});
                    used[clean[q]] = 1;
                }
            }
        }
    }
    std::sort(out.begin(), out.end(), [](const Pick& a, const Pick& b) {
        return std::tie(a.passenger, a.seq, a.device, a.label) < std::tie(b.passenger, b.seq, b.device, b.label);
    });
    return out;
}

}  // namespace mm::ctx
