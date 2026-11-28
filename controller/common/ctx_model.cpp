#include "ctx_model.hpp"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <fstream>
#include <sstream>
#include <stdexcept>

#include "json_min.hpp"
#include "protocol_gen.hpp"

namespace mm {

namespace {

std::string read_text(const std::string& path) {
    std::ifstream in(path, std::ios::binary);
    if (!in) throw std::runtime_error("ctx: cannot open " + path);
    std::ostringstream ss;
    ss << in.rdbuf();
    return ss.str();
}

// PyTorch nn.GELU() 기본값 (erf)
inline float gelu(float x) { return 0.5f * x * (1.f + std::erf(x * 0.70710678118654752f)); }
inline float sigm(float x) { return 1.f / (1.f + std::exp(-x)); }

}  // namespace

void CtxModel::Lin::operator()(const float* x, float* y) const {
    for (int o = 0; o < out; ++o) {
        const float* r = W + static_cast<size_t>(o) * in;
        float s = b[o];
        for (int i = 0; i < in; ++i) s += r[i] * x[i];
        y[o] = s;
    }
}

const CtxModel::Ref& CtxModel::ref(const std::string& name) const {
    for (const auto& kv : refs_)
        if (kv.first == name) return kv.second;
    throw std::runtime_error("ctx: missing tensor " + name);
}

CtxModel::Lin CtxModel::lin(const std::string& p) const {
    const Ref& w = ref(p + ".weight");
    const Ref& b = ref(p + ".bias");
    if (w.shape.size() != 2 || b.n != static_cast<size_t>(w.shape[0]))
        throw std::runtime_error("ctx: bad linear " + p);
    Lin l;
    l.out = w.shape[0];
    l.in = w.shape[1];
    l.W = w_.data() + w.off;
    l.b = w_.data() + b.off;
    return l;
}

CtxModel::CtxModel(const std::string& dir) {
    const json::Value models = json::parse(read_text(dir + "/models.json"));
    if (models.at("kind").string() != "ctx_gru") throw std::runtime_error("ctx: models.json kind is not ctx_gru");
    if (models.at("feature_order_version").integer() != proto::kFeatureVersion)
        throw std::runtime_error("ctx: feature_order_version " + std::to_string(models.at("feature_order_version").integer()) +
                                 " != controller " + std::to_string(proto::kFeatureVersion));
    const json::Value m = json::parse(read_text(dir + "/manifest.json"));
    nf_ = m.at("n_features").integer();
    de_ = m.at("d_summary").integer();
    d2_ = m.at("d2").integer();
    k_ = m.at("k").integer();
    if (m.at("n_heads").integer() != kHeads) throw std::runtime_error("ctx: n_heads != 3");
    if (m.at("gru_gate_order").string() != "r,z,n") throw std::runtime_error("ctx: gru gate order");
    if (m.at("dtype").string() != "float32-le") throw std::runtime_error("ctx: dtype");

    // 입력 열 → protocol 칸. 켜진 칸 전부를 정확히 한 번씩 써야 한다 (학습과 같은 입력 집합)
    const auto& names = m.at("feature_names").arr;
    if (static_cast<int>(names.size()) != nf_) throw std::runtime_error("ctx: feature_names size");
    int enabled = 0;
    for (int f = 0; f < proto::kNumFeatures; f++) enabled += proto::kFeatureEnabled[f] ? 1 : 0;
    if (nf_ != enabled)
        throw std::runtime_error("ctx: model has " + std::to_string(nf_) + " inputs, protocol has " + std::to_string(enabled) +
                                 " enabled features");
    for (const auto& v : names) {
        int s = -1;
        for (int f = 0; f < proto::kNumFeatures; f++)
            if (v.string() == proto::kFeatureNames[f]) s = f;
        if (s < 0) throw std::runtime_error("ctx: unknown feature " + v.string());
        if (!proto::kFeatureEnabled[s]) throw std::runtime_error("ctx: feature " + v.string() + " is disabled in protocol");
        if (std::find(slot_.begin(), slot_.end(), s) != slot_.end()) throw std::runtime_error("ctx: duplicate " + v.string());
        slot_.push_back(s);
    }

    const size_t total = static_cast<size_t>(m.at("total_floats").number());
    w_.resize(total);
    {
        std::ifstream in(dir + "/weights.bin", std::ios::binary);
        if (!in) throw std::runtime_error("ctx: cannot open weights.bin");
        in.read(reinterpret_cast<char*>(w_.data()), static_cast<std::streamsize>(total * sizeof(float)));
        if (static_cast<size_t>(in.gcount()) != total * sizeof(float)) throw std::runtime_error("ctx: weights.bin too short");
        char extra;
        if (in.read(&extra, 1)) throw std::runtime_error("ctx: weights.bin too long");
    }
    for (const auto& t : m.at("tensors").arr) {
        Ref r;
        r.off = static_cast<size_t>(t.at("offset_floats").number());
        r.n = static_cast<size_t>(t.at("n").number());
        size_t prod = 1;
        for (const auto& d : t.at("shape").arr) {
            r.shape.push_back(d.integer());
            prod *= static_cast<size_t>(d.integer());
        }
        if (prod != r.n || r.off + r.n > total) throw std::runtime_error("ctx: bad tensor " + t.at("name").string());
        refs_.emplace_back(t.at("name").string(), r);
    }

    const Ref& mu = ref("mu");
    const Ref& sd = ref("sd");
    if (mu.n != static_cast<size_t>(nf_) || sd.n != static_cast<size_t>(nf_)) throw std::runtime_error("ctx: mu/sd size");
    mu_ = w_.data() + mu.off;
    sd_ = w_.data() + sd.off;
    b0_ = lin("stage1.body.0");
    b3_ = lin("stage1.body.3");
    b5_ = lin("stage1.body.5");
    head1_ = lin("stage1.head");
    pq_ = lin("stage2.pq");
    pc_ = lin("stage2.pc");
    h0_ = lin("stage2.head.0");
    h3_ = lin("stage2.head.3");
    const Ref& wih = ref("stage2.gru.weight_ih_l0");
    const Ref& whh = ref("stage2.gru.weight_hh_l0");
    Wih_ = w_.data() + wih.off;
    Whh_ = w_.data() + whh.off;
    bih_ = w_.data() + ref("stage2.gru.bias_ih_l0").off;
    bhh_ = w_.data() + ref("stage2.gru.bias_hh_l0").off;

    // 모양 점검: 앞 층의 출력이 다음 층의 입력과 맞는지
    auto need = [](bool ok, const char* what) {
        if (!ok) throw std::runtime_error(std::string("ctx: shape mismatch ") + what);
    };
    need(b0_.in == 2 * nf_ && b3_.in == b0_.out && b5_.in == b3_.out && b5_.out == de_, "stage1 body");
    need(head1_.in == de_ && head1_.out == kHeads, "stage1 head");
    need(pq_.in == de_ && pq_.out == d2_ && pc_.in == de_ + kHeads + 1 && pc_.out == d2_, "stage2 projections");
    need(wih.shape == std::vector<int>{3 * d2_, d2_} && whh.shape == std::vector<int>{3 * d2_, d2_}, "gru");
    need(h0_.in == 2 * d2_ && h3_.in == h0_.out && h3_.out == kHeads, "stage2 head");
}

void CtxModel::summarize_inputs(const double* x, float* e) const {
    std::vector<float> in(2 * nf_), a(b0_.out), b(b3_.out);
    for (int i = 0; i < nf_; i++) {
        const bool ok = std::isfinite(x[i]);
        double z = ok ? (x[i] - mu_[i]) / sd_[i] : 0.0;  // common6.standardize: 64비트로 빼고 나눈 뒤 float32
        if (!std::isfinite(z)) z = 0.0;
        z = std::min(5.0, std::max(-5.0, z));
        in[i] = static_cast<float>(z);
        in[nf_ + i] = ok ? 1.f : 0.f;
    }
    b0_(in.data(), a.data());
    for (float& v : a) v = gelu(v);
    b3_(a.data(), b.data());
    for (float& v : b) v = gelu(v);
    b5_(b.data(), e);
}

void CtxModel::summarize(const double* x_protocol, float* e) const {
    std::vector<double> x(nf_);
    for (int i = 0; i < nf_; i++) x[i] = x_protocol[slot_[i]];
    summarize_inputs(x.data(), e);
}

void CtxModel::common(const float* e, float* p) const {
    head1_(e, p);
    for (int j = 0; j < kHeads; j++) p[j] = sigm(p[j]);
}

void CtxModel::history(const float* tokens, int n, float* h) const {
    const int H = d2_;
    std::fill(h, h + H, 0.f);
    std::vector<float> x(H), gi(3 * H), gh(3 * H);
    for (int t = 0; t < n; t++) {
        pc_(tokens + static_cast<size_t>(t) * token_dim(), x.data());
        for (int o = 0; o < 3 * H; ++o) {
            const float* r = Wih_ + static_cast<size_t>(o) * H;
            const float* q = Whh_ + static_cast<size_t>(o) * H;
            float s = bih_[o], u = bhh_[o];
            for (int i = 0; i < H; ++i) {
                s += r[i] * x[i];
                u += q[i] * h[i];
            }
            gi[o] = s;
            gh[o] = u;
        }
        for (int i = 0; i < H; ++i) {
            const float r = sigm(gi[i] + gh[i]);
            const float z = sigm(gi[H + i] + gh[H + i]);
            const float nn = std::tanh(gi[2 * H + i] + r * gh[2 * H + i]);
            h[i] = (1.f - z) * nn + z * h[i];
        }
    }
}

void CtxModel::predict(const float* e, const float* h, float* p) const {
    std::vector<float> cat(2 * d2_), a(h0_.out);
    pq_(e, cat.data());
    std::copy(h, h + d2_, cat.begin() + d2_);
    h0_(cat.data(), a.data());
    for (float& v : a) v = gelu(v);
    h3_(a.data(), p);
    for (int j = 0; j < kHeads; j++) p[j] = sigm(p[j]);
}

std::vector<int> select_tokens(const std::vector<TokenMeta>& cand, int k, int quota) {
    const int n = static_cast<int>(cand.size());
    std::vector<int> out;
    if (n <= k) {
        for (int i = 0; i < n; i++) out.push_back(i);
        return out;
    }
    std::vector<char> pick(n, 0);
    for (int j = 0; j < CtxModel::kHeads; j++)
        for (int lab : {1, 0}) {
            std::vector<int> idx;
            for (int i = 0; i < n; i++)
                if (cand[i].device == j && cand[i].label == lab) idx.push_back(i);
            const size_t from = idx.size() > static_cast<size_t>(quota) ? idx.size() - quota : 0;
            for (size_t q = from; q < idx.size(); q++) pick[idx[q]] = 1;
        }
    int picked = 0;
    for (char c : pick) picked += c;
    const int room = k - picked;
    if (room > 0) {
        std::vector<int> rest;
        for (int i = 0; i < n; i++)
            if (!pick[i]) rest.push_back(i);
        const size_t from = rest.size() > static_cast<size_t>(room) ? rest.size() - room : 0;
        for (size_t q = from; q < rest.size(); q++) pick[rest[q]] = 1;
    } else if (room < 0) {
        std::vector<int> idx;
        for (int i = 0; i < n; i++)
            if (pick[i]) idx.push_back(i);
        std::fill(pick.begin(), pick.end(), 0);
        for (size_t q = idx.size() - k; q < idx.size(); q++) pick[idx[q]] = 1;
    }
    for (int i = 0; i < n; i++)
        if (pick[i]) out.push_back(i);
    return out;
}

}  // namespace mm
