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

// 내적: 8칸 부분합 (컴파일러가 NEON/SSE로 묶는다). 더하는 순서가 PyTorch와 달라 차이는 float 반올림 수준(1e-7)
float dot(const float* a, const float* b, int n) {
    float acc[8] = {0, 0, 0, 0, 0, 0, 0, 0};
    int i = 0;
    for (; i + 8 <= n; i += 8)
        for (int l = 0; l < 8; l++) acc[l] += a[i + l] * b[i + l];
    float s = ((acc[0] + acc[4]) + (acc[1] + acc[5])) + ((acc[2] + acc[6]) + (acc[3] + acc[7]));
    for (; i < n; i++) s += a[i] * b[i];
    return s;
}

void CtxModel::Lin::operator()(const float* x, float* y) const {
    for (int o = 0; o < out; ++o) y[o] = b[o] + dot(W + static_cast<size_t>(o) * in, x, in);
}

const CtxModel::Ref& CtxModel::ref(const std::string& name) const {
    for (const auto& kv : refs_)
        if (kv.first == name) return kv.second;
    throw std::runtime_error("ctx: missing tensor " + name);
}

bool CtxModel::has(const std::string& name) const {
    for (const auto& kv : refs_)
        if (kv.first == name) return true;
    return false;
}

const float* CtxModel::tensor(const std::string& name, const std::vector<int>& shape) const {
    const Ref& r = ref(name);
    if (r.shape != shape) throw std::runtime_error("ctx: shape of " + name);
    return w_.data() + r.off;
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

CtxModel::Norm CtxModel::norm(const std::string& p, int d) const {
    Norm n;
    n.d = d;
    n.g = tensor(p + ".weight", {d});
    n.b = tensor(p + ".bias", {d});
    return n;
}

std::vector<CtxModel::EncLayer> CtxModel::enc(const std::string& p, int d, int heads) const {
    if (heads <= 0 || d % heads) throw std::runtime_error("ctx: bad head count for " + p);
    std::vector<EncLayer> L;
    for (int i = 0; has(p + ".layers." + std::to_string(i) + ".linear1.weight"); i++) {
        const std::string q = p + ".layers." + std::to_string(i);
        EncLayer e;
        e.d = d;
        e.heads = heads;
        e.qkv.in = d;
        e.qkv.out = 3 * d;
        e.qkv.W = tensor(q + ".self_attn.in_proj_weight", {3 * d, d});
        e.qkv.b = tensor(q + ".self_attn.in_proj_bias", {3 * d});
        e.out = lin(q + ".self_attn.out_proj");
        e.l1 = lin(q + ".linear1");
        e.l2 = lin(q + ".linear2");
        e.n1 = norm(q + ".norm1", d);
        e.n2 = norm(q + ".norm2", d);
        if (e.out.in != d || e.out.out != d || e.l1.in != d || e.l2.in != e.l1.out || e.l2.out != d)
            throw std::runtime_error("ctx: shape mismatch in " + q);
        L.push_back(e);
    }
    if (L.empty()) throw std::runtime_error("ctx: no encoder layers under " + p);
    if (has(p + ".norm.weight")) throw std::runtime_error("ctx: encoder final norm is not supported (" + p + ")");
    return L;
}

namespace {

void layer_norm(const CtxModel::Norm& n, float* x) {
    float mean = 0.f;
    for (int i = 0; i < n.d; i++) mean += x[i];
    mean /= static_cast<float>(n.d);
    float var = 0.f;
    for (int i = 0; i < n.d; i++) var += (x[i] - mean) * (x[i] - mean);
    var /= static_cast<float>(n.d);
    const float inv = 1.f / std::sqrt(var + 1e-5f);
    for (int i = 0; i < n.d; i++) x[i] = (x[i] - mean) * inv * n.g[i] + n.b[i];
}

}  // namespace

void encode(const std::vector<CtxModel::EncLayer>& layers, std::vector<float>& x, int n, bool last_first_only,
            const float* qkv1_rest) {
    for (size_t li = 0; li < layers.size(); li++) {
        const CtxModel::EncLayer& L = layers[li];
        const int d = L.d, H = L.heads, dh = d / H, d3 = 3 * d;
        const bool last_one = last_first_only && li + 1 == layers.size();
        const int rows = last_one ? 1 : n;
        std::vector<float> qkv(static_cast<size_t>(n) * d3);
        for (int r = 0; r < n; r++) {
            float* o = &qkv[static_cast<size_t>(r) * d3];
            const float* xr = &x[static_cast<size_t>(r) * d];
            if (li == 0 && qkv1_rest && r > 0) {  // 첫 층, 미리 계산해 둔 행
                std::copy(qkv1_rest + static_cast<size_t>(r - 1) * d3, qkv1_rest + static_cast<size_t>(r) * d3, o);
                continue;
            }
            const int from = (r >= rows) ? d : 0;  // 질의로 안 쓰이는 행은 K, V만
            for (int j = from; j < d3; j++) o[j] = L.qkv.b[j] + dot(L.qkv.W + static_cast<size_t>(j) * d, xr, d);
        }
        const float scale = 1.f / std::sqrt(static_cast<float>(dh));
        std::vector<float> y(static_cast<size_t>(rows) * d), att(d), o(d), s(n), f(L.l1.out), g(d);
        for (int r = 0; r < rows; r++) {
            const float* q = &qkv[static_cast<size_t>(r) * d3];
            for (int h = 0; h < H; h++) {
                float mx = -INFINITY;
                for (int j = 0; j < n; j++) {
                    s[j] = dot(q + h * dh, &qkv[static_cast<size_t>(j) * d3 + d + h * dh], dh) * scale;
                    mx = std::max(mx, s[j]);
                }
                float sum = 0.f;
                for (int j = 0; j < n; j++) {
                    s[j] = std::exp(s[j] - mx);
                    sum += s[j];
                }
                float* a = &att[h * dh];
                std::fill(a, a + dh, 0.f);
                for (int j = 0; j < n; j++) {
                    const float* v = &qkv[static_cast<size_t>(j) * d3 + 2 * d + h * dh];
                    const float w = s[j];
                    for (int i = 0; i < dh; i++) a[i] += w * v[i];
                }
                const float inv = 1.f / sum;
                for (int i = 0; i < dh; i++) a[i] *= inv;
            }
            L.out(att.data(), o.data());
            float* yr = &y[static_cast<size_t>(r) * d];
            for (int i = 0; i < d; i++) yr[i] = x[static_cast<size_t>(r) * d + i] + o[i];
            layer_norm(L.n1, yr);
            L.l1(yr, f.data());
            for (float& v : f) v = std::max(0.f, v);
            L.l2(f.data(), g.data());
            for (int i = 0; i < d; i++) yr[i] += g[i];
            layer_norm(L.n2, yr);
        }
        x.swap(y);
        n = rows;
    }
}

CtxModel::CtxModel(const std::string& dir) {
    const json::Value models = json::parse(read_text(dir + "/models.json"));
    const std::string kind = models.at("kind").string();
    if (kind != "ctx_gru" && kind != "ctx_attn") throw std::runtime_error("ctx: models.json kind " + kind + " is not ctx_gru / ctx_attn");
    if (models.at("feature_order_version").integer() != proto::kFeatureVersion)
        throw std::runtime_error("ctx: feature_order_version " + std::to_string(models.at("feature_order_version").integer()) +
                                 " != controller " + std::to_string(proto::kFeatureVersion));
    const json::Value m = json::parse(read_text(dir + "/manifest.json"));
    nf_ = m.at("n_features").integer();
    de_ = m.at("d_summary").integer();
    d2_ = m.at("d2").integer();
    k_ = m.at("k").integer();
    s1_ = m.has("stage1") ? m.at("stage1").at("type").string() : "mlp";  // 처음 내보낸 형식은 mlp + gru뿐
    s2_ = m.has("stage2") ? m.at("stage2").at("type").string() : "gru";
    if ((kind == "ctx_gru") != (s2_ == "gru")) throw std::runtime_error("ctx: kind " + kind + " but stage2 " + s2_);
    if (m.at("n_heads").integer() != kHeads) throw std::runtime_error("ctx: n_heads != 3");
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
    auto need = [](bool ok, const char* what) {
        if (!ok) throw std::runtime_error(std::string("ctx: shape mismatch ") + what);
    };

    head1_ = lin("stage1.head");
    need(head1_.in == de_ && head1_.out == kHeads, "stage1 head");
    if (s1_ == "mlp") {
        b0_ = lin("stage1.body.0");
        b3_ = lin("stage1.body.3");
        b5_ = lin("stage1.body.5");
        need(b0_.in == 2 * nf_ && b3_.in == b0_.out && b5_.in == b3_.out && b5_.out == de_, "stage1 body");
    } else if (s1_ == "ft") {
        ft_w_ = tensor("stage1.w", {nf_, de_});
        ft_b_ = tensor("stage1.b", {nf_, de_});
        ft_miss_ = tensor("stage1.miss", {nf_, de_});
        ft_cls_ = tensor("stage1.cls", {1, 1, de_});
        ft_enc_ = enc("stage1.enc", de_, m.at("stage1").at("heads").integer());
    } else {
        throw std::runtime_error("ctx: unknown stage1 " + s1_);
    }

    pq_ = lin("stage2.pq");
    pc_ = lin("stage2.pc");
    need(pq_.in == de_ && pq_.out == d2_ && pc_.in == de_ + kHeads + 1 && pc_.out == d2_, "stage2 projections");
    if (s2_ == "gru") {
        if (m.has("gru_gate_order") && m.at("gru_gate_order").string() != "r,z,n") throw std::runtime_error("ctx: gru gate order");
        h0_ = lin("stage2.head.0");
        h3_ = lin("stage2.head.3");
        Wih_ = tensor("stage2.gru.weight_ih_l0", {3 * d2_, d2_});
        Whh_ = tensor("stage2.gru.weight_hh_l0", {3 * d2_, d2_});
        bih_ = tensor("stage2.gru.bias_ih_l0", {3 * d2_});
        bhh_ = tensor("stage2.gru.bias_hh_l0", {3 * d2_});
        need(h0_.in == 2 * d2_ && h3_.in == h0_.out && h3_.out == kHeads, "stage2 head");
    } else if (s2_ == "attn") {
        type_ = tensor("stage2.tok_type", {2, d2_});
        at_enc_ = enc("stage2.enc", d2_, m.at("stage2").at("heads").integer());
        at_head_ = lin("stage2.head");
        need(at_head_.in == d2_ && at_head_.out == kHeads, "stage2 head");
    } else {
        throw std::runtime_error("ctx: unknown stage2 " + s2_);
    }
}

void CtxModel::summarize_inputs(const double* x, float* e) const {
    std::vector<float> z(nf_), msk(nf_);
    for (int i = 0; i < nf_; i++) {
        const bool ok = std::isfinite(x[i]);
        double v = ok ? (x[i] - mu_[i]) / sd_[i] : 0.0;  // common6.standardize: 64비트로 빼고 나눈 뒤 float32
        if (!std::isfinite(v)) v = 0.0;
        v = std::min(5.0, std::max(-5.0, v));
        z[i] = static_cast<float>(v);
        msk[i] = ok ? 1.f : 0.f;
    }
    if (s1_ == "mlp") {
        std::vector<float> in(2 * nf_), a(b0_.out), b(b3_.out);
        std::copy(z.begin(), z.end(), in.begin());
        std::copy(msk.begin(), msk.end(), in.begin() + nf_);
        b0_(in.data(), a.data());
        for (float& v : a) v = gelu(v);
        b3_(a.data(), b.data());
        for (float& v : b) v = gelu(v);
        b5_(b.data(), e);
        return;
    }
    // ft: [cls, 피처 토큰 nf개]
    const int d = de_;
    std::vector<float> t(static_cast<size_t>(nf_ + 1) * d);
    std::copy(ft_cls_, ft_cls_ + d, t.begin());
    for (int i = 0; i < nf_; i++) {
        float* r = &t[static_cast<size_t>(i + 1) * d];
        for (int j = 0; j < d; j++)
            r[j] = msk[i] > 0.f ? z[i] * ft_w_[i * d + j] + ft_b_[i * d + j] : ft_miss_[i * d + j];
    }
    encode(ft_enc_, t, nf_ + 1, true);
    std::copy(t.begin(), t.begin() + d, e);
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

void CtxModel::prepare(const float* tokens, int n, Ctx& c) const {
    const int H = d2_;
    c.n = n;
    if (s2_ == "attn") {
        // 토큰 투영과, 첫 층의 Q·K·V (토큰 자신의 입력에만 달려 있어 탑승 동안 같다)
        c.v.assign(static_cast<size_t>(n) * H, 0.f);
        c.qkv1.assign(static_cast<size_t>(n) * 3 * H, 0.f);
        const EncLayer& L0 = at_enc_.front();
        for (int t = 0; t < n; t++) {
            float* r = &c.v[static_cast<size_t>(t) * H];
            pc_(tokens + static_cast<size_t>(t) * token_dim(), r);
            for (int i = 0; i < H; i++) r[i] += type_[H + i];
            L0.qkv(r, &c.qkv1[static_cast<size_t>(t) * 3 * H]);
        }
        return;
    }
    c.v.assign(static_cast<size_t>(H), 0.f);
    c.qkv1.clear();
    float* h = c.v.data();
    std::vector<float> x(H), gi(3 * H), gh(3 * H);
    for (int t = 0; t < n; t++) {
        pc_(tokens + static_cast<size_t>(t) * token_dim(), x.data());
        for (int o = 0; o < 3 * H; ++o) {
            gi[o] = bih_[o] + dot(Wih_ + static_cast<size_t>(o) * H, x.data(), H);
            gh[o] = bhh_[o] + dot(Whh_ + static_cast<size_t>(o) * H, h, H);
        }
        for (int i = 0; i < H; ++i) {
            const float r = sigm(gi[i] + gh[i]);
            const float z = sigm(gi[H + i] + gh[H + i]);
            const float nn = std::tanh(gi[2 * H + i] + r * gh[2 * H + i]);
            h[i] = (1.f - z) * nn + z * h[i];
        }
    }
}

void CtxModel::predict(const float* e, const Ctx& c, float* p) const {
    const int H = d2_;
    if (s2_ == "attn") {
        if (c.v.size() != static_cast<size_t>(c.n) * H) throw std::runtime_error("ctx: context not prepared");
        std::vector<float> x(static_cast<size_t>(c.n + 1) * H);
        pq_(e, x.data());
        for (int i = 0; i < H; i++) x[i] += type_[i];
        std::copy(c.v.begin(), c.v.end(), x.begin() + H);
        encode(at_enc_, x, c.n + 1, true, c.qkv1.empty() ? nullptr : c.qkv1.data());
        at_head_(x.data(), p);
    } else {
        if (c.v.size() != static_cast<size_t>(H)) throw std::runtime_error("ctx: context not prepared");
        std::vector<float> cat(2 * H), a(h0_.out);
        pq_(e, cat.data());
        std::copy(c.v.begin(), c.v.end(), cat.begin() + H);
        h0_(cat.data(), a.data());
        for (float& v : a) v = gelu(v);
        h3_(a.data(), p);
    }
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
