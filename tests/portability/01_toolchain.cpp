// 1단계: C++17, NaN, 스레드, atomic, 100 Hz 타이머 확인
#include <atomic>
#include <cmath>
#include <cstdio>
#include <limits>
#include <optional>
#include <thread>
#include <utility>
#include <vector>
#include <time.h>

static int fails = 0;
static void check(const char* name, bool ok) {
    std::printf("[%s] %s\n", ok ? "PASS" : "FAIL", name);
    if (!ok) fails++;
}

int main() {
    std::printf("__cplusplus = %ld\n", (long)__cplusplus);
    check("C++17 이상", __cplusplus >= 201703L);

    std::optional<int> o = 3;
    auto [a, b] = std::pair<int, int>(1, 2);
    check("optional, structured binding", o.value() == 3 && a + b == 3);

    const double q = std::numeric_limits<double>::quiet_NaN();
    check("isnan", std::isnan(q));
    check("NaN <= 0 은 false", !(q <= 0.0));
    check("double은 8바이트", sizeof(double) == 8);
    check("exp", std::fabs(std::exp(1.0) - 2.718281828459045) < 1e-15);

    std::atomic<long> count{0};
    std::vector<std::thread> threads;
    for (int i = 0; i < 4; i++)
        threads.emplace_back([&count] { for (int k = 0; k < 100000; k++) count++; });
    for (auto& t : threads) t.join();
    check("스레드 4개 + atomic", count.load() == 400000);

#if defined(__APPLE__)
    std::printf("[SKIP] clock_nanosleep (macOS에는 없음)\n");
#else
    timespec next{}, now{};
    clock_gettime(CLOCK_MONOTONIC, &next);
    long long prev = 0, lo = 1LL << 60, hi = 0;
    bool sleep_ok = true;
    for (int i = 0; i < 100; i++) {
        next.tv_nsec += 10000000L;
        if (next.tv_nsec >= 1000000000L) { next.tv_nsec -= 1000000000L; next.tv_sec++; }
        if (clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &next, nullptr) != 0) sleep_ok = false;
        clock_gettime(CLOCK_MONOTONIC, &now);
        long long t = now.tv_sec * 1000000000LL + now.tv_nsec;
        if (prev) {
            long long d = (t - prev) / 1000;
            if (d < lo) lo = d;
            if (d > hi) hi = d;
        }
        prev = t;
    }
    std::printf("샘플 간격 최소 %lld us, 최대 %lld us\n", lo, hi);
    check("clock_nanosleep 100 Hz", sleep_ok && lo > 8000 && hi < 12000);
#endif

    std::printf(fails ? "RESULT: FAIL (%d)\n" : "RESULT: PASS\n", fails);
    return fails ? 1 : 0;
}
