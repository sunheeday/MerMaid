#include "features.hpp"

#include <algorithm>
#include <cmath>
#include <limits>

namespace mm {
using namespace proto;

namespace {
constexpr double NaN = std::numeric_limits<double>::quiet_NaN();
constexpr size_t W10S = 1000, W1M = 6000, W5M = 30000;  // 100 Hz 기준 샘플 수
constexpr double kStopSpeed = 0.1;                       // m/s, 정차로 보는 속도

size_t avail(const Ring& r, size_t w) { return std::min(r.size(), w); }

// NaN을 건너뛴 평균, 제곱평균, 표준편차, 최댓값
struct Stats {
    double sum = 0, sum2 = 0, maxv = -std::numeric_limits<double>::infinity();
    size_t n = 0;
};
Stats stats(const Ring& r, size_t w) {
    Stats s;
    for (size_t i = 0, m = avail(r, w); i < m; i++) {
        const double x = r.back(i);
        if (std::isnan(x)) continue;
        s.sum += x; s.sum2 += x * x; s.maxv = std::max(s.maxv, x); s.n++;
    }
    return s;
}
double mean(const Ring& r, size_t w) { auto s = stats(r, w); return s.n ? s.sum / s.n : NaN; }
double rms(const Ring& r, size_t w) { auto s = stats(r, w); return s.n ? std::sqrt(s.sum2 / s.n) : NaN; }
double stdev(const Ring& r, size_t w) {
    auto s = stats(r, w);
    if (!s.n) return NaN;
    const double m = s.sum / s.n;
    return std::sqrt(std::max(0.0, s.sum2 / s.n - m * m));
}
double maxv(const Ring& r, size_t w) { auto s = stats(r, w); return s.n ? s.maxv : NaN; }

// 음(sign<0) 또는 양(sign>0) 구간의 에너지를 창 전체 길이로 나눈 RMS
double semi_rms(const Ring& r, size_t w, int sign) {
    const size_t m = avail(r, w);
    if (!m) return NaN;
    double s2 = 0;
    for (size_t i = 0; i < m; i++) {
        const double x = r.back(i);
        if ((sign < 0 && x < 0) || (sign > 0 && x > 0)) s2 += x * x;
    }
    return std::sqrt(s2 / m);
}
}  // namespace

FeatureExtractor::FeatureExtractor(double oman_k)
    : dt_(1.0 / kSampleRateHz),
      wfx_(kSampleRateHz, kWf_f1, kWf_f2, kWf_q1, kWf_f4, kWf_q4, kWf_f5, kWf_q5, kWf_f6, kWf_q6),
      wfy_(wfx_), wfz_(wfx_),
      lpx_(butter2_lowpass(kOmanInputLowpassHz, kSampleRateHz)), lpy_(lpx_),
      oman_(oman_k, kOmanBetaFast, kOmanBetaSlow, kOmanExponent, 1.0 / kSampleRateHz) {
    for (auto& c : ch_) c = Ring(W5M);
    reset();
}

void FeatureExtractor::reset() {
    wfx_.reset(); wfy_.reset(); wfz_.reset();
    lpx_.reset(); lpy_.reset();
    oman_.reset();
    for (auto& c : ch_) c.clear();
    has_prev_ = false;
    n_ = 0;
    t0_ = t_ = 0;
    awx_ = awy_ = awz_ = 0;
    msdv2_ = 0;
    msdv_hist_.clear(); score_hist_.clear();
    events_.clear();
    in_accel_ = in_brake_ = in_turn_ = false;
    still_time_ = rest_end_ = 0;
    rest_ = reexp_ = had_rest_ = false;
    out_.fill(NaN);
}

bool FeatureExtractor::push(const Row& r) {
    t_ = r[C_T];
    if (n_ == 0) {
        t0_ = t_;
        // 첫 샘플의 정상상태에서 시작 (T-22). 0에서 시작하면 중력 9.81이 계단 입력이 되어
        // Wf 고역통과가 1분 가까이 울리고 rms_z_1m이 1 m/s^2 안팎까지 튄다.
        wfx_.prime(r[C_AX]); wfy_.prime(r[C_AY]); wfz_.prime(r[C_AZ]);
        lpx_.prime(r[C_AX]); lpy_.prime(r[C_AY]);
    }

    // 가중 가속도, jerk
    const double wx = wfx_.step(r[C_AX]), wy = wfy_.step(r[C_AY]), wz = wfz_.step(r[C_AZ]);
    const double jx = has_prev_ ? (wx - awx_) / dt_ : 0.0;
    const double jy = has_prev_ ? (wy - awy_) / dt_ : 0.0;
    awx_ = wx; awy_ = wy; awz_ = wz;

    // MSDV: 수평 합성 가중 가속도
    const double h = std::hypot(wx, wy);
    msdv2_ += h * h * dt_;

    // Oman: 가중 전 수평 가속도, 0.63 Hz 저역통과 후 크기
    const double lx = lpx_.step(r[C_AX]), ly = lpy_.step(r[C_AY]);
    oman_.step(std::hypot(lx, ly));

    // 이벤트: 저역통과 가속도가 기준을 넘는 순간. 기준에서 kEventRearmMargin만큼 되돌아와야 다시 센다
    // (히스테리시스, feature_order v3). 기준 근처에서 오르내리는 제동 하나를 여러 번 세지 않게.
    const double ay_abs = std::fabs(ly);
    if (!in_accel_ && lx > kHardAccelX) { in_accel_ = true; events_.push_back({t_, 0}); }
    else if (in_accel_ && lx < kHardAccelX - kEventRearmMargin) in_accel_ = false;
    if (!in_brake_ && lx < kHardBrakeX) { in_brake_ = true; events_.push_back({t_, 1}); }
    else if (in_brake_ && lx > kHardBrakeX + kEventRearmMargin) in_brake_ = false;
    if (!in_turn_ && ay_abs > kHardTurnAbsY) { in_turn_ = true; events_.push_back({t_, 2}); }
    else if (in_turn_ && ay_abs < kHardTurnAbsY - kEventRearmMargin) in_turn_ = false;
    while (!events_.empty() && events_.front().t <= t_ - 300.0) events_.pop_front();

    const double v = r[C_SPEED];

    ch_[AWX].push(wx); ch_[AWY].push(wy); ch_[AWZ].push(wz);
    ch_[JX].push(jx); ch_[JY].push(jy); ch_[GZ].push(r[C_GZ]);
    ch_[RAX].push(r[C_AX]); ch_[RAZ].push(r[C_AZ]);
    ch_[SPEED].push(v);

    // 휴식: 차속이 있으면 정차, 없으면 수평 가중 가속도가 작은 상태가 3분
    const bool still = std::isnan(v) ? (h < kRestStillAccel) : (v < kStopSpeed);
    still_time_ = still ? still_time_ + dt_ : 0.0;
    if (!rest_ && still_time_ >= kRestStillTimeS) rest_ = true;
    if (rest_ && !still) { rest_ = false; rest_end_ = t_; reexp_ = true; had_rest_ = true; }

    last_ = r;
    has_prev_ = true;
    n_++;
    if (n_ % static_cast<long>(kFeaturePeriodS * kSampleRateHz) != 0) return false;
    emit();
    return true;
}

void FeatureExtractor::emit() {
    VehicleVec& o = out_;
    o.fill(NaN);
    const Ring* acc[3] = {&ch_[AWX], &ch_[AWY], &ch_[AWZ]};
    const size_t W[3] = {W10S, W1M, W5M};

    // ---- G1 ----
    for (int a = 0; a < 3; a++)
        for (int w = 0; w < 3; w++) o[F_RMS_X_10S + a * 3 + w] = rms(*acc[a], W[w]);
    o[F_DECEL_RMS_X_1M] = semi_rms(ch_[AWX], W1M, -1);
    o[F_DECEL_RMS_X_5M] = semi_rms(ch_[AWX], W5M, -1);
    o[F_ACCEL_RMS_X_1M] = semi_rms(ch_[AWX], W1M, +1);
    o[F_ACCEL_RMS_X_5M] = semi_rms(ch_[AWX], W5M, +1);
    o[F_JERK_RMS_X_10S] = rms(ch_[JX], W10S);
    o[F_JERK_RMS_X_1M] = rms(ch_[JX], W1M);
    o[F_JERK_RMS_Y_10S] = rms(ch_[JY], W10S);
    o[F_JERK_RMS_Y_1M] = rms(ch_[JY], W1M);
    o[F_YAW_RMS_10S] = rms(ch_[GZ], W10S);
    o[F_YAW_RMS_1M] = rms(ch_[GZ], W1M);

    int cnt[3][2] = {};
    std::vector<double> times;
    for (const auto& e : events_) {
        cnt[e.type][1]++;
        if (e.t > t_ - 60.0) cnt[e.type][0]++;
        times.push_back(e.t);
    }
    o[F_HARD_ACCEL_COUNT_1M] = cnt[0][0]; o[F_HARD_ACCEL_COUNT_5M] = cnt[0][1];
    o[F_HARD_BRAKE_COUNT_1M] = cnt[1][0]; o[F_HARD_BRAKE_COUNT_5M] = cnt[1][1];
    o[F_HARD_TURN_COUNT_1M] = cnt[2][0];  o[F_HARD_TURN_COUNT_5M] = cnt[2][1];
    if (times.size() >= 2) {
        std::sort(times.begin(), times.end());
        double s = 0, s2 = 0;
        const size_t k = times.size() - 1;
        for (size_t i = 0; i < k; i++) { const double d = times[i + 1] - times[i]; s += d; s2 += d * d; }
        const double m = s / k;
        o[F_EVENT_IRREGULARITY_5M] = m > 0 ? std::sqrt(std::max(0.0, s2 / k - m * m)) / m : NaN;
    }
    o[F_PITCH_MEAN_1M] = std::atan2(mean(ch_[RAX], W1M), mean(ch_[RAZ], W1M));

    const double msdv = std::sqrt(msdv2_);
    const double score = oman_.score();
    const size_t k = msdv_hist_.size();  // 지금까지 낸 벡터 수
    o[F_MSDV] = msdv;
    o[F_MSDV_INC_5M] = msdv - (k >= 30 ? msdv_hist_[k - 30] : 0.0);
    o[F_OMAN_SCORE] = score;
    o[F_OMAN_DELTA_2M] = score - (k >= 12 ? score_hist_[k - 12] : 0.0);
    o[F_SENSITIZATION] = oman_.S;
    // F_SVC_CONFLICT: v1 비활성 (NaN)
    msdv_hist_.push_back(msdv);
    score_hist_.push_back(score);

    const Row& r = last_;
    // ---- G2 ----
    if (!std::isnan(r[C_SPEED])) {
        o[F_SPEED_MEAN_10S] = mean(ch_[SPEED], W10S);
        o[F_SPEED_MEAN_1M] = mean(ch_[SPEED], W1M);
        o[F_SPEED_MEAN_5M] = mean(ch_[SPEED], W5M);
        o[F_SPEED_STD_1M] = stdev(ch_[SPEED], W1M);
        o[F_SPEED_STD_5M] = stdev(ch_[SPEED], W5M);
        size_t stop = 0, m = avail(ch_[SPEED], W5M);
        for (size_t i = 0; i < m; i++) stop += ch_[SPEED].back(i) < kStopSpeed;
        o[F_STOP_RATIO_5M] = m ? double(stop) / m : NaN;
        o[F_SPEED_MAX_5M] = maxv(ch_[SPEED], W5M);
    }
    // ---- G5, G6: 현재 값 ----
    o[F_CABIN_TEMP] = r[C_CABIN_TEMP];
    o[F_FAN_LEVEL] = r[C_FAN_LEVEL];
    o[F_SEAT_ROW] = r[C_SEAT_ROW];
    // ---- G9 ----
    o[F_RIDE_TIME] = t_ - t0_;
    o[F_REEXPOSURE] = reexp_ ? 1.0 : 0.0;
    o[F_TIME_SINCE_REST] = had_rest_ ? t_ - rest_end_ : NaN;

    // enabled=false는 항상 NaN
    for (int i = 0; i < kNumVehicle; i++)
        if (!kFeatureEnabled[i]) o[i] = NaN;
}

}  // namespace mm
