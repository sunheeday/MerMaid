// 필터: 표준 C++만 사용 (QNX API 없음). ml/oman.py, ml/wf.py와 같은 식과 같은 계산 순서.
#pragma once
#include <array>
#include <cmath>

namespace mm {

constexpr double kPi = 3.14159265358979323846;  // M_PI는 표준이 아니라 QNX strict 모드에 없을 수 있음

// 2차 구간, 전치 직접형 II (scipy lfilter / sosfilt와 같은 구조)
struct Biquad {
    double b0 = 1, b1 = 0, b2 = 0, a1 = 0, a2 = 0;
    double z1 = 0, z2 = 0;
    double step(double x) {
        const double y = b0 * x + z1;
        z1 = b1 * x - a1 * y + z2;
        z2 = b2 * x - a2 * y;
        return y;
    }
    void reset() { z1 = z2 = 0; }
};

// 2차 버터워스 저역통과, 쌍선형 변환 + 사전 왜곡 (ml/oman.py butter2_lowpass)
inline Biquad butter2_lowpass(double fc, double fs) {
    const double k = std::tan(kPi * fc / fs);
    const double norm = 1.0 / (1.0 + std::sqrt(2.0) * k + k * k);
    Biquad q;
    q.b0 = k * k * norm;
    q.b1 = 2.0 * q.b0;
    q.b2 = q.b0;
    q.a1 = 2.0 * (k * k - 1.0) * norm;
    q.a2 = (1.0 - std::sqrt(2.0) * k + k * k) * norm;
    return q;
}

// 아날로그 (n2 s^2 + n1 s + n0) / (d2 s^2 + d1 s + d0) → 쌍선형 변환 (사전 왜곡 없음)
inline Biquad bilinear2(double n2, double n1, double n0, double d2, double d1, double d0, double fs) {
    const double K = 2.0 * fs, K2 = K * K;
    const double B0 = n2 * K2 + n1 * K + n0, B1 = 2.0 * (n0 - n2 * K2), B2 = n2 * K2 - n1 * K + n0;
    const double A0 = d2 * K2 + d1 * K + d0, A1 = 2.0 * (d0 - d2 * K2), A2 = d2 * K2 - d1 * K + d0;
    Biquad q;
    q.b0 = B0 / A0; q.b1 = B1 / A0; q.b2 = B2 / A0;
    q.a1 = A1 / A0; q.a2 = A2 / A0;
    return q;
}

// ISO 2631-1 Wf: Hh, Hl, Ht, Hs 순서 (constants.json "wf")
struct Wf {
    std::array<Biquad, 4> s;
    Wf(double fs, double f1, double f2, double q1, double f4, double q4, double f5, double q5, double f6, double q6) {
        const auto w = [](double f) { return 2.0 * kPi * f; };
        const double w1 = w(f1), w2 = w(f2), w4 = w(f4), w5 = w(f5), w6 = w(f6);
        s[0] = bilinear2(1, 0, 0, 1, w1 / q1, w1 * w1, fs);
        s[1] = bilinear2(0, 0, w2 * w2, 1, w2 / q1, w2 * w2, fs);
        s[2] = bilinear2(0, 0, w4 * w4, 1, w4 / q4, w4 * w4, fs);
        s[3] = bilinear2(1, w5 / q5, w5 * w5, 1, w6 / q6, w6 * w6, fs);
    }
    double step(double x) {
        for (auto& q : s) x = q.step(x);
        return x;
    }
    void reset() {
        for (auto& q : s) q.reset();
    }
};

// 1차 저역통과 2단, y = a y + (1 - a) x, a = exp(-dt / beta) (ml/oman.py lp2)
struct Lp2 {
    double al = 0, y1 = 0, y2 = 0;
    Lp2(double beta = 1, double dt = 0.01) : al(std::exp(-dt / beta)) {}
    double step(double x) {
        y1 = (1.0 - al) * x + al * y1;
        y2 = (1.0 - al) * y1 + al * y2;
        return y2;
    }
    void reset() { y1 = y2 = 0; }
};

// Oman 모델 (ml/oman.py run): S를 먼저 갱신하고 갱신된 S로 F를 계산
struct Oman {
    Lp2 slow, fast;
    double K, exponent, S = 0, F = 0;
    Oman(double K_, double beta_fast, double beta_slow, double exponent_, double dt)
        : slow(beta_slow, dt), fast(beta_fast, dt), K(K_), exponent(exponent_) {}
    void step(double c) {
        S = slow.step(c);
        F = fast.step(K * S * c);
    }
    double score() const { return std::pow(std::fmax(F + S, 0.0), exponent); }
    void reset() { slow.reset(); fast.reset(); S = F = 0; }
};

}  // namespace mm
