// V3 문맥 모델 추론. 학습은 ml/experiments/v6/context6.py, 내보내기는 export_ctx.py.
// 외부 라이브러리 없이 표준 C++17만 쓴다 (lib_lightgbm 필요 없음). float32로 PyTorch와 같은 계산.
//
// 모델 폴더: models.json ("kind": "ctx_gru" 또는 "ctx_attn"), manifest.json (텐서 이름·모양·오프셋, 입력 열 이름,
//            stage1 / stage2 종류), weights.bin (float32 LE)
//
// 1단계 (요약 e, 32)   입력 z = clip((x - mu) / sd, -5, 5) (NaN → 0), m = isfinite(x)
//   mlp   e = L3(gelu(L2(gelu(L1([z ‖ m])))))
//   ft    피처 토큰 t_i = m_i ? z_i·w_i + b_i : miss_i,  [cls, t_1..t_nf] → 트랜스포머 인코더 → e = 첫 칸
//   p0 = sigmoid(head(e))  문맥 없는 공통 모델 (첫 탑승 참고용)
// 토큰   c = [e (32) ‖ onehot(장치, 3) ‖ 켰나 (1)] = 36,  지난 탑승들의 것만
// 2단계
//   gru   h = GRU(pc(c_1) … pc(c_n)) 의 마지막 은닉 (n = 0 이면 0),  p = sigmoid(head_1(gelu(head_0([pq(e) ‖ h]))))
//   attn  [pq(e) + type_0, pc(c_1) + type_1, …] → 트랜스포머 인코더 → p = sigmoid(head(첫 칸))
// 트랜스포머 인코더 층 = PyTorch nn.TransformerEncoderLayer 기본값 (post-norm, ReLU, LayerNorm eps 1e-5):
//   x = LN1(x + MHA(x)),  x = LN2(x + W2·relu(W1·x)).  마지막 층은 첫 칸만 계산한다 (나머지는 출력에 안 쓰임).
//
// 문맥은 지난 탑승들의 토큰이라 한 탑승 안에서는 바뀌지 않는다. prepare()를 탑승마다 승객별로 한 번 부른다.
//   gru:  h를 여기서 다 계산한다 → 10초 주기는 summarize + 머리만.
//   attn: 토큰 투영 pc(c)+type_1만 미리 둔다. 지금 상황 토큰이 과거 토큰과 서로 보므로 인코더는 주기마다 돈다.
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

    // 한 승객의 문맥 (prepare가 채운다). gru: v = h (hidden_dim).
    // attn: v = n × hidden_dim 투영된 토큰, qkv1 = 그 토큰들의 첫 층 Q·K·V (n × 3·hidden_dim)
    struct Ctx {
        int n = 0;
        std::vector<float> v;
        std::vector<float> qkv1;
    };

    // dir/models.json, manifest.json, weights.bin. 입력 열이 protocol의 켜진 칸과 이름으로 맞지 않으면 예외
    explicit CtxModel(const std::string& dir);

    int n_inputs() const { return nf_; }          // 켜진 피처 수 (v5: 49)
    int summary_dim() const { return de_; }       // 32
    int token_dim() const { return de_ + kHeads + 1; }
    int hidden_dim() const { return d2_; }        // 64
    int k() const { return k_; }                  // 학습 때 문맥 길이 (128)
    const std::string& stage1() const { return s1_; }  // "mlp" | "ft"
    const std::string& stage2() const { return s2_; }  // "gru" | "attn"
    // 입력 i가 protocol 52칸 벡터의 몇 번째 칸인지
    const std::vector<int>& input_slots() const { return slot_; }

    // 1단계: protocol 52칸 벡터 → 요약 e[summary_dim]
    void summarize(const double* x_protocol, float* e) const;
    // 1단계: 모델 입력 순서의 값 x[n_inputs] (패리티 시험용)
    void summarize_inputs(const double* x, float* e) const;
    // 문맥 없는 공통 확률 (1단계 머리)
    void common(const float* e, float* p) const;
    // 2단계 문맥 준비: 토큰 n개 (행 우선, token_dim 칸씩, 시간 순)
    void prepare(const float* tokens, int n, Ctx& c) const;
    // 2단계: e와 준비된 문맥 → p[kHeads]
    void predict(const float* e, const Ctx& c, float* p) const;

    struct Lin {
        int in = 0, out = 0;
        const float* W = nullptr;  // (out, in) 행 우선 = PyTorch weight
        const float* b = nullptr;
        void operator()(const float* x, float* y) const;
    };
    struct Norm {
        int d = 0;
        const float* g = nullptr;
        const float* b = nullptr;
    };
    struct EncLayer {
        int d = 0, heads = 0;
        Lin qkv, out, l1, l2;  // qkv = in_proj (3d × d, 순서 q, k, v)
        Norm n1, n2;
    };

private:
    Lin lin(const std::string& prefix) const;
    Norm norm(const std::string& prefix, int d) const;
    std::vector<EncLayer> enc(const std::string& prefix, int d, int heads) const;
    const float* tensor(const std::string& name, const std::vector<int>& shape) const;

    std::vector<float> w_;
    int nf_ = 0, de_ = 0, d2_ = 0, k_ = 0;
    std::string s1_, s2_;
    std::vector<int> slot_;
    const float* mu_ = nullptr;
    const float* sd_ = nullptr;
    Lin head1_, pq_, pc_;
    // 1단계 mlp
    Lin b0_, b3_, b5_;
    // 1단계 ft
    const float *ft_w_ = nullptr, *ft_b_ = nullptr, *ft_miss_ = nullptr, *ft_cls_ = nullptr;
    std::vector<EncLayer> ft_enc_;
    // 2단계 gru
    Lin h0_, h3_;
    const float *Wih_ = nullptr, *Whh_ = nullptr, *bih_ = nullptr, *bhh_ = nullptr;
    // 2단계 attn
    const float* type_ = nullptr;  // (2, d2)
    std::vector<EncLayer> at_enc_;
    Lin at_head_;
    struct Ref { size_t off = 0, n = 0; std::vector<int> shape; };
    std::vector<std::pair<std::string, Ref>> refs_;
    const Ref& ref(const std::string& name) const;
    bool has(const std::string& name) const;
};

// 트랜스포머 인코더 층들을 x (n × d, 행 우선)에 차례로. last_first_only면 마지막 층은 첫 칸만 계산하고 x는 1행이 된다.
// qkv1_rest: 첫 층에서 1..n-1행의 Q·K·V를 미리 계산해 둔 것 ((n-1) × 3d, 없으면 nullptr)
void encode(const std::vector<CtxModel::EncLayer>& layers, std::vector<float>& x, int n, bool last_first_only,
            const float* qkv1_rest = nullptr);
// 8칸 부분합 내적
float dot(const float* a, const float* b, int n);

// 문맥에 넣을 토큰 고르기 (context6.select_tokens, recent=True와 같음).
// 후보는 시간 순. 반환은 고른 후보의 번호(시간 순 유지). 후보가 k개 이하면 전부.
struct TokenMeta {
    int device = 0;  // 0 fan, 1 ambient, 2 dots
    int label = 0;   // 1 켰음, 0 켤 수 있었는데 안 켬
};
std::vector<int> select_tokens(const std::vector<TokenMeta>& cand, int k, int quota = CtxModel::kQuota);

}  // namespace mm
