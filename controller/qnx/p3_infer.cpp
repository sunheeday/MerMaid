// P3 추론: 차량 피처 46개 + 폰마다 승객 피처 → 52개 벡터 → LightGBM 5개 → RISK를 P4로.
//   p3_infer --models DIR [--vehicle-type 0|1] [--once] [--prio P]
// 주기마다 P4에 등록된 폰과 프로필을 묻는다 (PROFILES). 폰이 없으면 기본 프로필(PRD §P3:
// MSSQ 12.90, 민감도 1.0, constants.json default_profile) "default" 한 행만 추론한다 (공용 기기
// Tier 1·2는 그 값으로 동작). 기본 프로필의 EV 경험은 모르므로 NaN, 폰 허용도도 NaN.
// feature_order에서 enabled=false인 칸(v1: svc_conflict, G8 허용도)은 학습 때처럼 NaN.
// 개인 보정: PROFILES 회신의 cal_a, cal_b로 p' = σ(a·logit(p) + b)를 RISK에 적용한다 (calib.hpp, DATA_LOGGING §12-5).
// 기본 프로필과 a=1, b=0은 그대로. LOG_INFER에는 보정 전 확률과 적용한 a, b를 남긴다 (보정을 다시 맞출 때 입력).
// RISK를 보낸 뒤 P7에 LOG_INFER (52칸 입력과 확률, 2 ms 시간 초과). P7이 없거나 느려도 log_fail만 센다.
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <limits>
#include <string>
#include <vector>

#include "calib.hpp"
#include "inference.hpp"
#include "ipc.hpp"
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

int main(int argc, char** argv) {
    std::string models;
    double vtype = 1;
    bool once = false;
    int prio = 20;
    for (int i = 1; i < argc; i++) {
        if (!std::strcmp(argv[i], "--models") && i + 1 < argc) models = argv[++i];
        else if (!std::strcmp(argv[i], "--vehicle-type") && i + 1 < argc) vtype = std::atof(argv[++i]);
        else if (!std::strcmp(argv[i], "--once")) once = true;
        else if (!std::strcmp(argv[i], "--prio") && i + 1 < argc) prio = std::atoi(argv[++i]);
    }
    if (models.empty()) {
        std::fprintf(stderr, "usage: p3_infer --models DIR [--vehicle-type 0|1] [--once] [--prio P]\n");
        return 2;
    }
    try {
        ipc::set_priority(prio, "p3");
        Inference inf(models);  // 피처 수가 다르면 여기서 거부
        std::fprintf(stderr, "p3: models loaded in %.0f ms\n", inf.load_ms());
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
                const std::vector<Risk> P = inf.predict(X.data(), n);
                const uint64_t dt = ipc::now_ns() - t0;
                infer_max_ns = std::max(infer_max_ns, dt);
                msg::RiskMsg out;
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
                if (type == msg::RIDE_START) cycles = prof_fail = risk_fail = log_fail = infer_max_ns = 0;
                if (!p4.send(&e, sizeof e)) std::fprintf(stderr, "p3: ride edge not delivered to p4\n");
                if (type == msg::RIDE_END) {
                    std::printf("{\"proc\": \"p3\", \"cycles\": %llu, \"profiles_fail\": %llu, \"risk_fail\": %llu, "
                                "\"log_fail\": %llu, \"infer_ms_max\": %.3f}\n",
                                (unsigned long long)cycles, (unsigned long long)prof_fail,
                                (unsigned long long)risk_fail, (unsigned long long)log_fail, infer_max_ns / 1e6);
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
