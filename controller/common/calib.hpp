// 개인 보정 (docs/controller/DATA_LOGGING.md §12): p' = σ(a·logit(p) + b), 사람마다 a, b 하나씩.
// k = 2..6에 같은 a > 0, b를 쓰므로 σ(a·logit(p) + b)가 p에 대해 증가해 p2 ≥ … ≥ p6 순서가 유지된다.
// a = 1, b = 0이면 계산을 건너뛰어 입력과 비트 단위로 같은 값을 돌려준다.
// Python 쪽 같은 식: ml/personal/fit_calib.py apply_calib (ml/tests/test_calib.py가 둘을 비교).
#pragma once
#include <algorithm>
#include <cmath>

#include "policy.hpp"  // Risk

namespace mm::calib {

constexpr double kAMin = 0.5, kAMax = 2.0;  // §12-4
constexpr double kBMax = 4.0;               // |b| 상한 (fit_calib.py와 같음)
constexpr double kPClip = 1e-6;             // logit이 무한대가 되지 않게

struct Calib {
    double a = 1, b = 0;
};

inline bool identity(const Calib& c) { return c.a == 1.0 && c.b == 0.0; }

inline bool valid(const Calib& c) {
    return std::isfinite(c.a) && std::isfinite(c.b) && c.a >= kAMin && c.a <= kAMax && std::fabs(c.b) <= kBMax;
}

inline double apply_one(double p, const Calib& c) {
    const double q = std::clamp(p, kPClip, 1 - kPClip);
    const double z = c.a * std::log(q / (1 - q)) + c.b;
    return 1 / (1 + std::exp(-z));
}

// 잘못된 값(범위 밖, NaN)이면 보정 없이 그대로
inline Risk apply(const Risk& p, const Calib& c) {
    if (identity(c) || !valid(c)) return p;
    Risk out;
    for (size_t k = 0; k < p.size(); k++) out[k] = apply_one(p[k], c);
    return out;
}

}  // namespace mm::calib
