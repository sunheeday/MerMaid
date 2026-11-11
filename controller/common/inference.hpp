// P3 추론: LightGBM 4.6.0 C API로 model_2.txt ~ model_6.txt를 로드해 P(>=k)를 낸다 (PRD §P3, §4.3).
// 평가기를 새로 짜지 않고 lib_lightgbm을 그대로 쓴다. 단조 보정 p[k] = min(p[k], p[k-1]).
#pragma once
#include <array>
#include <string>
#include <vector>

#include "policy.hpp"  // Risk

namespace mm {

class Inference {
public:
    // model_dir/model_k.txt (k = 2..6). 피처 수가 77이 아니면 예외 (시작 거부)
    explicit Inference(const std::string& model_dir);
    ~Inference();
    Inference(const Inference&) = delete;
    Inference& operator=(const Inference&) = delete;

    // X: n x 77 행 우선 float64. 결과는 행마다 단조 보정된 P(>=2..6)
    std::vector<Risk> predict(const double* X, int n) const;
    Risk predict_one(const double* x) const { return predict(x, 1)[0]; }

    double load_ms() const { return load_ms_; }
    const std::array<int, 5>& iterations() const { return iters_; }

private:
    std::array<void*, 5> h_{};
    std::array<int, 5> iters_{};
    double load_ms_ = 0;
};

// P(>=3..6)가 P(>=k-1)보다 크지 않게
inline void monotone_fix(Risk& p) {
    for (size_t k = 1; k < p.size(); k++)
        if (p[k] > p[k - 1]) p[k] = p[k - 1];
}

}  // namespace mm
