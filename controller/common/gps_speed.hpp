// P2가 쓰는 GPS 속도 보관 (#18). P6이 M09 GPS를 받을 때마다 GPS_SPEED로 보내 주고, P2는 샘플마다 speed()를 묻는다.
// 규칙 (docs/controller/RF_MESSAGES.md §4):
//   - 폰마다 가장 최근 값 하나를 기억한다 (폰은 9대까지)
//   - 최근 kFreshNs(2초) 안의 값 중 수평 정확도 acc가 가장 작은(가장 정확한) 폰의 속도
//   - 2초 안의 값이 없으면 kStaleNs(3초) 안의 가장 최근 값, 그것도 없으면 NaN (속도 모름)
// 시각은 단조 시계 ns (P6이 줄을 받은 때). 표준 C++만 (Mac에서 시험). 잠그지 않는다: P2는 한 스레드다
#pragma once
#include <cmath>
#include <cstdint>
#include <cstring>
#include <limits>

namespace mm::gps {

constexpr uint64_t kFreshNs = 2000000000ULL;  // 여러 폰 중 고를 때 보는 범위
constexpr uint64_t kStaleNs = 3000000000ULL;  // 이보다 오래되면 속도 없음
constexpr int kMaxPhones = 9;
constexpr int kIdLen = 64;

class SpeedHold {
public:
    // 받은 값. spd·acc가 유한한 숫자가 아니면 버린다 (P6이 이미 거른 값이지만 한 번 더)
    void put(const char* phone, uint64_t mono_ns, double spd, double acc) {
        if (!std::isfinite(spd) || !std::isfinite(acc) || spd < 0 || acc < 0) return;
        Slot* s = find(phone);
        if (!s) s = oldest();
        std::strncpy(s->phone, phone, kIdLen - 1);
        s->phone[kIdLen - 1] = 0;
        s->used = true;
        s->mono_ns = mono_ns;
        s->spd = spd;
        s->acc = acc;
    }

    // 지금(now_ns) 쓸 속도 m/s, 없으면 NaN
    double speed(uint64_t now_ns) const {
        const Slot* s = pick(now_ns);
        return s ? s->spd : std::numeric_limits<double>::quiet_NaN();
    }
    // speed()가 고른 값의 수평 정확도 m, 없으면 NaN (횡단 경사 제거가 정확도 나쁜 초를 거른다)
    double accuracy(uint64_t now_ns) const {
        const Slot* s = pick(now_ns);
        return s ? s->acc : std::numeric_limits<double>::quiet_NaN();
    }

    void clear() {
        for (Slot& s : slot_) s = Slot{};
    }

private:
    struct Slot {
        bool used = false;
        char phone[kIdLen] = {};
        uint64_t mono_ns = 0;
        double spd = 0, acc = 0;
    };
    const Slot* pick(uint64_t now_ns) const {
        const Slot* best = nullptr;  // 2초 안, acc 최소
        const Slot* newest = nullptr;
        for (const Slot& s : slot_) {
            if (!s.used) continue;
            const uint64_t age = now_ns >= s.mono_ns ? now_ns - s.mono_ns : 0;  // 미래 시각(시계 차이)은 방금 받은 것으로
            if (age > kStaleNs) continue;
            if (!newest || s.mono_ns > newest->mono_ns) newest = &s;
            if (age <= kFreshNs && (!best || s.acc < best->acc)) best = &s;
        }
        return best ? best : newest;
    }
    Slot* find(const char* phone) {
        for (Slot& s : slot_)
            if (s.used && std::strncmp(s.phone, phone, kIdLen) == 0) return &s;
        return nullptr;
    }
    Slot* oldest() {  // 빈 칸, 없으면 가장 오래된 칸 (나간 폰의 칸을 새 폰이 쓴다)
        Slot* o = &slot_[0];
        for (Slot& s : slot_) {
            if (!s.used) return &s;
            if (s.mono_ns < o->mono_ns) o = &s;
        }
        return o;
    }
    Slot slot_[kMaxPhones];
};

// 샘플의 speed 칸 채우기: 재생 trip이 준 값(NaN 아님)은 그대로, LIVE(NaN)면 보관한 GPS 속도
inline double fill_speed(double row_speed, const SpeedHold& h, uint64_t now_ns) {
    return std::isnan(row_speed) ? h.speed(now_ns) : row_speed;
}

}  // namespace mm::gps
