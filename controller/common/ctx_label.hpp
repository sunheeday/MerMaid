// V3 문맥 모델: 한 주행의 기록에서 승객·장치마다 문맥 토큰으로 남길 주기와 라벨을 고른다 (P4가 들고 있음).
// 표준 C++17만 사용. 학습 쪽 규칙(context6.build_tokens)과 2차 보고 §3b의 반응 라벨 규칙을 차에서 할 수 있는 만큼 옮겼다.
//
// 장치: 0 fan(airflow-01, 공용) · 1 ambient(display-01, 공용) · 2 dots(그 승객의 폰)
//
// 주기마다 record_cycle: 그 주기에 승객이 장치를 켤 수 있었는지(자격: 등록·꺼짐·게이트 열림)
// 사건마다 on_user / on_auto: 사람이 직접 켜고 끔(누가) / 정책이 자동으로 켜고 끔
//
// picks(t_end): 승객·장치마다 (한 주행에)
//   양성 1개  그 사람이 직접 켠 마지막 사건, 또는 (그 사람 폰의 dots만) 자동으로 켜졌는데 quick_off_s 넘게 둔 마지막 사건.
//             토큰 주기 = 켜기 시각 이하의 마지막 자격 있는 주기 (학습의 '마지막 Y=1 행'). P4는 자동 켜기를 그 결정을 낸
//             주기의 t로, OVR을 마지막 주기의 t로 기록하므로 같은 시각의 주기가 그 사건 바로 앞의 상태다
//   음성 ≤2개 (a) 자동으로 켜졌는데 그 사람이 quick_off_s 안에 끔 → 켜기 직전 주기 (가장 확실한 '아니오', 먼저 넣음)
//             (b) 깨끗한 0: 자격 있고, 앞으로 horizon_s 안에 켜기 사건이 없고, 끈 뒤 after_off_s 지났고,
//                 주행 끝 horizon_s 전보다 앞인 주기에서 무작위
//   공용 장치를 남이 켰으면 그 전 horizon_s의 나머지 승객 주기는 음성 후보에서 뺀다 (누르려 했는지 모름)
//   공용 장치의 자동 켜짐을 그대로 둔 것은 누구 몫인지 몰라 양성으로 쓰지 않는다
#pragma once
#include <cstdint>
#include <map>
#include <random>
#include <string>
#include <vector>

namespace mm::ctx {

constexpr int kFan = 0, kAmbient = 1, kDots = 2, kDevices = 3;

struct Pick {
    std::string passenger;
    uint64_t seq = 0;
    int device = 0;
    int label = 0;
};

struct LabelConfig {
    double horizon_s = 300;    // 앞으로 5분 (반응 라벨의 지평)
    double quick_off_s = 30;   // 자동으로 켜진 뒤 이 안에 끄면 '아니오'
    double after_off_s = 120;  // 끈 직후 애매한 구간
    int neg_per_ride = 2;      // context6.build_tokens 기본값
};

class Labeler {
public:
    explicit Labeler(LabelConfig cfg = {}) : cfg_(cfg) {}

    void ride_start(uint64_t seed);
    // 주기 하나: 승객마다 장치 3개의 자격 (켤 수 있었나)
    void record_cycle(uint64_t seq, double t, const std::string& passenger, const bool eligible[kDevices]);
    // 사람이 직접 켜고 끔. 공용 장치(fan, ambient)는 누른 사람이 sender
    void on_user(double t, const std::string& sender, int device, bool on);
    // 정책이 자동으로 켜고 끔. dots는 그 폰 주인(owner)의 것, 공용 장치는 owner 비움
    void on_auto(double t, int device, const std::string& owner, bool on);
    // 주행 끝: 고른 토큰 (승객 이름 순, 장치 순, 같은 장치는 시간 순)
    std::vector<Pick> picks(double t_end);

private:
    struct Cyc {
        uint64_t seq;
        double t;
        bool ok[kDevices];
    };
    struct Ev {
        double t;
        int device;
        std::string who;  // 사람: sender / 자동: dots면 폰 주인, 공용이면 ""
        bool on, user;
    };
    LabelConfig cfg_;
    std::map<std::string, std::vector<Cyc>> cyc_;
    std::vector<Ev> ev_;
    std::mt19937_64 rng_;
};

}  // namespace mm::ctx
