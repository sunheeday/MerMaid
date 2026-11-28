// V3 문맥 모델 부품 테스트: 주행 끝 토큰 고르기(ctx_label), 승객별 토큰 저장(ctx_history), 토큰 고르기 규칙(select_tokens)
#include <unistd.h>

#include <cstdio>
#include <cstdlib>
#include <string>
#include <vector>

#include "ctx_history.hpp"
#include "ctx_label.hpp"
#include "ctx_model.hpp"

using namespace mm;

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

static std::vector<ctx::Pick> only(const std::vector<ctx::Pick>& v, const std::string& p, int dev) {
    std::vector<ctx::Pick> o;
    for (const auto& x : v)
        if (x.passenger == p && x.device == dev) o.push_back(x);
    return o;
}

// 10초 주기 n개, 장치 3개 모두 자격 있음 (켜진 동안은 호출 쪽이 false로 넘긴다)
static void cycles(ctx::Labeler& L, const std::string& p, int n, double on_from = 1e9, double on_to = -1, int on_dev = -1) {
    for (int i = 0; i < n; i++) {
        const double t = 10.0 * i;
        bool ok[3] = {true, true, true};
        if (on_dev >= 0 && t >= on_from && t < on_to) ok[on_dev] = false;
        L.record_cycle(static_cast<uint64_t>(i), t, p, ok);
    }
}

int main() {
    // ---- 1. 직접 켜면 켜기 직전 주기가 양성, 음성은 앞으로 5분 안에 켜기 없는 주기에서 2개 ----
    {
        ctx::Labeler L;
        L.ride_start(1);
        cycles(L, "a", 180, 905, 1e9, ctx::kFan);  // 30분, 905 s에 fan을 켬
        L.on_user(905, "a", ctx::kFan, true);
        const auto P = L.picks(1800);
        const auto fan = only(P, "a", ctx::kFan);
        int pos = 0, neg = 0;
        for (const auto& x : fan) {
            if (x.label == 1) {
                pos++;
                CHECK(x.seq == 90, "positive anchor is the last eligible cycle before the press (t=900): seq %llu",
                      (unsigned long long)x.seq);
            } else {
                neg++;
                const double t = 10.0 * x.seq;
                CHECK(!(t >= 905 - 300 && t < 905), "negative inside the 5 minutes before the press: t=%.0f", t);
                CHECK(t < 1800 - 300, "negative in the last 5 minutes of the ride: t=%.0f", t);
                CHECK(t < 905, "negative while the fan is on: t=%.0f", t);
            }
        }
        CHECK(pos == 1 && neg == 2, "fan tokens: %d positive, %d negative", pos, neg);
        CHECK(only(P, "a", ctx::kAmbient).size() == 2 && only(P, "a", ctx::kDots).size() == 2, "untouched devices get 2 negatives");
    }
    // ---- 2. 공용 장치를 남이 켜면 나머지 승객은 그 전 5분이 음성에서 빠지고, 양성도 없다 ----
    {
        ctx::Labeler L;
        L.ride_start(2);
        cycles(L, "a", 60, 505, 1e9, ctx::kFan);
        cycles(L, "b", 60, 505, 1e9, ctx::kFan);
        L.on_user(505, "a", ctx::kFan, true);
        const auto P = L.picks(600);  // 10분: 끝 5분을 빼면 0~295 s만 음성 후보
        const auto fb = only(P, "b", ctx::kFan);
        for (const auto& x : fb) {
            CHECK(x.label == 0, "b did not press, no positive for b");
            CHECK(10.0 * x.seq < 205, "b negative inside a's 5-minute window: t=%.0f", 10.0 * x.seq);
        }
        int apos = 0;
        for (const auto& x : only(P, "a", ctx::kFan)) apos += x.label;
        CHECK(apos == 1, "a gets the positive: %d", apos);
    }
    // ---- 3. 자동으로 켜 줬는데 30초 안에 끄면 그 직전 주기가 음성(먼저), 그대로 둔 내 폰 dots는 양성 ----
    {
        ctx::Labeler L;
        L.ride_start(3);
        cycles(L, "a", 120);
        L.on_auto(400, ctx::kFan, "", true);
        L.on_user(415, "a", ctx::kFan, false);  // 15초 만에 끔
        L.on_auto(700, ctx::kDots, "a", true);  // 그대로 둠
        const auto P = L.picks(1200);
        const auto fan = only(P, "a", ctx::kFan);
        bool strong = false;
        for (const auto& x : fan) {
            CHECK(x.label == 0, "quick-off after auto-on never gives a positive");
            if (x.seq == 40) strong = true;  // t=400: 자동 켜기를 낸 주기
        }
        CHECK(strong, "strong negative at the cycle before the auto-on");
        const auto dots = only(P, "a", ctx::kDots);
        int dpos = 0;
        for (const auto& x : dots)
            if (x.label == 1) {
                dpos++;
                CHECK(x.seq == 70, "dots positive at the cycle of the auto-on at 700: seq %llu", (unsigned long long)x.seq);
            }
        CHECK(dpos == 1, "accepted auto dots is a positive: %d", dpos);
    }
    // ---- 3b. 자동 켜기를 낸 주기(같은 시각)는 깨끗한 음성이 될 수 없다 ----
    {
        ctx::Labeler L;
        L.ride_start(5);
        cycles(L, "a", 72, 125, 1e9, ctx::kDots);  // 12분, 120 s 주기(결정 전이라 자격 있음)의 결정으로 dots를 켜고 그대로
        L.on_auto(120, ctx::kDots, "a", true);
        const auto dots = only(L.picks(720), "a", ctx::kDots);
        CHECK(dots.size() == 1 && dots[0].label == 1 && dots[0].seq == 12, "only the positive at seq 12: %zu tokens",
              dots.size());
    }
    // ---- 4. 공용 장치 자동 켜짐을 그대로 두면 누구 몫인지 몰라 양성 없음, 그 전 5분은 음성에서 빠짐 ----
    {
        ctx::Labeler L;
        L.ride_start(4);
        cycles(L, "a", 120, 600, 1e9, ctx::kAmbient);
        L.on_auto(600, ctx::kAmbient, "", true);
        const auto P = L.picks(1200);
        for (const auto& x : only(P, "a", ctx::kAmbient)) {
            CHECK(x.label == 0, "kept shared auto-on is not a positive");
            CHECK(10.0 * x.seq < 300, "negative inside the 5 minutes before the auto-on: t=%.0f", 10.0 * x.seq);
        }
    }

    // ---- 5. select_tokens: 장치·라벨마다 최근 3개는 남기고, 나머지는 최근 것 ----
    {
        std::vector<TokenMeta> c;
        c.push_back({2, 1});                                     // 오래된 dots 양성 (드묾)
        for (int i = 0; i < 20; i++) c.push_back({0, 0});        // fan 음성 20
        const auto s = select_tokens(c, 8);
        CHECK(s.size() == 8 && s[0] == 0, "rare old token kept by the quota, size %zu first %d", s.size(), s.empty() ? -1 : s[0]);
        CHECK(s.back() == 20, "newest kept");
        const auto all = select_tokens(c, 64);
        CHECK(all.size() == c.size(), "k >= n keeps everything");
    }

    // ---- 6. History: 저장 → 읽기, 주행 번호, 한도, 다른 요약 차원은 무시 ----
    {
        char tmpl[] = "/tmp/mm_ctx_XXXXXX";
        const char* base = mkdtemp(tmpl);
        CHECK(base, "mkdtemp");
        const std::string dir = std::string(base) + "/ctx";
        ctx::History H(dir, 4, 6);
        CHECK(H.load("phone-a").empty(), "no file reads as empty");
        std::vector<ctx::Token> r1;
        for (int i = 0; i < 4; i++) r1.push_back({0, i % 3, i == 0, {1.f * i, 2.f, 3.f, 4.f}});
        H.append("phone-a", r1);
        int32_t rides = -1;
        auto got = H.load("phone-a", &rides);
        CHECK(got.size() == 4 && rides == 1 && got[0].ride == 0 && got[3].e[0] == 3.f, "round trip: n %zu rides %d", got.size(), rides);
        H.append("phone-a", r1);
        got = H.load("phone-a", &rides);
        CHECK(got.size() == 6 && rides == 2 && got.back().ride == 1, "keep limit 6 and second ride number: n %zu", got.size());
        int n = 0;
        const auto C = H.context(got, 3, &n);
        CHECK(n == 3 && C.size() == 3u * 8, "context rows %d", n);
        for (int r = 0; r < n; r++) {
            const float* row = &C[static_cast<size_t>(r) * 8];
            CHECK(row[4] + row[5] + row[6] == 1.f, "one-hot row %d", r);
            CHECK(row[7] == 0.f || row[7] == 1.f, "label column row %d", r);
        }
        ctx::History other(dir, 5);
        CHECK(other.load("phone-a").empty(), "different summary size is ignored");
        bool threw = false;
        try {
            H.load("../x");
        } catch (const std::exception&) {
            threw = true;
        }
        CHECK(threw, "unsafe passenger id rejected");
        std::string cmd = "rm -rf " + std::string(base);
        std::system(cmd.c_str());
    }

    if (failures) {
        std::printf("%d failure(s)\n", failures);
        return 1;
    }
    std::printf("ctx: all tests passed\n");
    return 0;
}
