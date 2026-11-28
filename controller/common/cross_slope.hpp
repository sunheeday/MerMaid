// P2 실시간 횡단 경사 제거 (선택, p2_feat --xslope). 설계: docs/controller/FEATURES.md 끝 "횡단 경사 제거"
//
// 왜: 학습 데이터(ml/simulator/sim.py)의 좌우 가속도는 원심 가속 v²κ + 차선 흔들림 + 진동 + 잡음뿐이고,
//     자이로 gz와 짝이 맞게 만들어져 ay − v·gz 의 느린 성분이 0이다. 실차 ay에는 도로 횡단 경사(배수 기울기)의 중력이
//     늘 +0.2~0.4 m/s² 더 들어와 모델이 "계속 한쪽으로 쏠린다"고 본다. 그 느린 성분을 GPS 속도로 찾아 뺀다.
//
// 1초(100샘플)마다:
//     ay1, gz1  = lag초 전 1초의 IMU 평균 (GPS 속도는 IMU보다 약 0.8초 늦게 온다)
//     v1, acc1  = 이번 1초의 GPS 속도·정확도 평균 (1초 내내 속도가 있어야 함. 정확도 NaN = 모름 = 통과)
//     쓸 초     = v1 있음, acc1 <= max_acc, |gz1| < straight (직진·정차만: 회전 중에는 v·gz 오차와 차체 롤이 섞임)
//     남는 값   = ay1 − v1·gz1 → 지난 window초 버퍼
//     추정      = 버퍼 안의 쓸 초가 min_n 이상이면 그 중앙값, 아니면 이전 추정 유지 (처음엔 0)
// 샘플마다: 뺄 값이 추정 쪽으로 샘플당 rate/100 까지만 움직인다 (계단 없이). ay ← ay − 뺄 값
//
// ax(오르막 중력은 학습 데이터에도 있음), az, 자이로는 건드리지 않는다.
// 맥 재현: ml/realdata/imu_calib/cross_slope_rt.py (같은 계산)
#pragma once
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <vector>

namespace mm::xslope {

constexpr double kPi = 3.14159265358979323846;

struct Config {
    double window_s = 30;      // 지난 몇 초를 보나 (11-23 주행 비교: 30초가 시뮬레이터 정의에 가장 가까움)
    int min_n = 10;            // 창 안에 쓸 초가 이보다 적으면 이전 추정 유지
    double straight_dps = 1.0; // 직진으로 볼 |gz| 상한 [°/s]
    double max_acc_m = 10.0;   // 쓸 GPS 수평 정확도 상한 [m]
    double rate = 0.1;         // 뺄 값의 최대 변화 [m/s² / 초]
    double lag_s = 0.8;        // GPS가 IMU보다 늦은 시간 [초]
};

// 1초마다 한 줄 (p2 --xslope-log)
struct Second {
    double t = 0;        // 이 초 마지막 샘플의 t (주행 시각)
    double resid = NAN;  // ay1 − v1·gz1 (못 구하면 NaN)
    bool use = false;
    double est = NAN;    // 지금 추정 (아직 없으면 NaN)
    double applied = 0;  // 이 초 끝에서 뺀 값 (이 초의 마지막 샘플 갱신 전)
};

class CrossSlope {
public:
    explicit CrossSlope(Config c = {}) : c_(c) {
        delay_ = std::max(0, static_cast<int>(std::lround(c_.lag_s * 100)));
        win_ = std::max(1, static_cast<int>(std::lround(c_.window_s)));
        dly_ay_.assign(static_cast<size_t>(delay_) + 1, 0.0);
        dly_gz_.assign(static_cast<size_t>(delay_) + 1, 0.0);
        step_ = c_.rate / 100.0;
    }

    // 100 Hz 샘플 하나. spd [m/s], acc [m]는 NaN이면 모름. 돌려줌: 이 샘플의 ay에서 뺄 값 [m/s²]
    double push(double t, double ay, double gz, double spd, double acc) {
        // lag만큼 늦춘 IMU (원형 버퍼)
        dly_ay_[pos_] = ay;
        dly_gz_[pos_] = gz;
        const size_t rd = (pos_ + 1) % dly_ay_.size();  // delay_ 샘플 전
        pos_ = rd;
        n_in_++;
        if (n_in_ > static_cast<uint64_t>(delay_)) {
            sa_ += dly_ay_[rd];
            sg_ += dly_gz_[rd];
            ni_++;
        }
        if (std::isfinite(spd)) {
            sv_ += spd;
            nv_++;
        }
        if (std::isfinite(acc)) {
            sacc_ += acc;
            nacc_++;
        }
        if (++k_ == 100) end_second(t);
        const double target = has_est_ ? est_ : 0.0;
        applied_ += std::clamp(target - applied_, -step_, step_);
        return applied_;
    }

    double applied() const { return applied_; }
    bool has_estimate() const { return has_est_; }
    uint64_t used_seconds() const { return used_; }
    uint64_t seconds() const { return secs_; }
    // 방금 끝난 1초가 있으면 true 한 번 (push 뒤에 확인)
    bool take_second(Second& s) {
        if (!new_sec_) return false;
        new_sec_ = false;
        s = last_;
        return true;
    }

private:
    void end_second(double t) {
        Second s;
        s.t = t;
        s.applied = applied_;
        if (ni_ == 100 && nv_ == 100) {
            const double v1 = std::max(sv_ / 100.0, 0.0);
            const double ay1 = sa_ / 100.0, gz1 = sg_ / 100.0;
            const double a1 = nacc_ ? sacc_ / nacc_ : NAN;
            s.resid = ay1 - v1 * gz1;
            s.use = (!std::isfinite(a1) || a1 <= c_.max_acc_m) && std::fabs(gz1 * 180.0 / kPi) < c_.straight_dps;
        }
        buf_r_.push_back(s.resid);
        buf_u_.push_back(s.use);
        if (static_cast<int>(buf_r_.size()) > win_) {
            buf_r_.erase(buf_r_.begin());
            buf_u_.erase(buf_u_.begin());
        }
        tmp_.clear();
        for (size_t i = 0; i < buf_r_.size(); i++)
            if (buf_u_[i]) tmp_.push_back(buf_r_[i]);
        if (static_cast<int>(tmp_.size()) >= c_.min_n) {
            est_ = median(tmp_);
            has_est_ = true;
        }
        used_ += s.use;
        secs_++;
        s.est = has_est_ ? est_ : NAN;
        last_ = s;
        new_sec_ = true;
        k_ = ni_ = nv_ = nacc_ = 0;
        sa_ = sg_ = sv_ = sacc_ = 0;
    }
    static double median(std::vector<double>& v) {  // numpy.median과 같음 (짝수면 가운데 둘의 평균)
        const size_t n = v.size(), h = n / 2;
        std::nth_element(v.begin(), v.begin() + h, v.end());
        const double hi = v[h];
        if (n % 2) return hi;
        const double lo = *std::max_element(v.begin(), v.begin() + h);
        return (lo + hi) / 2;
    }

    Config c_;
    int delay_ = 0, win_ = 1;
    double step_ = 0;
    std::vector<double> dly_ay_, dly_gz_;
    size_t pos_ = 0;
    uint64_t n_in_ = 0;
    int k_ = 0, ni_ = 0, nv_ = 0, nacc_ = 0;
    double sa_ = 0, sg_ = 0, sv_ = 0, sacc_ = 0;
    std::vector<double> buf_r_;
    std::vector<bool> buf_u_;
    std::vector<double> tmp_;
    double est_ = 0, applied_ = 0;
    bool has_est_ = false, new_sec_ = false;
    uint64_t used_ = 0, secs_ = 0;
    Second last_;
};

}  // namespace mm::xslope
