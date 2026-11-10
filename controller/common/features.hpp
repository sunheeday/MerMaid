// P2 피처 추출: 100 Hz 샘플(trip_format.json 한 행) → 10초마다 차량 피처 71개.
// 표준 C++17만 사용. Mac의 tools/featurize와 Pi의 P2가 이 코드를 그대로 쓴다.
// 피처 정의: controller/common/FEATURES.md
#pragma once
#include <array>
#include <deque>
#include <vector>

#include "filters.hpp"
#include "protocol_gen.hpp"

namespace mm {

using Row = std::array<double, proto::kTripCols>;
using VehicleVec = std::array<double, proto::kNumVehicle>;

// 고정 길이 링버퍼: 최근 n개에 대한 통계만 필요하다
class Ring {
public:
    explicit Ring(size_t cap = 1) : buf_(cap, 0.0) {}
    void push(double x) {
        buf_[head_] = x;
        head_ = (head_ + 1) % buf_.size();
        if (size_ < buf_.size()) size_++;
    }
    size_t size() const { return size_; }
    // i = 0이 가장 최근
    double back(size_t i) const { return buf_[(head_ + buf_.size() - 1 - i) % buf_.size()]; }
    void clear() { head_ = size_ = 0; }

private:
    std::vector<double> buf_;
    size_t head_ = 0, size_ = 0;
};

class FeatureExtractor {
public:
    explicit FeatureExtractor(double oman_k = proto::kOmanK0);

    // 주행 시작: 버퍼, MSDV, Oman, 휴식, 이벤트를 모두 초기화
    void reset();

    // 샘플 하나를 넣는다. 10초 경계(1000번째 샘플마다)에서 true를 돌려주고 vehicle()이 갱신된다.
    bool push(const Row& r);

    const VehicleVec& vehicle() const { return out_; }
    double time() const { return t_; }
    // MOT(100 ms)용 가중 가속도
    double weighted_ax() const { return awx_; }
    double weighted_ay() const { return awy_; }
    bool resting() const { return rest_; }

private:
    void emit();

    double dt_;
    Wf wfx_, wfy_, wfz_;
    Biquad lpx_, lpy_;
    Oman oman_;

    // 100 Hz 채널 (최대 5분)
    enum Ch { AWX, AWY, AWZ, JX, JY, GZ, RAX, RAZ, SPEED, APED, BPED, PRATE, REGEN, DECEL, BPOW, TRATE, NCH };
    std::array<Ring, NCH> ch_;

    Row last_{};
    bool has_prev_ = false;
    long n_ = 0;
    double t0_ = 0, t_ = 0;
    double awx_ = 0, awy_ = 0, awz_ = 0;
    double msdv2_ = 0;
    std::vector<double> msdv_hist_, score_hist_;  // 10초마다

    // 이벤트: 저역통과한 원가속도의 상승 에지
    struct Event { double t; int type; };
    std::deque<Event> events_;
    bool in_accel_ = false, in_brake_ = false, in_turn_ = false;

    // 휴식
    double still_time_ = 0, rest_end_ = 0;
    bool rest_ = false, reexp_ = false, had_rest_ = false;

    VehicleVec out_{};
};

}  // namespace mm
