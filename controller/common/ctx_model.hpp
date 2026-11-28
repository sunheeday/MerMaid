// V3 문맥 모델 (MLP 요약 + GRU) 추론. 학습은 ml/experiments/v6/context6.py, 내보내기는 export_ctx.py.
// 외부 라이브러리 없이 표준 C++17만 쓴다 (lib_lightgbm 필요 없음). float32로 PyTorch와 같은 계산.
//
// 모델 폴더: models.json ("kind": "ctx_gru"), manifest.json (텐서 이름·모양·오프셋, 입력 열 이름), weights.bin (float32 LE)
//
// 계산 (export_ctx.py 머리글과 같음)
//   1단계  z = clip((x - mu) / sd, -5, 5) (NaN → 0), m = isfinite(x)
//          e = L3(gelu(L2(gelu(L1([z ‖ m])))))                     요약 32
//          p0 = sigmoid(head(e))                                     문맥 없는 공통 모델 (첫 탑승)
//   토큰   c = [e (32) ‖ onehot(장치, 3) ‖ 켰나 (1)] = 36,  지난 탑승들의 것만
//   2단계  h = GRU(pc(c_1) … pc(c_n)) 의 마지막 은닉 (n = 0 이면 0)
//          p = sigmoid(head_1(gelu(head_0([pq(e) ‖ h]))))           fan, ambient, dots
//
// 문맥은 지난 탑승들의 토큰이라 한 탑승 안에서는 바뀌지 않는다. 그래서 h는 탑승 시작에 한 번 계산하고(history),
// 10초 주기마다는 summarize + predict만 한다.
#pragma once
#include <array>
#include <cstdint>
#include <string>
#include <vector>

namespace mm {

class CtxModel {
public:
    static constexpr int kHeads = 3;   // fan, ambient, dots
    static constexpr int kQuota = 3;   // context6.QUOTA: 장치·라벨마다 최근 토큰을 최소 이만큼 남긴다

    // dir/models.json, manifest.json, weights.bin. 입력 열이 protocol의 켜진 칸과 이름으로 맞지 않으면 예외
    explicit CtxModel(const std::string& dir);

    int n_inputs() const { return nf_; }          // 켜진 피처 수 (v5: 49)
    int summary_dim() const { return de_; }       // 32
    int token_dim() const { return de_ + kHeads + 1; }
    int hidden_dim() const { return d2_; }        // 64
    int k() const { return k_; }                  // 학습 때 문맥 길이 (128)
    // 입력 i가 protocol 52칸 벡터의 몇 번째 칸인지
    const std::vector<int>& input_slots() const { return slot_; }

    // 1단계: protocol 52칸 벡터 → 요약 e[summary_dim]
    void summarize(const double* x_protocol, float* e) const;
    // 1단계: 모델 입력 순서의 값 x[n_inputs] (패리티 시험용)
    void summarize_inputs(const double* x, float* e) const;
    // 문맥 없는 공통 확률 (1단계 머리)
    void common(const float* e, float* p) const;
    // 2단계 GRU: 토큰 n개 (행 우선, token_dim 칸씩, 시간 순) → h[hidden_dim]
    void history(const float* tokens, int n, float* h) const;
    // 2단계 머리: e, h → p[kHeads]
    void predict(const float* e, const float* h, float* p) const;

private:
    struct Lin {
        int in = 0, out = 0;
        const float* W = nullptr;  // (out, in) 행 우선 = PyTorch weight
        const float* b = nullptr;
        void operator()(const float* x, float* y) const;
    };
    Lin lin(const std::string& prefix) const;

    std::vector<float> w_;
    int nf_ = 0, de_ = 0, d2_ = 0, k_ = 0;
    std::vector<int> slot_;
    const float* mu_ = nullptr;
    const float* sd_ = nullptr;
    Lin b0_, b3_, b5_, head1_, pq_, pc_, h0_, h3_;
    const float *Wih_ = nullptr, *Whh_ = nullptr, *bih_ = nullptr, *bhh_ = nullptr;
    struct Ref { size_t off = 0, n = 0; std::vector<int> shape; };
    std::vector<std::pair<std::string, Ref>> refs_;
    const Ref& ref(const std::string& name) const;
};

// 문맥에 넣을 토큰 고르기 (context6.select_tokens, recent=True와 같음).
// 후보는 시간 순. 반환은 고른 후보의 번호(시간 순 유지). 후보가 k개 이하면 전부.
struct TokenMeta {
    int device = 0;  // 0 fan, 1 ambient, 2 dots
    int label = 0;   // 1 켰음, 0 켤 수 있었는데 안 켬
};
std::vector<int> select_tokens(const std::vector<TokenMeta>& cand, int k, int quota = CtxModel::kQuota);

}  // namespace mm
