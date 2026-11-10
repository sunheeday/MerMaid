// C++ P4 정책으로 확률 시퀀스를 재생 (ml/train/policy.py와 같은 결과인지 확인하는 용도)
//   policy_replay <tier 1..3> <threshold> < probs.txt
// 입력: 한 줄에 확률 하나 (10초 주기). 빈 줄은 주행 경계. 출력: 줄마다 ON 여부 0/1.
// 재생 조건은 Python과 같다: 게이트는 항상 열림, 수동 조작 없음, 허용도 0.
#include <cstdio>
#include <cstdlib>
#include <iostream>
#include <string>

#include "policy.hpp"
#include "protocol_gen.hpp"

int main(int argc, char** argv) {
    if (argc != 3) {
        std::fprintf(stderr, "usage: policy_replay <tier 1..3> <threshold> < probs.txt\n");
        return 2;
    }
    const int tier = std::atoi(argv[1]);
    if (tier < 1 || tier > 3) {
        std::fprintf(stderr, "tier must be 1..3\n");
        return 2;
    }
    mm::PolicyConfig cfg = mm::load_policy_config(MM_CONFIG_DIR "/policy.json", MM_CONFIG_DIR "/thresholds.json");
    cfg.base_th[tier - 1] = std::atof(argv[2]);
    mm::Policy pol(cfg);
    const std::string phone = "replay-phone";
    pol.set_hud_connected(true);
    pol.on_screen(mm::proto::kDeviceHud, true, false);
    pol.add_phone(phone);
    pol.on_screen(phone, true, true);
    const std::string device = tier == 1 ? mm::proto::kDeviceAirflow : tier == 2 ? mm::proto::kDeviceDisplay : phone;
    const size_t col = static_cast<size_t>(cfg.tier[tier - 1].input_k - 2);

    pol.on_ride_start();
    std::string line;
    double t = 0;
    while (std::getline(std::cin, line)) {
        if (line.empty()) {
            pol.on_ride_start();
            std::cout << "\n";
            continue;
        }
        mm::Risk r{};
        r[col] = std::atof(line.c_str());
        pol.on_risk(t, {{phone, r}});
        t += 10;
        std::cout << (pol.is_on(device) ? 1 : 0) << "\n";
    }
    return 0;
}
