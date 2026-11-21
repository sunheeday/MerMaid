// P4 정책: P3의 RISK로 기기별 ON/OFF를 정하고 로그와 명령을 남긴다.
//   p4_policy --config DIR --state DIR [--log FILE] [--rf] [--hud] [--phone ID:MSSQ:SENS0[:EV]]... [--once] [--prio P]
// 기기 등록은 두 길이다 (함께 써도 된다).
//   --rf    P5(mermaid_p5)에 DEV_SYNC를 보내 그 회신 명부로 폰·HUD를 등록/해제하고, 명령을 TIER_CMD로 P5에 보낸다.
//           P6의 CTX_SCREEN / CTX_OVR / CTX_PROF를 받아 Policy에 넘긴다 (docs/RF_MESSAGES.md).
//           DEV_SYNC는 RISK마다(10초)와 RIDE_START에 보낸다. 주행이 없으면 보내지 않으므로 30초 뒤 P5가 안전 OFF 한다.
//           RF로 온 폰의 EV 경험은 M06 PROF에 없어서 NaN으로 P3에 준다.
//   --phone / --hud  명령행으로 고정 등록 (화면 켜짐, 주의 있음). BLE 없이 AC-02를 돌릴 때.
// 허용도와 프로필은 --state 아래에 바뀔 때마다 저장하고(store.hpp), 시작할 때나 기기가 등록될 때 읽어 온다.
// 개인 보정 state/calib/<phone>.json은 읽기만 한다: 폰이 등록될 때와 RIDE_START마다 다시 읽어 PROFILES 회신에
// cal_a, cal_b로 P3에 넘긴다 (DATA_LOGGING §12-5). 없거나 잘못된 파일이면 1, 0 (잘못된 파일은 stderr에 한 번).
// 명령은 stdout에 "CMD <t> <device> <tier> <0|1>", 주기 로그는 --log CSV.
// 정책 시각은 주행 시각(RISK의 t)을 쓴다. OVR는 마지막 RISK의 t로 처리한다.
// RISK를 처리한 뒤 P7에 LOG_DECISION (기기별 on, tier, 이번 주기에 바뀌었는지. 2 ms 시간 초과, 실패는 log_fail만).
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <memory>
#include <set>
#include <sstream>
#include <string>
#include <vector>

#include "ipc.hpp"
#include "log_messages.hpp"
#include "messages.hpp"
#include "rf_messages.hpp"
#include "json_min.hpp"
#include "store.hpp"

using namespace mm;

union Inbox {
    msg::Hdr h;
    msg::RiskMsg r;
    msg::RideEdge e;
    msg::ProfilesReq q;
    msg::CtxScreen scr;
    msg::CtxOvr ovr;
    msg::CtxProf prof;
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

static std::string id_of(const char* s) { return std::string(s, strnlen(s, msg::kIdLen)); }

int main(int argc, char** argv) {
    std::string cfg_dir, state_dir, log_path;
    bool hud = false, once = false, rf = false;
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
            else if (a == "--rf") rf = true;
            else if (a == "--once") once = true;
            else throw std::runtime_error("unknown argument " + a);
        }
        if (cfg_dir.empty() || state_dir.empty()) throw std::runtime_error("--config and --state are required");
        if (phones.size() > static_cast<size_t>(msg::kMaxPhones)) throw std::runtime_error("too many phones");

        ipc::set_priority(prio, "p4");
        Policy pol(load_policy_config(cfg_dir + "/policy.json", cfg_dir + "/thresholds.json"));
        store::Store st(state_dir);
        std::map<std::string, double> ev;
        std::map<std::string, calib::Calib> cal;
        auto load_cal = [&](const std::string& id) {
            calib::Calib c;
            try {
                if (!st.load_calib(id, c)) c = {};
            } catch (const std::exception& e) {
                std::fprintf(stderr, "p4: ignoring calib for %s: %s\n", id.c_str(), e.what());
                c = {};
            }
            const auto it = cal.find(id);
            const bool changed = it == cal.end() ? !calib::identity(c) : (it->second.a != c.a || it->second.b != c.b);
            if (changed) std::printf("CAL %s a=%.4f b=%.4f\n", id.c_str(), c.a, c.b);
            cal[id] = c;
        };
        auto cal_of = [&](const std::string& id) {
            const auto it = cal.find(id);
            return it == cal.end() ? calib::Calib{} : it->second;
        };
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
            load_cal(p.id);
            devices.push_back(p.id);
        }
        for (const auto& d : devices) {
            int n;
            if (st.load_tolerance(d, n)) pol.restore_tolerance(d, n);
        }
        st.save_dirty(pol);

        // ---- RF: P5 명부로 등록, 명령은 TIER_CMD로 ----
        std::unique_ptr<ipc::Client> p5;
        if (rf) p5 = std::make_unique<ipc::Client>(msg::kNameP5, 200 * 1000000ULL);
        std::set<std::string> rf_phones;  // P5 명부의 폰 (명령행 폰 제외)
        bool rf_hud = false;
        uint64_t sync_seq = 0, sync_fail = 0, cmd_fail = 0;
        // P5가 다시 시작했으면 옛 연결로 보낸 첫 번은 실패하고 연결이 닫힌다 → 바로 한 번 더 (새로 연결).
        // 시간 초과(P5가 바쁨)는 연결이 남으므로 다시 보내지 않는다 (docs/RF_MESSAGES.md §2-2)
        auto send_p5 = [&](const void* m, size_t n, void* r = nullptr, size_t rn = 0) {
            return p5->send(m, n, r, rn) || (!p5->connected() && p5->send(m, n, r, rn));
        };
        auto restore_device = [&](const std::string& d) {
            int n;
            if (st.load_tolerance(d, n)) pol.restore_tolerance(d, n);
        };
        auto is_cli_phone = [&](const std::string& id) {
            return std::any_of(phones.begin(), phones.end(), [&](const PhoneArg& p) { return p.id == id; });
        };
        auto sync = [&]() {
            if (!p5) return;
            msg::DevSync s;
            s.seq = sync_seq++;
            msg::DevSyncReply rep;
            if (!send_p5(&s, sizeof s, &rep, sizeof rep)) {
                if (sync_fail++ == 0) std::fprintf(stderr, "p4: cannot reach %s (DEV_SYNC)\n", msg::kNameP5);
                return;
            }
            std::set<std::string> now;
            bool now_hud = false;
            for (uint32_t i = 0; i < rep.n && i < static_cast<uint32_t>(msg::kMaxConn); i++) {
                const std::string id = id_of(rep.e[i].dev);
                if (rep.e[i].role == msg::Role::Hud) now_hud = true;
                else if (store::safe_name(id) && !is_cli_phone(id)) now.insert(id);
            }
            if (now_hud != rf_hud && !hud) {
                pol.set_hud_connected(now_hud);
                if (now_hud) {
                    restore_device(proto::kDeviceAirflow);
                    restore_device(proto::kDeviceDisplay);
                }
                std::printf("REG hud %s\n", now_hud ? "on" : "off");
            }
            rf_hud = now_hud;
            for (const auto& id : rf_phones)
                if (!now.count(id)) {
                    pol.remove_phone(id);
                    std::printf("REG phone %s off\n", id.c_str());
                }
            std::set<std::string> kept;
            for (const auto& id : now) {
                if (!rf_phones.count(id)) {
                    if (phones.size() + kept.size() >= static_cast<size_t>(msg::kMaxPhones)) {
                        std::fprintf(stderr, "p4: more than %d phones, ignoring %s\n", msg::kMaxPhones, id.c_str());
                        continue;
                    }
                    pol.add_phone(id);
                    Profile saved;
                    if (!pol.profile(id) && st.load_profile(id, saved)) pol.restore_profile(id, saved);
                    restore_device(id);
                    load_cal(id);
                    std::printf("REG phone %s on\n", id.c_str());
                }
                kept.insert(id);
            }
            rf_phones = kept;
            std::fflush(stdout);
        };
        auto emit = [&](const std::vector<Command>& cs, double t) {
            for (const Command& c : cs) {
                std::printf("CMD %.1f %s %d %d\n", t, c.device.c_str(), c.tier, c.on ? 1 : 0);
                if (p5) {
                    msg::TierCmd m;
                    msg::copy_id(m.device, c.device);
                    m.tier = static_cast<uint8_t>(c.tier);
                    m.on = c.on ? 1 : 0;
                    if (!send_p5(&m, sizeof m)) cmd_fail++;
                }
            }
            std::fflush(stdout);
            return cs.size();
        };
        double last_t = 0;

        FILE* log = nullptr;
        if (!log_path.empty()) {
            log = std::fopen(log_path.c_str(), "w");
            if (!log) throw std::runtime_error("cannot open " + log_path);
            std::fprintf(log, "seq,t,latency_ms,infer_ms,phone,p2,p3,p4,p5,p6");
            if (rf) devices = {proto::kDeviceAirflow, proto::kDeviceDisplay};  // 폰은 행마다 바뀌어 own_phone 열로
            for (const auto& d : devices) std::fprintf(log, ",%s", d.c_str());
            if (rf) std::fprintf(log, ",own_phone");
            std::fprintf(log, "\n");
        }

        // ---- P7 로그: 주기마다 기기별 최종 상태 ----
        ipc::Client p7(msg::kNameP7, msg::kLogTimeoutNs);
        uint64_t log_fail = 0;
        auto log_decision = [&](uint64_t seq, double t, const std::vector<Command>& cs) {
            std::vector<std::string> devs = {proto::kDeviceAirflow, proto::kDeviceDisplay};
            for (const auto& p : phones) devs.push_back(p.id);
            for (const auto& id : rf_phones) devs.push_back(id);
            msg::LogDecision m;
            m.seq = seq;
            m.t = t;
            for (const auto& d : devs) {
                if (m.n >= static_cast<uint32_t>(msg::kMaxDevices)) break;
                msg::LogDecision::Dev& e = m.d[m.n++];
                msg::copy_id(e.device, d);
                e.on = pol.is_on(d) ? 1 : 0;
                e.tier = static_cast<uint8_t>(pol.tier_of(d));
                e.changed = std::any_of(cs.begin(), cs.end(), [&](const Command& c) { return c.device == d; }) ? 1 : 0;
            }
            m.mono_ns = ipc::now_ns();
            if (!p7.send(&m, sizeof m)) log_fail++;
        };

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
                    e.cal_a = cal_of(p.id).a;
                    e.cal_b = cal_of(p.id).b;
                }
                for (const auto& id : rf_phones) {  // PROF가 아직 없으면 PRD 기본 프로필
                    if (rep.n >= static_cast<uint32_t>(msg::kMaxPhones)) break;
                    msg::ProfileEntry& e = rep.e[rep.n++];
                    msg::copy_id(e.id, id);
                    const Profile* pf = pol.profile(id);
                    e.mssq = pf ? pf->mssq : proto::kDefaultMssq;
                    e.sens = pf ? pf->sens : proto::kDefaultSens;
                    e.ev = std::nan("");
                    e.tol = pol.tol(id);
                    e.cal_a = cal_of(id).a;
                    e.cal_b = cal_of(id).b;
                }
                ipc::Server::reply(rcvid, &rep, sizeof rep);
            } else if (type == msg::RISK) {
                const msg::RiskMsg r = in.r;
                ipc::Server::reply(rcvid);
                if (r.seq != expect) gaps += r.seq > expect ? r.seq - expect : 1;
                expect = r.seq + 1;
                std::map<std::string, Risk> risk;
                for (uint32_t i = 0; i < r.n && i < static_cast<uint32_t>(msg::kMaxPhones); i++)
                    risk[id_of(r.id[i])] = r.p[i];
                last_t = r.t;
                const std::vector<Command> cs = pol.on_risk(r.t, risk);
                cmds += emit(cs, r.t);
                log_decision(r.seq, r.t, cs);
                sync();  // 다음 주기의 명부 (P3는 이 회신 뒤에 PROFILES를 묻는다)
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
                        if (rf) std::fprintf(log, ",%d", pol.is_on(id_of(r.id[i])) ? 1 : 0);
                        std::fprintf(log, "\n");
                    }
                    std::fflush(log);
                }
            } else if (type == msg::RIDE_START || type == msg::RIDE_END) {
                ipc::Server::reply(rcvid);
                if (type == msg::RIDE_START) {
                    sync();
                    for (const auto& p : phones) load_cal(p.id);  // 새로 넣은 보정은 다음 주행부터
                    for (const auto& id : rf_phones) load_cal(id);
                    std::fflush(stdout);
                    pol.on_ride_start();
                    cycles = gaps = expect = cmds = log_fail = 0;
                    lat_ms.clear();
                } else {
                    saves += static_cast<uint64_t>(st.save_dirty(pol));
                    std::vector<double> s = lat_ms;
                    std::sort(s.begin(), s.end());
                    std::printf("{\"proc\": \"p4\", \"cycles\": %llu, \"risk_gaps\": %llu, \"commands\": %llu, "
                                "\"files_saved\": %llu, \"log_fail\": %llu, \"latency_ms_median\": %.3f, "
                                "\"latency_ms_max\": %.3f",
                                (unsigned long long)cycles, (unsigned long long)gaps, (unsigned long long)cmds,
                                (unsigned long long)saves, (unsigned long long)log_fail, s.empty() ? 0.0 : s[s.size() / 2],
                                s.empty() ? 0.0 : s.back());
                    if (rf)
                        std::printf(", \"rf_phones\": %zu, \"rf_hud\": %d, \"sync_fail\": %llu, \"tier_cmd_fail\": %llu",
                                    rf_phones.size(), rf_hud ? 1 : 0, (unsigned long long)sync_fail,
                                    (unsigned long long)cmd_fail);
                    std::printf("}\n");
                    std::fflush(stdout);
                    if (once) {
                        if (log) std::fclose(log);
                        return gaps ? 1 : 0;
                    }
                }
            } else if (type == msg::CTX_SCREEN) {
                const msg::CtxScreen m = in.scr;
                ipc::Server::reply(rcvid);
                pol.on_screen(id_of(m.sender), m.on != 0, m.att != 0);
            } else if (type == msg::CTX_OVR) {
                const msg::CtxOvr m = in.ovr;
                ipc::Server::reply(rcvid);
                cmds += emit(pol.on_override(last_t, id_of(m.sender), id_of(m.device), m.on != 0), last_t);
                saves += static_cast<uint64_t>(st.save_dirty(pol));
            } else if (type == msg::CTX_PROF) {
                const msg::CtxProf m = in.prof;
                ipc::Server::reply(rcvid);
                pol.on_profile(id_of(m.phone), m.mssq, m.sens);
                saves += static_cast<uint64_t>(st.save_dirty(pol));
            } else {
                ipc::Server::error(rcvid, EINVAL);
            }
        }
    } catch (const std::exception& ex) {
        std::fprintf(stderr, "p4: %s\n", ex.what());
        return 1;
    }
}
