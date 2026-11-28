#include "ctx_history.hpp"

#include <cstring>
#include <stdexcept>

#include "store.hpp"

namespace mm::ctx {

namespace {
constexpr char kMagic[8] = {'M', 'M', 'C', 'T', 'X', '1', 0, 0};

template <typename T>
void put(std::string& s, T v) {
    s.append(reinterpret_cast<const char*>(&v), sizeof v);
}
template <typename T>
bool get(const std::string& s, size_t& off, T& v) {
    if (off + sizeof v > s.size()) return false;
    std::memcpy(&v, s.data() + off, sizeof v);
    off += sizeof v;
    return true;
}
}  // namespace

History::History(std::string dir, int summary_dim, int keep) : dir_(std::move(dir)), de_(summary_dim), keep_(keep) {
    store::make_dirs(dir_);
}

std::string History::path(const std::string& passenger) const {
    if (!store::safe_name(passenger)) throw std::runtime_error("ctx: unsafe passenger id " + passenger);
    return dir_ + "/" + passenger + ".ctx";
}

std::vector<Token> History::load(const std::string& passenger, int32_t* rides) const {
    std::vector<Token> out;
    if (rides) *rides = 0;
    std::string s;
    if (!store::read_file(path(passenger), s)) return out;
    if (s.size() < 8 || std::memcmp(s.data(), kMagic, 8) != 0) return out;
    size_t off = 8;
    int32_t de = 0, r = 0, n = 0;
    if (!get(s, off, de) || !get(s, off, r) || !get(s, off, n) || de != de_ || n < 0) return out;
    for (int32_t i = 0; i < n; i++) {
        Token t;
        uint8_t dev = 0, lab = 0;
        uint16_t pad = 0;
        if (!get(s, off, t.ride) || !get(s, off, dev) || !get(s, off, lab) || !get(s, off, pad)) return {};
        t.device = dev;
        t.label = lab;
        t.e.resize(static_cast<size_t>(de_));
        if (off + sizeof(float) * de_ > s.size()) return {};
        std::memcpy(t.e.data(), s.data() + off, sizeof(float) * de_);
        off += sizeof(float) * de_;
        out.push_back(std::move(t));
    }
    if (rides) *rides = r;
    return out;
}

void History::append(const std::string& passenger, const std::vector<Token>& ride_tokens) const {
    if (ride_tokens.empty()) return;
    int32_t rides = 0;
    std::vector<Token> all = load(passenger, &rides);
    for (Token t : ride_tokens) {
        t.ride = rides;
        if (static_cast<int>(t.e.size()) != de_) throw std::runtime_error("ctx: token summary size");
        all.push_back(std::move(t));
    }
    rides++;
    // 한도: select_tokens(keep)가 고를 토큰만 남긴다 (장치·라벨마다 최근 kQuota개 + 나머지 최근 것)
    std::vector<TokenMeta> meta;
    for (const Token& t : all) meta.push_back({t.device, t.label});
    std::vector<Token> kept;
    for (int i : select_tokens(meta, keep_)) kept.push_back(std::move(all[static_cast<size_t>(i)]));

    std::string s(kMagic, 8);
    put<int32_t>(s, de_);
    put<int32_t>(s, rides);
    put<int32_t>(s, static_cast<int32_t>(kept.size()));
    for (const Token& t : kept) {
        put<int32_t>(s, t.ride);
        put<uint8_t>(s, static_cast<uint8_t>(t.device));
        put<uint8_t>(s, static_cast<uint8_t>(t.label));
        put<uint16_t>(s, 0);
        s.append(reinterpret_cast<const char*>(t.e.data()), sizeof(float) * de_);
    }
    store::write_atomic(path(passenger), s);
}

std::vector<float> History::context(const std::vector<Token>& all, int k, int* n_out) const {
    std::vector<TokenMeta> meta;
    for (const Token& t : all) meta.push_back({t.device, t.label});
    const std::vector<int> pick = select_tokens(meta, k);
    const int td = de_ + CtxModel::kHeads + 1;
    std::vector<float> c(pick.size() * static_cast<size_t>(td), 0.f);
    for (size_t r = 0; r < pick.size(); r++) {
        const Token& t = all[static_cast<size_t>(pick[r])];
        float* row = &c[r * static_cast<size_t>(td)];
        std::memcpy(row, t.e.data(), sizeof(float) * de_);
        row[de_ + t.device] = 1.f;
        row[td - 1] = static_cast<float>(t.label);
    }
    *n_out = static_cast<int>(pick.size());
    return c;
}

}  // namespace mm::ctx
