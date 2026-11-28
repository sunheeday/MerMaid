// P2 실시간 횡단 경사 제거 (common/cross_slope.hpp)
//   test_cross_slope                 단위 시험
//   test_cross_slope IN.csv OUT.csv  맥 재현(ml/realdata/imu_calib/cross_slope_rt.py)과 대조: IN 줄 = t,ay,gz,spd,acc → OUT 줄 = 뺀 값
#include <cmath>
#include <cstdio>
#include <cstdlib>

#include "cross_slope.hpp"

using namespace mm::xslope;

static int failures = 0;
#define CHECK(cond, ...)                                     \
    do {                                                     \
        if (!(cond)) {                                       \
            failures++;                                      \
            std::printf("FAIL %s:%d: ", __FILE__, __LINE__); \
            std::printf(__VA_ARGS__);                        \
            std::printf("\n");                               \
        }                                                    \
    } while (0)

static const double D2R = kPi / 180.0;

// n초 동안 같은 값을 넣는다. 돌려줌: 마지막 뺀 값
static double feed(CrossSlope& x, double& t, double secs, double ay, double gz, double spd, double acc) {
    double a = 0;
    for (int i = 0; i < static_cast<int>(std::lround(secs * 100)); i++) {
        t += 0.01;
        a = x.push(t, ay, gz, spd, acc);
    }
    return a;
}

static void straight_offset_removed() {
    CrossSlope x;
    double t = 0;
    CHECK(feed(x, t, 5, 0.3, 0, 10, 4) == 0 && !x.has_estimate(), "first 5 s: not enough seconds yet, nothing removed");
    const double a = feed(x, t, 15, 0.3, 0, 10, 4);
    CHECK(x.has_estimate() && std::fabs(a - 0.3) < 1e-9, "straight +0.3 removed after 10 s + ramp (%.4f)", a);
    CHECK(x.used_seconds() == 19, "all straight seconds used except the first (lag 0.8 s) (%llu)", (unsigned long long)x.used_seconds());
}

static void rate_limited() {
    CrossSlope x;
    double t = 0, prev = 0, worst = 0;
    for (int i = 0; i < 3000; i++) {
        t += 0.01;
        const double a = x.push(t, 0.5, 0, 10, 4);
        worst = std::fmax(worst, std::fabs(a - prev));
        prev = a;
    }
    CHECK(worst <= 0.1 / 100 + 1e-12, "change per sample <= rate/100 (%.6f)", worst);
    CHECK(std::fabs(prev - 0.5) < 1e-9, "reaches the estimate");
}

static void curve_not_used_and_centripetal_kept() {
    // 직진 20초로 +0.2를 배운 뒤, 커브(gz 10 °/s, v 10 m/s → v·gz 1.745)에서 ay = v·gz + 0.2
    CrossSlope x;
    double t = 0;
    feed(x, t, 20, 0.2, 0, 10, 4);
    const uint64_t used = x.used_seconds();
    const double gz = 10 * D2R, ay = 10 * gz + 0.2;
    const double a = feed(x, t, 30, ay, gz, 10, 4);
    CHECK(x.used_seconds() == used, "curve seconds not used");
    CHECK(std::fabs(a - 0.2) < 1e-9, "estimate held through the curve: corrected ay = v·gz (%.4f)", a);
}

static void needs_gps() {
    CrossSlope x;
    double t = 0;
    CHECK(feed(x, t, 60, 0.3, 0, NAN, NAN) == 0 && x.used_seconds() == 0, "no speed: nothing removed");
    // 1초 중 일부만 속도가 있으면 그 초는 버린다
    CrossSlope y;
    t = 0;
    for (int i = 0; i < 6000; i++) {
        t += 0.01;
        y.push(t, 0.3, 0, (i % 100) < 50 ? 10 : NAN, 4);
    }
    CHECK(y.used_seconds() == 0, "half-second speed: not used");
}

static void bad_accuracy_not_used() {
    CrossSlope x;
    double t = 0;
    feed(x, t, 60, 0.3, 0, 10, 25);
    CHECK(x.used_seconds() == 0 && x.applied() == 0, "gps acc 25 m: not used");
    CrossSlope y;  // 정확도 모름(재생 trip)은 통과
    t = 0;
    feed(y, t, 20, 0.3, 0, 10, NAN);
    CHECK(y.used_seconds() == 19, "unknown accuracy: used");
}

static void window_follows_change() {
    // 30초 창: +0.4 다음 −0.1로 바뀌면 30초 안에 따라감 (지난 값만 보므로 그만큼 늦음)
    CrossSlope x;
    double t = 0;
    feed(x, t, 60, 0.4, 0, 10, 4);
    const double a = feed(x, t, 40, -0.1, 0, 10, 4);
    CHECK(std::fabs(a + 0.1) < 1e-9, "follows new slope within window + ramp (%.4f)", a);
}

static void stopped_uses_floor() {
    // 정차(v = 0, gz = 0): ay 자체가 그 자리 바닥 기울기 → 뺀다 (오프라인 cross_slope.py와 같음)
    CrossSlope x;
    double t = 0;
    const double a = feed(x, t, 30, 0.25, 0, 0, 4);
    CHECK(std::fabs(a - 0.25) < 1e-9, "stopped: floor tilt removed (%.4f)", a);
}

static void gps_lag_alignment() {
    // IMU에서 커브가 GPS 속도 변화보다 0.8초 먼저 보이는 것은 lag로 맞춘다: 속도가 바뀌는 직진에서 남는 값이 0
    CrossSlope x;
    double t = 0;
    for (int i = 0; i < 4000; i++) {
        t += 0.01;
        x.push(t, 0.0, 0, 5 + 5 * std::sin(i * 0.002), 4);
    }
    Second s;
    x.take_second(s);
    CHECK(std::fabs(s.resid) < 1e-12 && s.use, "gz 0: resid = ay (%.4f)", s.resid);
}

static void second_log() {
    CrossSlope x;
    double t = 0;
    Second s;
    int n = 0;
    for (int i = 0; i < 1000; i++) {
        t += 0.01;
        x.push(t, 0.3, 0, 10, 4);
        if (x.take_second(s)) n++;
    }
    CHECK(n == 10 && !x.take_second(s), "one log line per second (%d)", n);
    CHECK(std::isnan(Second{}.resid), "default resid NaN");
}

static int csv_mode(const char* in, const char* out) {
    std::FILE* f = std::fopen(in, "r");
    std::FILE* o = std::fopen(out, "w");
    if (!f || !o) return 2;
    CrossSlope x;
    char line[256];
    while (std::fgets(line, sizeof line, f)) {
        double v[5];
        char* p = line;
        for (double& d : v) {
            d = std::strtod(p, &p);
            if (*p == ',') p++;
        }
        std::fprintf(o, "%.9f\n", x.push(v[0], v[1], v[2], v[3], v[4]));
    }
    std::fclose(f);
    std::fclose(o);
    return 0;
}

int main(int argc, char** argv) {
    if (argc == 3) return csv_mode(argv[1], argv[2]);
    straight_offset_removed();
    rate_limited();
    curve_not_used_and_centripetal_kept();
    needs_gps();
    bad_accuracy_not_used();
    window_follows_change();
    stopped_uses_floor();
    gps_lag_alignment();
    second_log();
    if (failures) {
        std::printf("%d failure(s)\n", failures);
        return 1;
    }
    std::printf("cross_slope: all passed\n");
    return 0;
}
