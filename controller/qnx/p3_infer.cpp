// P3 추론: 차량 피처 46개 + 폰마다 승객 피처 → 52개 벡터 → 모델 → RISK를 P4로.
//   p3_infer --models DIR [--ctx-state DIR] [--vehicle-type 0|1] [--once] [--prio P]
// 모델 종류는 DIR/models.json의 "kind"로 고른다 (없으면 V1):
//   misc5 (V1)   LightGBM 5개 → P(MISC>=2..6), 단조 보정, 개인 보정. RISK subtype RISK_MISC5
//   ctx_gru (V3) 문맥 모델 MLP 요약 + GRU (ctx_model.hpp) → [P(fan), P(ambient), P(dots), NaN, NaN]. RISK subtype RISK_RESPONSE3
//                --ctx-state 아래에 승객별 과거 토큰(ctx_history.hpp). 승객이 주행에 처음 보일 때 그 토큰으로 GRU 상태 h를
//                한 번 계산하고(문맥은 지난 주행들의 것이라 주행 안에서는 안 바뀐다), 10초마다 요약 e와 h로 확률을 낸다.
//                주행 끝(RIDE_END를 P4에 넘긴 뒤) P4에 TOKENS를 물어 고른 주기의 e로 토큰을 만들어 붙인다.
//                "default"(폰 없음) 행은 문맥 없이(h = 0) 추론하고 토큰을 남기지 않는다. 개인 보정은 MISC용이라 쓰지 않는다.
// 주기마다 P4에 등록된 폰과 프로필을 묻는다 (PROFILES). 폰이 없으면 기본 프로필(PRD §P3:
// MSSQ 12.90, 민감도 1.0, constants.json default_profile) "default" 한 행만 추론한다 (공용 기기
// Tier 1·2는 그 값으로 동작). 기본 프로필의 EV 경험은 모르므로 NaN, 폰 허용도도 NaN.
// feature_order에서 enabled=false인 칸(v1: svc_conflict, G8 허용도)은 학습 때처럼 NaN.
// 개인 보정: PROFILES 회신의 cal_a, cal_b로 p' = σ(a·logit(p) + b)를 RISK에 적용한다 (calib.hpp, DATA_LOGGING §12-5).
// 기본 프로필과 a=1, b=0은 그대로. LOG_INFER에는 보정 전 확률과 적용한 a, b를 남긴다 (보정을 다시 맞출 때 입력).
// RISK를 보낸 뒤 P7에 LOG_INFER (52칸 입력과 확률, 2 ms 시간 초과). P7이 없거나 느려도 log_fail만 센다.
#include <cerrno>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <limits>
#include <string>
#include <vector>

#include <algorithm>
#include <map>
#include <memory>

#include "calib.hpp"
#include "ctx_history.hpp"
#include "ctx_model.hpp"
#include "ipc.hpp"
#include "json_min.hpp"
#include "store.hpp"
#ifdef MM_HAVE_LIGHTGBM
#include "inference.hpp"
#endif
#include "log_messages.hpp"
#include "messages.hpp"

using namespace mm;

union Inbox {
    msg::Hdr h;
    msg::Veh v;
    msg::RideEdge e;
    _pulse pulse;
    Inbox() {}
};

static std::string model_kind(const std::string& dir) {
    std::string text;
    if (!store::read_file(dir + "/models.json", text)) throw std::runtime_error("cannot read " + dir + "/models.json");
    const json::Value v = json::parse(text);
    return v.has("kind") ? v.at("kind").string() : "misc5";
}

int main(int argc, char** argv) {
    std::string models, ctx_state;
    double vtype = 1;
    bool once = false;
    int prio = 20;
    for (int i = 1; i < argc; i++) {
        if (!std::strcmp(argv[i], "--models") && i + 1 < argc) models = argv[++i];
        else if (!std::strcmp(argv[i], "--ctx-state") && i + 1 < argc) ctx_state = argv[++i];
        else if (!std::strcmp(argv[i], "--vehicle-type") && i + 1 < argc) vtype = std::atof(argv[++i]);
        else if (!std::strcmp(argv[i], "--once")) once = true;
        else if (!std::strcmp(argv[i], "--prio") && i + 1 < argc) prio = std::atoi(argv[++i]);
    }
    if (models.empty()) {
        std::fprintf(stderr, "usage: p3_infer --models DIR [--ctx-state DIR] [--vehicle-type 0|1] [--once] [--prio P]\n");
        return 2;
    }
    try {
        ipc::set_priority(prio, "p3");
        const std::string kind = model_kind(models);
        const bool ctx = kind == "ctx_gru";
#ifdef MM_HAVE_LIGHTGBM
        std::unique_ptr<Inference> inf;
#endif
        std::unique_ptr<CtxModel> cm;
        std::unique_ptr<ctx::History> hist;
        if (ctx) {
            if (ctx_state.empty()) throw std::runtime_error("a ctx_gru model needs --ctx-state DIR");
            const uint64_t t_load = ipc::now_ns();
            cm = std::make_unique<CtxModel>(models);  // 입력 열이 protocol과 다르면 여기서 거부
            hist = std::make_unique<ctx::History>(ctx_state, cm->summary_dim());
            std::fprintf(stderr, "p3: ctx_gru model loaded in %.1f ms (%d inputs, k %d), history %s\n",
                         (ipc::now_ns() - t_load) / 1e6, cm->n_inputs(), cm->k(), ctx_state.c_str());
        } else if (kind == "misc5") {
#ifdef MM_HAVE_LIGHTGBM
            inf = std::make_unique<Inference>(models);  // 피처 수가 다르면 여기서 거부
            std::fprintf(stderr, "p3: models loaded in %.0f ms\n", inf->load_ms());
#else
            throw std::runtime_error("this p3 was built without LightGBM: only ctx_gru models");
#endif
        } else {
            throw std::runtime_error("unknown model kind " + kind);
        }
        // V3 주행 상태: 승객별 GRU 상태 h, 주기별 요약 e (주행 끝 토큰용)
        std::map<std::string, std::vector<float>> h_of;
        std::map<std::string, std::map<uint64_t, std::vector<float>>> e_of;
        uint64_t ctx_hist_ns_max = 0, tokens_added = 0, tokens_fail = 0;
        auto h_for = [&](const std::string& id) -> const std::vector<float>& {
            auto it = h_of.find(id);
            if (it != h_of.end()) return it->second;
            std::vector<float> h(static_cast<size_t>(cm->hidden_dim()), 0.f);
            if (id != "default" && store::safe_name(id)) {
                const uint64_t t0 = ipc::now_ns();
                int n = 0;
                const std::vector<float> c = hist->context(hist->load(id), cm->k(), &n);
                cm->history(c.data(), n, h.data());
                ctx_hist_ns_max = std::max(ctx_hist_ns_max, ipc::now_ns() - t0);
                std::fprintf(stderr, "p3: %s history %d tokens\n", id.c_str(), n);
            }
            return h_of.emplace(id, std::move(h)).first->second;
        };
        ipc::Client p4(msg::kNameP4, 1000000000ULL);
        if (!p4.wait_open(30)) std::fprintf(stderr, "p3: %s not found yet, will retry\n", msg::kNameP4);
        ipc::Client p7(msg::kNameP7, msg::kLogTimeoutNs);
        const std::string model_version = msg::model_version_from_path(models);
        static msg::LogInfer lg;  // 6.7 KB
        ipc::Server srv(msg::kNameP3);  // 모델 로드와 P4 연결이 끝난 뒤에 이름을 붙인다
        const double nan = std::numeric_limits<double>::quiet_NaN();
        Inbox in;
        msg::ProfilesReply prof, last;  // P4에 못 물으면 마지막으로 받은 목록
        uint64_t cycles = 0, prof_fail = 0, risk_fail = 0, log_fail = 0, infer_max_ns = 0;
        std::vector<double> X(static_cast<size_t>(msg::kMaxPhones) * proto::kNumFeatures);
        calib::Calib cal[msg::kMaxPhones];
        for (;;) {
            const int rcvid = srv.receive(&in, sizeof in);
            const uint16_t type = in.h.type;
            if (type == msg::VEH) {
                const msg::Veh v = in.v;
                ipc::Server::reply(rcvid);
                msg::ProfilesReq req;
                if (p4.send(&req, sizeof req, &prof, sizeof prof)) last = prof;
                else prof_fail++;
                const msg::ProfilesReply& pr = last;
                const int n = pr.n == 0 ? 1 : static_cast<int>(std::min<uint32_t>(pr.n, msg::kMaxPhones));
                const uint64_t t0 = ipc::now_ns();
                for (int r = 0; r < n; r++) {
                    double* x = &X[static_cast<size_t>(r) * proto::kNumFeatures];
                    std::copy(v.x.begin(), v.x.end(), x);
                    const bool have = pr.n > 0;
                    x[proto::F_TOL_DISPLAY] = pr.tol_display;
                    x[proto::F_TOL_PHONE] = have ? pr.e[r].tol : nan;
                    x[proto::F_MSSQ] = have ? pr.e[r].mssq : proto::kDefaultMssq;
                    x[proto::F_SENSITIVITY] = have ? pr.e[r].sens : proto::kDefaultSens;
                    x[proto::F_EV_EXPERIENCE] = have ? pr.e[r].ev : nan;
                    x[proto::F_VEHICLE_TYPE] = vtype;
                    cal[r] = have ? calib::Calib{pr.e[r].cal_a, pr.e[r].cal_b} : calib::Calib{};
                    if (!calib::valid(cal[r])) cal[r] = {};  // P4가 거른 값이지만 한 번 더
                    for (int f = 0; f < proto::kNumFeatures; f++)
                        if (!proto::kFeatureEnabled[f]) x[f] = nan;
                }
                std::vector<Risk> P(static_cast<size_t>(n));
                if (ctx) {
                    const double nanv = std::numeric_limits<double>::quiet_NaN();
                    std::vector<float> e(static_cast<size_t>(cm->summary_dim()));
                    for (int r = 0; r < n; r++) {
                        const std::string id = pr.n > 0 ? std::string(pr.e[r].id, strnlen(pr.e[r].id, msg::kIdLen)) : "default";
                        cm->summarize(&X[static_cast<size_t>(r) * proto::kNumFeatures], e.data());
                        float p[CtxModel::kHeads];
                        cm->predict(e.data(), h_for(id).data(), p);
                        P[static_cast<size_t>(r)] = {p[0], p[1], p[2], nanv, nanv};
                        if (id != "default") e_of[id][v.seq] = e;
                        cal[r] = {};  // 개인 보정은 MISC 확률용
                    }
                } else {
#ifdef MM_HAVE_LIGHTGBM
                    P = inf->predict(X.data(), n);
#endif
                }
                const uint64_t dt = ipc::now_ns() - t0;
                infer_max_ns = std::max(infer_max_ns, dt);
                msg::RiskMsg out;
                out.h.subtype = ctx ? msg::RISK_RESPONSE3 : msg::RISK_MISC5;
                out.seq = v.seq;
                out.t = v.t;
                out.t_emit_ns = v.t_emit_ns;
                out.t_infer_ns = dt;
                out.n = static_cast<uint32_t>(n);
                for (int r = 0; r < n; r++) {
                    if (pr.n > 0) std::memcpy(out.id[r], pr.e[r].id, msg::kIdLen);
                    else msg::copy_id(out.id[r], "default");
                    out.p[r] = calib::apply(P[static_cast<size_t>(r)], cal[r]);
                }
                if (!p4.send(&out, sizeof out)) risk_fail++;
                lg = msg::LogInfer{};
                lg.seq = v.seq;
                lg.t = v.t;
                msg::copy_text(lg.model, model_version);
                lg.n = out.n;
                for (int r = 0; r < n; r++) {
                    std::memcpy(lg.id[r], out.id[r], msg::kIdLen);
                    std::memcpy(lg.x[r], &X[static_cast<size_t>(r) * proto::kNumFeatures], sizeof lg.x[r]);
                    lg.p[r] = P[static_cast<size_t>(r)];  // 보정 전
                    lg.cal_a[r] = cal[r].a;
                    lg.cal_b[r] = cal[r].b;
                }
                lg.mono_ns = ipc::now_ns();
                if (!p7.send(&lg, sizeof lg)) log_fail++;
                cycles++;
            } else if (type == msg::RIDE_START || type == msg::RIDE_END) {
                const msg::RideEdge e = in.e;
                ipc::Server::reply(rcvid);
                if (type == msg::RIDE_START) {
                    cycles = prof_fail = risk_fail = log_fail = infer_max_ns = 0;
                    ctx_hist_ns_max = tokens_added = tokens_fail = 0;
                    h_of.clear();  // 지난 주행에서 붙인 토큰까지 넣어 승객이 처음 보일 때 다시 계산
                    e_of.clear();
                }
                if (!p4.send(&e, sizeof e)) std::fprintf(stderr, "p3: ride edge not delivered to p4\n");
                if (type == msg::RIDE_END && ctx) {
                    // P4가 고른 주기·장치·라벨로 토큰을 만들어 승객별 파일에 붙인다
                    static msg::TokensReply tr;
                    msg::TokensReq req;
                    tr = msg::TokensReply{};
                    if (!p4.send(&req, sizeof req, &tr, sizeof tr, 5000000000ULL)) {  // 주행 끝 한 번: 늦어도 기다린다
                        tokens_fail++;
                        std::fprintf(stderr, "p3: TOKENS not answered by p4 (%s), no history added\n", std::strerror(errno));
                    } else {
                        std::map<std::string, std::vector<ctx::Token>> add;
                        for (uint32_t i = 0; i < tr.n && i < static_cast<uint32_t>(msg::kMaxTokens); i++) {
                            const auto& te = tr.e[i];
                            const std::string id(te.id, strnlen(te.id, msg::kIdLen));
                            const auto pe = e_of.find(id);
                            if (pe == e_of.end()) { tokens_fail++; continue; }
                            const auto ce = pe->second.find(te.seq);
                            if (ce == pe->second.end() || te.device >= CtxModel::kHeads || te.label > 1) { tokens_fail++; continue; }
                            add[id].push_back({0, te.device, te.label, ce->second});
                        }
                        for (const auto& kv : add) {
                            try {
                                hist->append(kv.first, kv.second);
                                tokens_added += kv.second.size();
                            } catch (const std::exception& ex) {
                                tokens_fail += kv.second.size();
                                std::fprintf(stderr, "p3: cannot save history for %s: %s\n", kv.first.c_str(), ex.what());
                            }
                        }
                    }
                }
                if (type == msg::RIDE_END) {
                    std::printf("{\"proc\": \"p3\", \"kind\": \"%s\", \"cycles\": %llu, \"profiles_fail\": %llu, \"risk_fail\": %llu, "
                                "\"log_fail\": %llu, \"infer_ms_max\": %.3f",
                                kind.c_str(), (unsigned long long)cycles, (unsigned long long)prof_fail,
                                (unsigned long long)risk_fail, (unsigned long long)log_fail, infer_max_ns / 1e6);
                    if (ctx)
                        std::printf(", \"history_ms_max\": %.3f, \"tokens_added\": %llu, \"tokens_fail\": %llu",
                                    ctx_hist_ns_max / 1e6, (unsigned long long)tokens_added, (unsigned long long)tokens_fail);
                    std::printf("}\n");
                    std::fflush(stdout);
                    if (once) return (prof_fail || risk_fail) ? 1 : 0;
                }
            } else {
                ipc::Server::error(rcvid, EINVAL);
            }
        }
    } catch (const std::exception& ex) {
        std::fprintf(stderr, "p3: %s\n", ex.what());
        return 1;
    }
}
