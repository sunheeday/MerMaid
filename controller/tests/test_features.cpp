// FeatureExtractor 단위 테스트 (PRD S2 완료 확인): ctest --test-dir build
#include <cmath>
#include <cstdio>
#include <functional>
#include <limits>

#include "features.hpp"

using namespace mm;
using namespace mm::proto;
using mm::kPi;

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

static const double NaN = std::numeric_limits<double>::quiet_NaN();
constexpr double FS = 100.0;

// IMU만 있는 행: 나머지 차량 신호는 NaN
static Row imu_row(double t, double ax, double ay, double az = 9.81, double gz = 0.0) {
    Row r;
    r.fill(NaN);
    r[C_T] = t; r[C_AX] = ax; r[C_AY] = ay; r[C_AZ] = az;
    r[C_GX] = 0; r[C_GY] = 0; r[C_GZ] = gz;
    return r;
}

// seconds초 동안 f(t)로 행을 만들어 넣고 마지막 피처 벡터를 돌려준다
static VehicleVec run(FeatureExtractor& fx, double seconds, const std::function<Row(double)>& f, double t0 = 0) {
    const long n = std::lround(seconds * FS);
    for (long i = 0; i < n; i++) fx.push(f(t0 + i / FS));
    return fx.vehicle();
}

static void test_sine_rms() {
    // 0.2 Hz, 진폭 1 m/s^2 앞뒤 사인파 → 가중 RMS = |Wf(0.2 Hz)| / sqrt(2) (ISO 표 0.992)
    FeatureExtractor fx;
    auto v = run(fx, 900, [](double t) { return imu_row(t, std::sin(2 * kPi * 0.2 * t), 0); });
    const double want = 0.992 / std::sqrt(2.0);
    CHECK(std::fabs(v[F_RMS_X_5M] / want - 1) < 0.01, "rms_x_5m %.4f want %.4f", v[F_RMS_X_5M], want);
    CHECK(std::fabs(v[F_RMS_X_1M] / want - 1) < 0.01, "rms_x_1m %.4f want %.4f", v[F_RMS_X_1M], want);
    CHECK(v[F_RMS_Y_5M] < 1e-9 && v[F_RMS_Z_5M] < 1e-6, "y %.3g z %.3g should be ~0", v[F_RMS_Y_5M], v[F_RMS_Z_5M]);
    // 가속 구간과 감속 구간 에너지가 같고 합이 전체와 같음
    CHECK(std::fabs(v[F_DECEL_RMS_X_5M] - v[F_ACCEL_RMS_X_5M]) < 0.01, "decel %.4f accel %.4f",
          v[F_DECEL_RMS_X_5M], v[F_ACCEL_RMS_X_5M]);
    const double tot = std::hypot(v[F_DECEL_RMS_X_5M], v[F_ACCEL_RMS_X_5M]);
    CHECK(std::fabs(tot - v[F_RMS_X_5M]) < 1e-9, "semi-RMS sum %.6f vs rms %.6f", tot, v[F_RMS_X_5M]);
    // MSDV ~ RMS x sqrt(T) (시작 과도 구간 때문에 2% 안)
    const double msdv_want = want * std::sqrt(900.0);
    CHECK(std::fabs(v[F_MSDV] / msdv_want - 1) < 0.02, "msdv %.3f want %.3f", v[F_MSDV], msdv_want);
}

static void test_constant_input_msdv() {
    // 상수 가속도(기울기, 중력)는 Wf 고역통과로 사라져 MSDV가 늘지 않는다. Oman 입력은 가중 전이라 쌓인다
    FeatureExtractor fx;
    auto v = run(fx, 1200, [](double t) { return imu_row(t, 0.5, 0.0); });
    CHECK(v[F_MSDV_INC_5M] < 1e-3, "msdv_inc_5m %.3g should be ~0", v[F_MSDV_INC_5M]);
    CHECK(v[F_RMS_X_5M] < 1e-3, "rms_x_5m %.3g should be ~0", v[F_RMS_X_5M]);
    CHECK(v[F_OMAN_SCORE] > 1.0 && v[F_OMAN_DELTA_2M] > 0, "oman %.3f delta %.3f should grow",
          v[F_OMAN_SCORE], v[F_OMAN_DELTA_2M]);
    // 상수 c의 정상 상태로 수렴하는 중: S < c
    CHECK(v[F_SENSITIZATION] > 0 && v[F_SENSITIZATION] < 0.5, "sensitization %.3f", v[F_SENSITIZATION]);
}

static void test_start_has_no_transient() {
    // T-22: 중력 9.81과 기울기 0.3 m/s^2로 시작해도 첫 10초·1분 가중 RMS가 0 근처, 급가속도 없음
    FeatureExtractor fx;
    auto v = run(fx, 10, [](double t) { return imu_row(t, 0.3, -0.2); });
    CHECK(v[F_RMS_Z_10S] < 1e-6 && v[F_RMS_X_10S] < 1e-6 && v[F_RMS_Y_10S] < 1e-6, "10s rms x %.3g y %.3g z %.3g",
          v[F_RMS_X_10S], v[F_RMS_Y_10S], v[F_RMS_Z_10S]);
    v = run(fx, 50, [](double t) { return imu_row(t, 0.3, -0.2); }, 10);
    CHECK(v[F_RMS_Z_1M] < 1e-6 && v[F_JERK_RMS_X_1M] < 1e-6, "1m rms_z %.3g jerk_x %.3g", v[F_RMS_Z_1M],
          v[F_JERK_RMS_X_1M]);
    CHECK(v[F_HARD_ACCEL_COUNT_1M] == 0 && v[F_HARD_BRAKE_COUNT_1M] == 0, "no events");
}

static void test_rest_without_speed() {
    FeatureExtractor fx;
    auto shake = [](double t) { return imu_row(t, 1.5 * std::sin(2 * kPi * 0.3 * t), 0); };
    auto v = run(fx, 120, shake);
    CHECK(v[F_REEXPOSURE] == 0 && std::isnan(v[F_TIME_SINCE_REST]), "no rest yet");
    // 정지 200초: 가중 가속도가 0.05 아래로 내려간 뒤 3분이 지나야 휴식
    v = run(fx, 200, [](double t) { return imu_row(t, 0, 0); }, 120);
    CHECK(fx.resting(), "should be resting after 200 s still");
    CHECK(v[F_REEXPOSURE] == 0, "reexposure before moving again");
    v = run(fx, 60, shake, 320);
    CHECK(!fx.resting() && v[F_REEXPOSURE] == 1, "reexposure after moving");
    CHECK(v[F_TIME_SINCE_REST] > 50 && v[F_TIME_SINCE_REST] < 61, "time_since_rest %.1f", v[F_TIME_SINCE_REST]);
    CHECK(std::fabs(v[F_RIDE_TIME] - 379.99) < 1e-6, "ride_time %.2f", v[F_RIDE_TIME]);
}

static void test_rest_short_stop_is_not_rest() {
    FeatureExtractor fx;
    auto shake = [](double t) { return imu_row(t, 1.5 * std::sin(2 * kPi * 0.3 * t), 0); };
    run(fx, 120, shake);
    run(fx, 100, [](double t) { return imu_row(t, 0, 0); }, 120);
    auto v = run(fx, 60, shake, 220);
    CHECK(v[F_REEXPOSURE] == 0, "100 s stop must not count as rest");
}

static void test_rest_with_speed() {
    FeatureExtractor fx;
    auto row = [](double t, double speed) { Row r = imu_row(t, 0, 0); r[C_SPEED] = speed; return r; };
    run(fx, 60, [&](double t) { return row(t, 10); });
    run(fx, 185, [&](double t) { return row(t, 0); }, 60);
    CHECK(fx.resting(), "speed 0 for 185 s is rest");
    auto v = run(fx, 30, [&](double t) { return row(t, 5); }, 245);
    CHECK(v[F_REEXPOSURE] == 1, "reexposure with speed");
    CHECK(v[F_SPEED_MEAN_10S] == 5 && v[F_STOP_RATIO_5M] > 0.6, "speed mean %.2f stop ratio %.2f",
          v[F_SPEED_MEAN_10S], v[F_STOP_RATIO_5M]);
}

static void test_events() {
    // 1.5 m/s^2를 넘는 가속 2번, 0.75 넘는 감속 1번 (저역통과 후 기준)
    FeatureExtractor fx;
    auto v = run(fx, 60, [](double t) {
        double ax = 0;
        if ((t > 5 && t < 10) || (t > 25 && t < 30)) ax = 2.5;
        if (t > 40 && t < 45) ax = -1.5;
        return imu_row(t, ax, 0);
    });
    CHECK(v[F_HARD_ACCEL_COUNT_1M] == 2, "hard accel %g", v[F_HARD_ACCEL_COUNT_1M]);
    CHECK(v[F_HARD_BRAKE_COUNT_1M] == 1, "hard brake %g", v[F_HARD_BRAKE_COUNT_1M]);
    CHECK(v[F_HARD_TURN_COUNT_1M] == 0, "hard turn %g", v[F_HARD_TURN_COUNT_1M]);
    CHECK(v[F_EVENT_IRREGULARITY_5M] > 0, "irregularity %g", v[F_EVENT_IRREGULARITY_5M]);
}

static void test_event_hysteresis() {
    // 감속이 -0.75 근처에서 0.1 m/s^2씩 오르내려도 (기준을 10번 넘나듦) 제동 1번. 되돌아온 뒤 다시 밟으면 2번
    FeatureExtractor fx;
    auto v = run(fx, 60, [](double t) {
        double ax = 0;
        if (t > 5 && t < 25) ax = -0.75 + 0.1 * std::sin(2 * kPi * 0.25 * t);
        if (t > 40 && t < 45) ax = -1.5;
        return imu_row(t, ax, 0);
    });
    CHECK(v[F_HARD_BRAKE_COUNT_1M] == 2, "hard brake with chatter %g", v[F_HARD_BRAKE_COUNT_1M]);
}

static void test_missing_groups_are_nan() {
    FeatureExtractor fx;
    auto v = run(fx, 30, [](double t) { return imu_row(t, std::sin(t), std::cos(t)); });
    for (int i = F_SPEED_MEAN_10S; i <= F_SEAT_ROW; i++)
        CHECK(std::isnan(v[i]), "%s should be NaN without vehicle signals", kFeatureNames[i]);
    CHECK(std::isnan(v[F_SVC_CONFLICT]), "svc disabled");
    for (int i = F_RMS_X_10S; i <= F_SENSITIZATION; i++)
        if (i != F_EVENT_IRREGULARITY_5M) CHECK(!std::isnan(v[i]), "%s should be filled", kFeatureNames[i]);
}

static void test_pitch() {
    // 경사 3도, 정속: 가속도계는 (g sin, 0, g cos)
    FeatureExtractor fx;
    const double th = 3 * kPi / 180;
    auto v = run(fx, 70, [&](double t) { return imu_row(t, 9.81 * std::sin(th), 0, 9.81 * std::cos(th)); });
    CHECK(std::fabs(v[F_PITCH_MEAN_1M] - th) < 1e-9, "pitch %.6f want %.6f", v[F_PITCH_MEAN_1M], th);
}

static void test_reset() {
    FeatureExtractor fx;
    run(fx, 100, [](double t) { return imu_row(t, std::sin(t), 0); });
    fx.reset();
    auto v = run(fx, 10, [](double t) { return imu_row(t, 0, 0); });
    CHECK(v[F_MSDV] == 0 && v[F_OMAN_SCORE] == 0, "reset should clear state");
}

int main() {
    test_sine_rms();
    test_constant_input_msdv();
    test_start_has_no_transient();
    test_rest_without_speed();
    test_rest_short_stop_is_not_rest();
    test_rest_with_speed();
    test_events();
    test_event_hysteresis();
    test_missing_groups_are_nan();
    test_pitch();
    test_reset();
    std::printf(failures ? "%d FAILED\n" : "all feature tests passed\n", failures);
    return failures ? 1 : 0;
}
