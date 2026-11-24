// P2 피처: P1의 100 Hz 샘플 → 10초마다 차량 피처 46개를 P3로 (Mac featurize와 같은 FeatureExtractor).
//   p2_feat [--once] [--motion] [--prio P] [--xslope [--xslope-window S] [--xslope-log FILE]]
// 샘플은 받자마자 회신해 P1을 풀어 준다. 주행 경계(RIDE_START/END)는 같은 순서로 P3에 넘긴다.
// --motion: 10샘플(100 ms, constants.json mot_period_ms)마다 P6(mermaid_p6)에 MOTION을 보낸다.
//   ax, ay = Wf 가중 가속도, pax, pay = trip 열 pred_ax_10s, pred_ay_10s (docs/controller/RF_MESSAGES.md §5).
//   P6이 없거나 늦으면 20 ms 뒤 포기하고 센다 (피처 계산은 계속).
// GPS 속도 (#18): P6이 앱의 M09 GPS를 받을 때마다 GPS_SPEED를 보낸다. 받자마자 회신하고 보관만 한다(common/gps_speed.hpp).
//   샘플의 speed 칸이 NaN(LIVE)이면 보관한 값으로 채운다 (최근 2초 안 acc 최소, 3초 넘게 없으면 NaN).
//   재생 trip에 speed가 있으면 그대로 둔다. P6 → P2와 P2 → P6(MOTION)의 교착 없음: docs/controller/RF_MESSAGES.md §2-2
//   빠른 재생(p1_acq --speed 2 초과)이면 주행 시각 100 ms가 실제 50 ms보다 짧아진다. BLE는 10 Hz를 넘게 못 나르므로
//   앞 MOTION에서 실제 시계로 반 주기(50 ms)가 안 지났으면 건너뛰고 센다 (mot_skip). 실시간 재생에서는 건너뛰지 않는다.
// --xslope: 좌우 가속도(ay)에서 도로 횡단 경사 같은 '움직임이 아닌 느린 좌우 값'을 GPS 속도로 찾아 뺀다 (기본 꺼짐,
//   common/cross_slope.hpp). 피처와 MOTION이 뺀 값을 쓴다. P7 주행 기록(링)은 P1 값 그대로다.
//   --xslope-log FILE: 1초마다 t, 남는 값, 썼는지, 추정, 뺀 값 (맥 재현 ml/realdata/imu_calib/cross_slope_rt.py와 대조용)
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <memory>

#include "cross_slope.hpp"
#include "gps_speed.hpp"
#include "ipc.hpp"
#include "messages.hpp"
#include "rf_messages.hpp"

using namespace mm;

union Inbox {
    msg::Hdr h;
    msg::Sample s;
    msg::RideEdge e;
    msg::GpsSpeed g;
    _pulse pulse;
    Inbox() {}
};

int main(int argc, char** argv) {
    bool once = false, motion = false, xs_on = false;
    int prio = 30;
    xslope::Config xs_cfg;
    const char* xs_log_path = nullptr;
    for (int i = 1; i < argc; i++) {
        if (!std::strcmp(argv[i], "--once")) once = true;
        else if (!std::strcmp(argv[i], "--motion")) motion = true;
        else if (!std::strcmp(argv[i], "--prio") && i + 1 < argc) prio = std::atoi(argv[++i]);
        else if (!std::strcmp(argv[i], "--xslope")) xs_on = true;
        else if (!std::strcmp(argv[i], "--xslope-window") && i + 1 < argc) xs_cfg.window_s = std::atof(argv[++i]);
        else if (!std::strcmp(argv[i], "--xslope-log") && i + 1 < argc) xs_log_path = argv[++i];
    }
    std::FILE* xs_log = nullptr;
    if (xs_on) {
        std::fprintf(stderr, "p2: cross-slope removal on (window %.0f s, min %d s, straight < %.1f dps, gps acc <= %.0f m)\n",
                     xs_cfg.window_s, xs_cfg.min_n, xs_cfg.straight_dps, xs_cfg.max_acc_m);
        if (xs_log_path) {
            xs_log = std::fopen(xs_log_path, "a");
            if (!xs_log) std::fprintf(stderr, "p2: cannot open %s\n", xs_log_path);
        }
    }
    try {
        ipc::set_priority(prio, "p2");
        ipc::Client p3(msg::kNameP3, 1000000000ULL);
        if (!p3.wait_open(30)) std::fprintf(stderr, "p2: %s not found yet, will retry\n", msg::kNameP3);
        ipc::Client p6(msg::kNameP6, 20 * 1000000ULL);
        const int mot_every = proto::kMotPeriodMs / 10;  // 100 Hz 샘플 수
        const uint64_t mot_min_gap_ns = static_cast<uint64_t>(proto::kMotPeriodMs) * 1000000ULL / 2;
        uint64_t mot_sent = 0, mot_fail = 0, mot_skip = 0, mot_last_ns = 0;
        ipc::Server srv(msg::kNameP2);  // P3에 연결된 뒤에 이름을 붙인다
        auto fx = std::make_unique<FeatureExtractor>();
        Inbox in;
        uint64_t expect = 0, received = 0, gaps = 0, veh = 0, veh_fail = 0;
        uint64_t push_max_ns = 0;
        gps::SpeedHold gps_hold;  // 주행이 바뀌어도 유지 (GPS는 주행과 상관없이 온다)
        uint64_t gps_rx = 0, gps_filled = 0;
        auto xs = std::make_unique<xslope::CrossSlope>(xs_cfg);
        uint64_t ride_no = 0;
        for (;;) {
            const int rcvid = srv.receive(&in, sizeof in);
            const uint16_t type = in.h.type;
            if (type == msg::SAMPLE) {
                msg::Sample s = in.s;
                ipc::Server::reply(rcvid);
                double gps_acc = NAN;  // 재생 trip이 준 속도는 정확도를 모름 (NaN = 거르지 않음)
                if (std::isnan(s.row[proto::C_SPEED])) {
                    const uint64_t now = ipc::now_ns();
                    s.row[proto::C_SPEED] = gps::fill_speed(s.row[proto::C_SPEED], gps_hold, now);
                    if (!std::isnan(s.row[proto::C_SPEED])) {
                        gps_filled++;
                        gps_acc = gps_hold.accuracy(now);
                    }
                }
                if (xs_on) {
                    s.row[proto::C_AY] -= xs->push(s.row[proto::C_T], s.row[proto::C_AY], s.row[proto::C_GZ],
                                                   s.row[proto::C_SPEED], gps_acc);
                    xslope::Second sec;
                    if (xs_log && xs->take_second(sec))
                        std::fprintf(xs_log, "%llu,%.2f,%.5f,%d,%.5f,%.5f\n", (unsigned long long)ride_no, sec.t, sec.resid,
                                     sec.use ? 1 : 0, sec.est, sec.applied);
                }
                if (s.seq != expect) gaps += s.seq > expect ? s.seq - expect : 1;
                expect = s.seq + 1;
                received++;
                const uint64_t t0 = ipc::now_ns();
                const bool emit = fx->push(s.row);
                push_max_ns = std::max(push_max_ns, ipc::now_ns() - t0);
                if (motion && received % mot_every == 0 && mot_last_ns && t0 - mot_last_ns < mot_min_gap_ns) {
                    mot_skip++;
                } else if (motion && received % mot_every == 0) {
                    mot_last_ns = t0;
                    msg::Motion m;
                    m.t = s.row[proto::C_T];
                    m.ax = fx->weighted_ax();
                    m.ay = fx->weighted_ay();
                    m.pax = s.row[proto::C_PRED_AX_10S];
                    m.pay = s.row[proto::C_PRED_AY_10S];
                    if (p6.send(&m, sizeof m)) mot_sent++;
                    else mot_fail++;
                }
                if (emit) {
                    msg::Veh v;
                    v.seq = veh++;
                    v.t = fx->time();
                    v.x = fx->vehicle();
                    v.t_emit_ns = ipc::now_ns();
                    if (!p3.send(&v, sizeof v)) veh_fail++;
                }
            } else if (type == msg::GPS_SPEED) {
                const msg::GpsSpeed g = in.g;
                ipc::Server::reply(rcvid);
                char phone[msg::kIdLen];
                std::memcpy(phone, g.phone, sizeof phone);
                phone[msg::kIdLen - 1] = 0;
                gps_hold.put(phone, g.mono_ns, g.spd, g.acc);
                gps_rx++;
            } else if (type == msg::RIDE_START || type == msg::RIDE_END) {
                const msg::RideEdge e = in.e;
                ipc::Server::reply(rcvid);
                if (type == msg::RIDE_START) {
                    fx = std::make_unique<FeatureExtractor>();
                    expect = received = gaps = veh = veh_fail = push_max_ns = mot_sent = mot_fail = mot_skip = mot_last_ns = 0;
                    gps_rx = gps_filled = 0;
                    xs = std::make_unique<xslope::CrossSlope>(xs_cfg);  // 주행마다 새로 (재시작 뒤 경사로일 수 있음)
                    ride_no++;
                    if (xs_log) {
                        std::fprintf(xs_log, "ride,t,resid,use,est,applied\n");
                        std::fflush(xs_log);
                    }
                }
                if (!p3.send(&e, sizeof e)) std::fprintf(stderr, "p2: ride edge not delivered to p3\n");
                if (type == msg::RIDE_END) {
                    std::printf("{\"proc\": \"p2\", \"p1_sent\": %llu, \"received\": %llu, \"seq_gaps\": %llu, "
                                "\"veh_sent\": %llu, \"veh_fail\": %llu, \"push_us_max\": %.1f",
                                (unsigned long long)e.samples, (unsigned long long)received, (unsigned long long)gaps,
                                (unsigned long long)veh, (unsigned long long)veh_fail, push_max_ns / 1e3);
                    if (motion)
                        std::printf(", \"mot_sent\": %llu, \"mot_fail\": %llu, \"mot_skip\": %llu",
                                    (unsigned long long)mot_sent, (unsigned long long)mot_fail,
                                    (unsigned long long)mot_skip);
                    if (gps_rx)
                        std::printf(", \"gps_rx\": %llu, \"gps_filled\": %llu", (unsigned long long)gps_rx,
                                    (unsigned long long)gps_filled);
                    if (xs_on) {
                        std::printf(", \"xslope_s\": %llu, \"xslope_used_s\": %llu, \"xslope_last\": %.4f",
                                    (unsigned long long)xs->seconds(), (unsigned long long)xs->used_seconds(), xs->applied());
                        if (xs_log) std::fflush(xs_log);
                    }
                    std::printf("}\n");
                    std::fflush(stdout);
                    if (once) return (gaps || veh_fail || received != e.samples) ? 1 : 0;
                }
            } else {
                ipc::Server::error(rcvid, EINVAL);
            }
        }
    } catch (const std::exception& ex) {
        std::fprintf(stderr, "p2: %s\n", ex.what());
        return 1;
    }
}
