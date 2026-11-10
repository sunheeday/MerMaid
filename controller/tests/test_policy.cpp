// P4 정책 단위 테스트 (PRD S5 완료 확인, AC-09): ctest --test-dir build
// AC-09는 PRD §P4 개인화 표의 4가지 경우: 30초 안에 끔 / 30초 뒤에 끔 / 꺼진 상태에서 켬 / 한 주행에 3번 끔
#include <cmath>
#include <cstdio>
#include <stdexcept>
#include <string>

#include "policy.hpp"
#include "protocol_gen.hpp"

using namespace mm;

static int failures = 0;
#define CHECK(cond, ...)                                              \
    do {                                                              \
        if (!(cond)) {                                                \
            failures++;                                               \
            std::printf("FAIL %s:%d: ", __FILE__, __LINE__);          \
            std::printf(__VA_ARGS__);                                 \
            std::printf("\n");                                        \
        }                                                             \
    } while (0)

static const std::string AIR = proto::kDeviceAirflow;
static const std::string DISP = proto::kDeviceDisplay;
static const std::string HUD = proto::kDeviceHud;
static const std::string PA = "phone-a";
static const std::string PB = "phone-b";

static PolicyConfig repo_config() {
    PolicyConfig c = load_policy_config(MM_CONFIG_DIR "/policy.json", MM_CONFIG_DIR "/thresholds.json");
    c.base_th = {0.5, 0.5, 0.5};  // 테스트는 학습 결과와 무관하게 고정 문턱으로
    return c;
}

static Risk R(double p3, double p4) { return {1.0, p3, p4, 0.0, 0.0}; }

// HUD 연결(화면 ON), 폰 A 등록(화면 ON + 응시), 주행 시작
static Policy setup() {
    Policy pol(repo_config());
    pol.set_hud_connected(true);
    pol.on_screen(HUD, true, false);
    pol.add_phone(PA);
    pol.on_screen(PA, true, true);
    pol.on_profile(PA, 12.9, 1.0);
    pol.on_ride_start();
    return pol;
}

// 송풍이 켜질 때까지 높은 확률을 넣고 켜진 시각을 돌려준다
static double turn_on_airflow(Policy& pol, double t) {
    for (int i = 0; i < 3 && !pol.is_on(AIR); i++, t += 10) pol.on_risk(t, {{PA, R(0.9, 0.1)}});
    return t - 10;
}

// ---------------------------------------------------------------- AC-09
static void test_quick_off() {
    // 켜진 뒤 30초 안에 끔 → n += 1, 5분 쿨다운
    Policy pol = setup();
    const double t_on = turn_on_airflow(pol, 0);
    CHECK(pol.is_on(AIR), "airflow should be on");
    auto cmd = pol.on_override(t_on + 20, HUD, AIR, false);
    CHECK(cmd.size() == 1 && !cmd[0].on && cmd[0].tier == 1, "override off should emit one OFF command");
    CHECK(pol.tolerance_count(AIR) == 1, "n %d want 1", pol.tolerance_count(AIR));
    const double want_tol = 0.4 * std::tanh(1.0 / 6.0);
    CHECK(std::fabs(pol.tol(AIR) - want_tol) < 1e-12, "tol %.6f want %.6f", pol.tol(AIR), want_tol);
    // 쿨다운 300초 동안은 확률이 높아도 켜지지 않음
    double t = t_on + 30;
    for (; t < t_on + 20 + 300; t += 10) {
        pol.on_risk(t, {{PA, R(0.95, 0.1)}});
        CHECK(!pol.is_on(AIR), "turned on during cooldown at t=%.0f", t);
    }
    pol.on_risk(t, {{PA, R(0.95, 0.1)}});
    CHECK(pol.is_on(AIR), "should turn on after cooldown (t=%.0f)", t);
    CHECK(pol.take_dirty_tolerance() == std::vector<std::string>{AIR}, "airflow tolerance should be marked to save");
}

static void test_late_off() {
    // 30초 뒤에 끔 → n 변화 없음, 5분 쿨다운
    Policy pol = setup();
    const double t_on = turn_on_airflow(pol, 0);
    pol.on_override(t_on + 31, HUD, AIR, false);
    CHECK(pol.tolerance_count(AIR) == 0, "n %d want 0", pol.tolerance_count(AIR));
    CHECK(pol.take_dirty_tolerance().empty(), "nothing to save when n did not change");
    pol.on_risk(t_on + 40, {{PA, R(0.95, 0.1)}});
    CHECK(!pol.is_on(AIR), "cooldown applies to a late off too");
    pol.on_risk(t_on + 31 + 300, {{PA, R(0.95, 0.1)}});
    CHECK(pol.is_on(AIR), "on again after 300 s");
}

static void test_manual_on() {
    // 꺼진 상태에서 켬 → n -= 1 (최소 -4)
    Policy pol = setup();
    for (int i = 0; i < 6; i++) {
        pol.on_override(100.0 * i, HUD, AIR, true);
        CHECK(pol.is_on(AIR), "manual on");
        pol.on_override(100.0 * i + 50, HUD, AIR, false);  // 30초 뒤에 끔: n 그대로
    }
    CHECK(pol.tolerance_count(AIR) == -4, "n %d want -4 (floor)", pol.tolerance_count(AIR));
    const double want = 0.4 * std::tanh(-4.0 / 6.0);
    CHECK(std::fabs(pol.tol(AIR) - want) < 1e-12, "tol %.4f want %.4f", pol.tol(AIR), want);
    // 이미 켜진 기기를 켜는 것은 아무 일도 하지 않음
    Policy p2 = setup();
    turn_on_airflow(p2, 0);
    CHECK(p2.on_override(30, HUD, AIR, true).empty() && p2.tolerance_count(AIR) == 0, "on while on is ignored");
}

static void test_three_quick_offs_disable() {
    // 한 주행에서 같은 기기를 (빠르게) 3번 끔 → 이번 주행 동안 비활성화, 다음 주행에 복구
    Policy pol = setup();
    double t = 0;
    for (int i = 0; i < 3; i++) {
        pol.on_override(t, HUD, DISP, true);
        pol.on_override(t + 10, HUD, DISP, false);
        t += 400;  // 쿨다운이 끝난 뒤
    }
    CHECK(!pol.is_enabled(DISP), "display should be disabled after 3 quick offs");
    // n: 켬 3번(-3) + 빠른 끔 3번(+3)
    CHECK(pol.tolerance_count(DISP) == 0, "n %d want 0", pol.tolerance_count(DISP));
    for (int i = 0; i < 12; i++, t += 10) pol.on_risk(t, {{PA, R(0.1, 0.99)}});
    CHECK(!pol.is_on(DISP), "disabled device must not turn on");
    pol.on_ride_start();
    CHECK(pol.is_enabled(DISP), "re-enabled on the next ride");
    for (int i = 0; i < 6; i++, t += 10) pol.on_risk(t, {{PA, R(0.1, 0.99)}});
    CHECK(pol.is_on(DISP), "display turns on again in the next ride");
}

// ---------------------------------------------------------------- 켜기, 끄기 규칙
static void test_hold_and_off() {
    Policy pol = setup();
    const double hi = 0.99, mid = 0.45, lo = 0.3;  // 문턱 0.5, 끄기 기준 0.4
    double t = 0;
    for (int i = 0; i < 5; i++, t += 10) pol.on_risk(t, {{PA, R(0.1, hi)}});
    CHECK(!pol.is_on(DISP), "tier 2 needs 6 cycles");
    pol.on_risk(t, {{PA, R(0.1, hi)}}); t += 10;
    CHECK(pol.is_on(DISP), "tier 2 on after 6 cycles");
    CHECK(!pol.is_on(PA), "tier 3 needs 12 cycles");
    for (int i = 0; i < 6; i++, t += 10) pol.on_risk(t, {{PA, R(0.1, hi)}});
    CHECK(pol.is_on(PA), "tier 3 on after 12 cycles");
    // 문턱과 끄기 기준 사이에서는 계속 켜져 있음
    for (int i = 0; i < 20; i++, t += 10) pol.on_risk(t, {{PA, R(0.1, mid)}});
    CHECK(pol.is_on(DISP) && pol.is_on(PA), "stays on between th - 0.10 and th");
    for (int i = 0; i < 5; i++, t += 10) pol.on_risk(t, {{PA, R(0.1, lo)}});
    CHECK(pol.is_on(DISP), "off needs 6 cycles below th - 0.10");
    pol.on_risk(t, {{PA, R(0.1, lo)}});
    CHECK(!pol.is_on(DISP) && !pol.is_on(PA), "off after 6 cycles");
}

static void test_gates() {
    Policy pol = setup();
    pol.on_screen(HUD, false, false);
    pol.on_screen(PA, true, false);  // 화면은 켜졌지만 응시 없음
    double t = 0;
    for (int i = 0; i < 15; i++, t += 10) pol.on_risk(t, {{PA, R(0.1, 0.99)}});
    CHECK(!pol.is_on(DISP) && !pol.is_on(PA), "closed gates keep tier 2/3 off");
    pol.on_screen(HUD, true, false);
    pol.on_screen(PA, true, true);
    for (int i = 0; i < 12; i++, t += 10) pol.on_risk(t, {{PA, R(0.1, 0.99)}});
    CHECK(pol.is_on(DISP) && pol.is_on(PA), "open gates");
    pol.on_screen(PA, false, false);
    auto cmd = pol.on_risk(t, {{PA, R(0.1, 0.99)}});
    CHECK(!pol.is_on(PA) && pol.is_on(DISP), "phone gate closing turns only the phone off at once");
    CHECK(cmd.size() == 1 && cmd[0].device == PA && cmd[0].tier == 3, "one OFF command for the phone");
}

static void test_shared_max_and_personal() {
    Policy pol = setup();
    pol.add_phone(PB);
    pol.on_screen(PB, true, true);
    double t = 0;
    // B만 위험: 공용(Tier 1, 2)은 최대값을 따라 켜지고, A의 폰은 켜지지 않음
    for (int i = 0; i < 12; i++, t += 10) pol.on_risk(t, {{PA, R(0.1, 0.1)}, {PB, R(0.9, 0.9)}});
    CHECK(pol.is_on(AIR) && pol.is_on(DISP), "shared devices follow the max over phones");
    CHECK(pol.is_on(PB) && !pol.is_on(PA), "tier 3 follows each phone's own P(>=4)");
    // 확률이 아예 없는 폰은 위험 낮음으로 본다
    for (int i = 0; i < 6; i++, t += 10) pol.on_risk(t, {{PA, R(0.1, 0.1)}});
    CHECK(!pol.is_on(PB), "phone with no risk value turns off");
}

static void test_tolerance_shifts_threshold() {
    Policy pol = setup();
    pol.restore_tolerance(AIR, 6);  // tol = 0.4 tanh(1) = 0.305 → 문턱 0.805
    pol.on_risk(0, {{PA, R(0.8, 0.1)}});
    CHECK(!pol.is_on(AIR), "0.8 < 0.805 should not turn on");
    pol.on_risk(10, {{PA, R(0.81, 0.1)}});
    CHECK(pol.is_on(AIR), "0.81 >= 0.805 should turn on");
}

// ---------------------------------------------------------------- 민감도
static void test_sensitivity() {
    Policy pol = setup();
    pol.add_phone(PB);
    pol.on_profile(PB, 30.0, 1.5);
    // A 폰에서 앰비언트(display)를 켬 → A만 x1.15
    pol.on_override(0, PA, DISP, true);
    CHECK(std::fabs(pol.profile(PA)->sens - 1.15) < 1e-12, "sens %.4f want 1.15", pol.profile(PA)->sens);
    CHECK(pol.profile(PB)->sens == 1.5, "other passenger unchanged");
    // 송풍 켜기, HUD 버튼, 다른 사람 폰 켜기, 끄기는 민감도를 바꾸지 않음
    pol.on_override(10, PA, AIR, true);
    pol.on_override(20, HUD, DISP, false);
    pol.on_override(30, HUD, DISP, true);
    pol.on_override(40, PA, PB, true);
    pol.on_override(50, PA, PA, true);  // 자기 폰 큐 → x1.15
    CHECK(std::fabs(pol.profile(PA)->sens - 1.15 * 1.15) < 1e-12, "sens %.4f want 1.3225", pol.profile(PA)->sens);
    // 상한 2.0
    for (int i = 0; i < 10; i++) {
        pol.on_override(100.0 + 100 * i, PA, PA, false);
        pol.on_override(150.0 + 100 * i, PA, PA, true);
    }
    CHECK(pol.profile(PA)->sens == 2.0, "sens %.4f capped at 2.0", pol.profile(PA)->sens);
    // 주행 끝: 설문값 쪽으로 10% 복귀
    pol.on_ride_end();
    CHECK(std::fabs(pol.profile(PA)->sens - 1.9) < 1e-12, "ride end sens %.4f want 1.9", pol.profile(PA)->sens);
    CHECK(pol.profile(PB)->sens == 1.5, "already at survey value");
    // PROF: 같은 설문값이면 현재 민감도 유지, 바뀌면 다시 시작
    pol.on_profile(PA, 12.9, 1.0);
    CHECK(std::fabs(pol.profile(PA)->sens - 1.9) < 1e-12, "same MSSQ keeps current sens");
    pol.on_profile(PA, 20.0, 1.3);
    CHECK(pol.profile(PA)->sens == 1.3 && pol.profile(PA)->sens_mssq == 1.3, "new MSSQ resets sens");
    auto dirty = pol.take_dirty_profiles();
    CHECK(dirty.size() == 2, "both profiles marked to save (%zu)", dirty.size());
}

// ---------------------------------------------------------------- 설정 파일
static void test_config() {
    PolicyConfig c = load_policy_config(MM_CONFIG_DIR "/policy.json", MM_CONFIG_DIR "/thresholds.json");
    CHECK(c.tier[0].hold == 1 && c.tier[1].hold == 6 && c.tier[2].hold == 12, "hold 1/6/12");
    CHECK(c.tier[0].input_k == 3 && c.tier[1].input_k == 4 && c.tier[2].input_k == 4, "input k 3/4/4");
    CHECK(c.off_hold == 6 && c.cooldown_s == 300 && c.max_quick_offs == 3, "off/cooldown/quick");
    for (int t = 0; t < 3; t++) CHECK(c.base_th[t] > 0 && c.base_th[t] < 1, "base_th[%d] %.3f", t, c.base_th[t]);
    const std::string pol = R"({"tiers":{"1":{"device":"airflow-01","input_k":3,"shared":true,"hold_cycles":1,"gate":"none"},
      "2":{"device":"display-01","input_k":4,"shared":true,"hold_cycles":6,"gate":"hud_screen_on"},
      "3":{"device":"phone","input_k":4,"shared":false,"hold_cycles":12,"gate":"screen_on_and_att"}},
      "off_margin":0.1,"off_hold_cycles":6,"cooldown_s":300,"quick_off_s":30,"max_quick_offs":3,
      "tolerance":{"scale":0.4,"divisor":6,"n_min":-4},"sensitivity":{"on_step":1.15,"max":2.0,"ride_end_return":0.1}})";
    auto th = [](const char* placeholder, int version) {
        return std::string("{\"placeholder\":") + placeholder + ",\"feature_order_version\":" + std::to_string(version) +
               ",\"base_th\":{\"1\":0.6,\"2\":0.6,\"3\":0.7}}";
    };
    bool threw = false;
    try { parse_policy_config(pol, th("true", proto::kFeatureVersion)); } catch (const std::runtime_error&) { threw = true; }
    CHECK(threw, "placeholder thresholds must be refused");
    threw = false;
    try { parse_policy_config(pol, th("false", proto::kFeatureVersion + 1)); } catch (const std::runtime_error&) { threw = true; }
    CHECK(threw, "feature_order_version mismatch must be refused");
    PolicyConfig ok = parse_policy_config(pol, th("false", proto::kFeatureVersion));
    CHECK(ok.base_th[2] == 0.7, "inline config parsed");
}

int main() {
    test_quick_off();
    test_late_off();
    test_manual_on();
    test_three_quick_offs_disable();
    test_hold_and_off();
    test_gates();
    test_shared_max_and_personal();
    test_tolerance_shifts_threshold();
    test_sensitivity();
    test_config();
    if (failures) {
        std::printf("%d failure(s)\n", failures);
        return 1;
    }
    std::printf("policy: all tests passed\n");
    return 0;
}
