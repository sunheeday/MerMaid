// P4 정책: 10초마다 받은 확률로 기기별 ON/OFF를 정하고, 수동 조작으로 허용도를 갱신한다.
// 표준 C++17만 사용 (QNX API 없음). PRD §P4 Policy의 의사코드를 그대로 따른다.
// 설정: controller/config/policy.json + thresholds.json (재학습 없이 바꿀 수 있어야 함, PRD §10.3)
//
// 영구 상태(허용도 카운터 n, 승객 프로필)는 이 클래스가 들고 있고, 파일 저장은 P4 프로세스가 한다.
// 바뀐 키는 take_dirty_*()로 가져가 저장한다 (PRD §11 Q7: 바뀔 때마다 기록).
#pragma once
#include <array>
#include <map>
#include <set>
#include <string>
#include <vector>

namespace mm {

struct TierCfg {
    std::string device;  // tier 3은 폰마다 다르므로 "phone"
    int input_k = 0;     // 3 또는 4
    bool shared = false; // 공용이면 등록된 폰 중 최대 확률
    int hold = 1;        // ON 전 연속 주기 수 (1주기 = 10초)
    std::string gate;    // none | hud_screen_on | screen_on_and_att
};

struct PolicyConfig {
    std::array<TierCfg, 3> tier;
    std::array<double, 3> base_th{};
    double off_margin = 0.10;
    int off_hold = 6;
    double cooldown_s = 300;
    double quick_off_s = 30;
    int max_quick_offs = 3;
    double tol_scale = 0.4;   // tol = tol_scale * tanh(n / tol_divisor)
    double tol_divisor = 6;
    int n_min = -4;
};

// JSON 문자열에서 / 파일에서. 형식 오류, 자리표시 문턱(placeholder: true), feature_order 버전 불일치는 예외.
PolicyConfig parse_policy_config(const std::string& policy_json, const std::string& thresholds_json);
PolicyConfig load_policy_config(const std::string& policy_path, const std::string& thresholds_path);

// 앱 설문 결과 (M06 PROF): mssq와 그 설문으로 계산한 sens. 둘 다 모델 입력(G10)이고, 학습 때와 같은 규칙의 값이 되도록
// 컨트롤러는 바꾸지 않는다. 타면서 사람마다 맞추는 일은 개인 보정(docs/controller/DATA_LOGGING.md §12)이 맡는다.
struct Profile {
    double mssq = 0;
    double sens = 1.0;
};

// P5로 보낼 명령 (기기 하나의 ON/OFF)
struct Command {
    std::string device;
    int tier = 0;
    bool on = false;
};

// 한 승객의 P(>=2) .. P(>=6), 단조 보정을 거친 값
using Risk = std::array<double, 5>;

class Policy {
public:
    explicit Policy(PolicyConfig cfg);

    // ---- 영구 상태 복원 (시작 시 파일에서) ----
    void restore_tolerance(const std::string& device, int n);
    void restore_profile(const std::string& phone, const Profile& p);

    // ---- 기기 등록 (P5) ----
    void set_hud_connected(bool connected);  // airflow-01, display-01을 함께 등록 / 해제
    void add_phone(const std::string& phone);
    void remove_phone(const std::string& phone);

    // ---- 주행 경계 ----
    void on_ride_start();  // 주행별 카운터 초기화. 허용도와 프로필은 유지

    // ---- 메시지 ----
    void on_screen(const std::string& sender, bool screen, bool att);           // M03 SCR
    void on_profile(const std::string& phone, double mssq, double sens);        // M06 PROF
    std::vector<Command> on_risk(double now, const std::map<std::string, Risk>& p);  // P3 RISK, 10초마다
    std::vector<Command> on_override(double now, const std::string& sender,
                                     const std::string& device, bool on);       // M05 OVR

    // ---- 조회 ----
    double tol(const std::string& device) const;  // tol_scale * tanh(n / tol_divisor), P3의 G8 입력
    int tolerance_count(const std::string& device) const;
    const Profile* profile(const std::string& phone) const;
    bool is_on(const std::string& device) const;
    bool is_enabled(const std::string& device) const;
    int tier_of(const std::string& device) const;  // 등록되지 않은 기기는 0
    const PolicyConfig& config() const { return cfg_; }

    std::vector<std::string> take_dirty_tolerance();
    std::vector<std::string> take_dirty_profiles();

private:
    struct DevState {
        int cnt = 0, low = 0, quick = 0;
        bool on = false, enabled = true;
        double on_time = 0, cool_until = -1e300;
    };
    struct Ctx {
        bool screen = false, att = false;
    };

    PolicyConfig cfg_;
    bool hud_ = false;
    std::vector<std::string> phones_;          // 등록 순서 유지
    std::map<std::string, DevState> state_;    // 주행별
    std::map<std::string, Ctx> ctx_;
    std::map<std::string, int> n_;             // 영구
    std::map<std::string, Profile> prof_;      // 영구
    std::set<std::string> dirty_tol_, dirty_prof_;

    std::vector<std::string> present() const;
    bool gate(const std::string& device, int tier) const;
    bool is_phone(const std::string& id) const;
    void set(const std::string& device, bool on, double now, std::vector<Command>& out);
};

}  // namespace mm
