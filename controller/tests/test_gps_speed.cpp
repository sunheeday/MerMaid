// P2 GPS 속도 보관 (#18, common/gps_speed.hpp): 폰마다 최근 값, 2초 안 acc 최소, 3초 넘으면 NaN, 재생 trip 값은 그대로
#include <cmath>
#include <cstdio>
#include <string>

#include "gps_speed.hpp"

using namespace mm::gps;

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

constexpr uint64_t S = 1000000000ULL;
constexpr uint64_t MS = 1000000ULL;

int main() {
    SpeedHold h;
    CHECK(std::isnan(h.speed(10 * S)), "nothing yet -> NaN");

    h.put("phone-a", 10 * S, 12.0, 5.0);
    CHECK(h.speed(10 * S) == 12.0, "one phone");
    CHECK(h.speed(13 * S) == 12.0, "still used at exactly 3 s");
    CHECK(std::isnan(h.speed(13 * S + 1)), "older than 3 s -> NaN");

    // 두 폰: 2초 안에서 acc가 작은(정확한) 쪽
    h.put("phone-a", 20 * S, 12.0, 5.0);
    h.put("phone-b", 20 * S + 500 * MS, 11.0, 3.0);
    CHECK(h.speed(21 * S) == 11.0, "most accurate of the last 2 s (b)");
    h.put("phone-a", 21 * S, 12.5, 2.0);
    CHECK(h.speed(21 * S) == 12.5, "a is now more accurate");
    // a가 끊김: 2초가 지나면 a는 후보에서 빠지고 b의 새 값
    h.put("phone-b", 23 * S + 200 * MS, 13.0, 4.0);
    CHECK(h.speed(23 * S + 300 * MS) == 13.0, "a older than 2 s, b fresh");
    // 둘 다 2초 넘음 → 3초 안의 가장 최근 값
    CHECK(h.speed(25 * S + 600 * MS) == 13.0, "none in 2 s: newest within 3 s");
    CHECK(std::isnan(h.speed(26 * S + 300 * MS)), "all older than 3 s -> NaN");
    // 정차: 0 m/s는 유효한 값
    h.put("phone-a", 30 * S, 0.0, 3.0);
    CHECK(h.speed(30 * S) == 0.0, "0 m/s is a speed");
    // 이상한 값은 버린다
    h.put("phone-c", 30 * S, std::nan(""), 3.0);
    h.put("phone-c", 30 * S, -1.0, 3.0);
    h.put("phone-c", 30 * S, 5.0, -1.0);
    CHECK(h.speed(30 * S) == 0.0, "bad values ignored");

    // 폰 9대 넘게: 가장 오래된 칸을 새 폰이 쓴다
    SpeedHold k;
    for (int i = 0; i < 12; i++) k.put(("p" + std::to_string(i)).c_str(), (40 + i) * S / 4, 1.0 + i, 10.0 - i * 0.5);
    CHECK(k.speed(40 * S / 4 + 11 * S / 4) == 12.0, "newest phones kept, most accurate is p11");

    // 샘플 칸 채우기
    CHECK(fill_speed(8.0, h, 30 * S) == 8.0, "replay trip speed kept");
    CHECK(fill_speed(std::nan(""), h, 30 * S) == 0.0, "LIVE NaN filled from GPS");
    SpeedHold empty;
    CHECK(std::isnan(fill_speed(std::nan(""), empty, 30 * S)), "no GPS -> stays NaN");

    // 미래 시각(시계 차이)은 방금 받은 것으로
    SpeedHold f;
    f.put("phone-a", 50 * S, 7.0, 1.0);
    CHECK(f.speed(49 * S) == 7.0, "future timestamp counts as fresh");

    if (failures) {
        std::printf("%d failure(s)\n", failures);
        return 1;
    }
    std::printf("gps_speed: all passed\n");
    return 0;
}
