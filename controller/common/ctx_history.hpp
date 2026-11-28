// V3 문맥 모델: 승객별 과거 토큰 저장 (P3). 파일 하나 = 승객 하나, <dir>/<승객 id>.ctx
// 토큰 = [요약 e (summary_dim) ‖ 장치 원핫 3 ‖ 라벨 1]. 파일에는 e, 장치, 라벨, 몇 번째 주행이었는지만 둔다.
//
// 형식 (리틀 엔디언): "MMCTX1\0\0" · int32 summary_dim · int32 rides(이 승객이 토큰을 남긴 주행 수) · int32 n
//                     · 토큰 n개 × [int32 ride · uint8 device · uint8 label · uint16 0 · float32 e[summary_dim]] (시간 순)
// 쓰기는 store::write_atomic (임시 파일 → rename). 요약 차원이 다른 파일(다른 모델)은 읽지 않고 비운 것으로 본다.
//
// 보관 한도: 장치·라벨마다 최근 kQuota개는 항상, 나머지는 최근 keep개. select_tokens(k ≤ keep)가 고르는 토큰은
// 전부 남으므로, 한도 때문에 차에서 고르는 문맥이 학습 때 규칙과 달라지지 않는다.
#pragma once
#include <cstdint>
#include <string>
#include <vector>

#include "ctx_model.hpp"

namespace mm::ctx {

struct Token {
    int32_t ride = 0;
    int device = 0;
    int label = 0;
    std::vector<float> e;
};

class History {
public:
    History(std::string dir, int summary_dim, int keep = 1024);

    // 없거나 요약 차원이 다르면 빈 목록 (rides = 0)
    std::vector<Token> load(const std::string& passenger, int32_t* rides = nullptr) const;
    // 이번 주행의 토큰을 뒤에 붙이고 한도에 맞춰 줄여 저장. 붙인 토큰이 없으면 아무것도 쓰지 않는다
    void append(const std::string& passenger, const std::vector<Token>& ride_tokens) const;
    // 문맥 행렬: 고른 토큰을 시간 순으로 [e ‖ 원핫 ‖ 라벨] (n × (summary_dim + 4))
    std::vector<float> context(const std::vector<Token>& all, int k, int* n_out) const;

private:
    std::string path(const std::string& passenger) const;
    std::string dir_;
    int de_;
    int keep_;
};

}  // namespace mm::ctx
