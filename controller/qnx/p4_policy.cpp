// P4 정책: P3의 RISK로 기기별 ON/OFF를 정하고 로그와 명령을 남긴다. BLE(P5)가 붙기 전까지
// 기기 등록, 화면 상태, 프로필은 명령행으로 준다 (화면 켜짐, 주의 있음으로 고정).
//   p4_policy --config DIR --state DIR [--log FILE] [--hud] [--phone ID:MSSQ:SENS0[:EV]]... [--once] [--prio P]
// 허용도와 프로필은 --state 아래에 바뀔 때마다 저장하고(store.hpp), 시작할 때 읽어 온다.
// 명령은 stdout에 "CMD <t> <device> <tier> <0|1>", 주기 로그는 --log CSV.
// 정책 시각은 주행 시각(RISK의 t)을 쓴다.
#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <sstream>
#include <string>
#include <vector>

#include "ipc.hpp"
#include "messages.hpp"
#include "json_min.hpp"
#include "store.hpp"

using namespace mm;

union Inbox {
    msg::Hdr h;
    msg::RiskMsg r;
    msg::RideEdge e;
    msg::ProfilesReq q;
    _pulse pulse;
    Inbox() {}
};

struct PhoneArg {
    std::string id;
    double mssq = 0, sens0 = 1, ev = 0;
};

static PhoneArg parse_phone(const std::string& s) {
    std::vector<std::string> f;
    std::stringstream ss(s);
    std::string part;
    while (std::getline(ss, part, ':')) f.push_back(part);
    if (f.size() < 3 || f.size() > 4) throw std::runtime_error("--phone wants ID:MSSQ:SENS0[:EV], got " + s);
    PhoneArg p;
    p.id = f[0];
    p.mssq = json::parse(f[1]).number();
    p.sens0 = json::parse(f[2]).number();
    if (f.size() == 4) p.ev = json::parse(f[3]).number();
    if (!store::safe_name(p.id) || p.id.size() >= static_cast<size_t>(msg::kIdLen))
        throw std::runtime_error("bad phone id " + p.id);
    return p;
}

int main(int argc, char** argv) {
    std::string cfg_dir, state_dir, log_path;
    bool hud = false, once = false;
    int prio = 15;
    std::vector<PhoneArg> phones;
    try {
        for (int i = 1; i < argc; i++) {
            const std::string a = argv[i];
            const bool more = i + 1 < argc;
            if (a == "--config" && more) cfg_dir = argv[++i];
            else if (a == "--state" && more) state_dir = argv[++i];
            else if (a == "--log" && more) log_path = argv[++i];
            else if (a == "--phone" && more) phones.push_back(parse_phone(argv[++i]));
            else if (a == "--prio" && more) prio = std::atoi(argv[++i]);
            else if (a == "--hud") hud = true;
            else if (a == "--once") once = true;
            else throw std::runtime_error("unknown argument " + a);
        }
        if (cfg_dir.empty() || state_dir.empty()) throw std::runtime_error("--config and --state are required");
        if (phones.size() > static_cast<size_t>(msg::kMaxPhones)) throw std::runtime_error("too many phones");

        ipc::set_priority(prio, "p4");
        Policy pol(load_policy_config(cfg_dir + "/policy.json", cfg_dir + "/thresholds.json"));
        store::Store st(state_dir);
        std::map<std::string, double> ev;
        std::vector<std::string> devices;
        if (hud) {
            pol.set_hud_connected(true);
            pol.on_screen(proto::kDeviceHud, true, false);
            devices = {proto::kDeviceAirflow, proto::kDeviceDisplay};
        }
        for (const auto& p : phones) {
            pol.add_phone(p.id);
            pol.on_screen(p.id, true, true);
            Profile saved;
            if (st.load_profile(p.id, saved)) pol.restore_profile(p.id, saved);
            pol.on_profile(p.id, p.mssq, p.sens0);  // 같은 설문값이면 저장된 현재 민감도를 유지
            ev[p.id] = p.ev;
            devices.push_back(p.id);
        }
        for (const auto& d : devices) {
            int n;
            if (st.load_tolerance(d, n)) pol.restore_tolerance(d, n);
        }
        st.save_dirty(pol);

        FILE* log = nullptr;
        if (!log_path.empty()) {
            log = std::fopen(log_path.c_str(), "w");
            if (!log) throw std::runtime_error("cannot open " + log_path);
            std::fprintf(log, "seq,t,latency_ms,infer_ms,phone,p2,p3,p4,p5,p6");
            for (const auto& d : devices) std::fprintf(log, ",%s", d.c_str());
            std::fprintf(log, "\n");
        }

        ipc::Server srv(msg::kNameP4);
        Inbox in;
        uint64_t cycles = 0, gaps = 0, expect = 0, cmds = 0, saves = 0;
        std::vector<double> lat_ms;
        for (;;) {
            const int rcvid = srv.receive(&in, sizeof in);
            const uint16_t type = in.h.type;
            if (type == msg::PROFILES) {
                msg::ProfilesReply rep;
                rep.tol_display = pol.tol(proto::kDeviceDisplay);
                for (const auto& p : phones) {
                    msg::ProfileEntry& e = rep.e[rep.n++];
                    msg::copy_id(e.id, p.id);
                    const Profile* pf = pol.profile(p.id);
                    e.mssq = pf ? pf->mssq : p.mssq;
                    e.sens = pf ? pf->sens : p.sens0;
                    e.ev = ev[p.id];
                    e.tol = pol.tol(p.id);
                }
                ipc::Server::reply(rcvid, &rep, sizeof rep);
            } else if (type == msg::RISK) {
                const msg::RiskMsg r = in.r;
                ipc::Server::reply(rcvid);
                if (r.seq != expect) gaps += r.seq > expect ? r.seq - expect : 1;
                expect = r.seq + 1;
                std::map<std::string, Risk> risk;
                for (uint32_t i = 0; i < r.n && i < static_cast<uint32_t>(msg::kMaxPhones); i++)
                    risk[std::string(r.id[i], strnlen(r.id[i], msg::kIdLen))] = r.p[i];
                for (const Command& c : pol.on_risk(r.t, risk)) {
                    std::printf("CMD %.1f %s %d %d\n", r.t, c.device.c_str(), c.tier, c.on ? 1 : 0);
                    cmds++;
                }
                std::fflush(stdout);
                saves += static_cast<uint64_t>(st.save_dirty(pol));
                const double lat = (ipc::now_ns() - r.t_emit_ns) / 1e6;
                lat_ms.push_back(lat);
                cycles++;
                if (log) {
                    for (uint32_t i = 0; i < r.n && i < static_cast<uint32_t>(msg::kMaxPhones); i++) {
                        std::fprintf(log, "%llu,%.3f,%.3f,%.3f,%s", (unsigned long long)r.seq, r.t, lat,
                                     r.t_infer_ns / 1e6, r.id[i]);
                        for (double p : r.p[i]) std::fprintf(log, ",%.17g", p);
                        for (const auto& d : devices) std::fprintf(log, ",%d", pol.is_on(d) ? 1 : 0);
                        std::fprintf(log, "\n");
                    }
                    std::fflush(log);
                }
            } else if (type == msg::RIDE_START || type == msg::RIDE_END) {
                ipc::Server::reply(rcvid);
                if (type == msg::RIDE_START) {
                    pol.on_ride_start();
                    cycles = gaps = expect = cmds = 0;
                    lat_ms.clear();
                } else {
                    pol.on_ride_end();
                    saves += static_cast<uint64_t>(st.save_dirty(pol));
                    std::vector<double> s = lat_ms;
                    std::sort(s.begin(), s.end());
                    std::printf("{\"proc\": \"p4\", \"cycles\": %llu, \"risk_gaps\": %llu, \"commands\": %llu, "
                                "\"files_saved\": %llu, \"latency_ms_median\": %.3f, \"latency_ms_max\": %.3f}\n",
                                (unsigned long long)cycles, (unsigned long long)gaps, (unsigned long long)cmds,
                                (unsigned long long)saves, s.empty() ? 0.0 : s[s.size() / 2],
                                s.empty() ? 0.0 : s.back());
                    std::fflush(stdout);
                    if (once) {
                        if (log) std::fclose(log);
                        return gaps ? 1 : 0;
                    }
                }
            } else {
                ipc::Server::error(rcvid, EINVAL);
            }
        }
    } catch (const std::exception& ex) {
        std::fprintf(stderr, "p4: %s\n", ex.what());
        return 1;
    }
}
