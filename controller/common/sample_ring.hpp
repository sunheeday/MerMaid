// P1 → P7 공유 메모리 링 (/mermaid_samples). 생산자 1 · 소비자 1, 락 없음. POSIX shm_open + mmap (QNX, Mac, Linux).
// 설계: docs/DATA_LOGGING.md §5
// - 만드는 쪽은 P1(Writer)이다. P1은 P7이 있든 없든 똑같이 돈다: 쓰기는 복사 + 원자 변수 저장뿐, 기다리지 않는다.
// - P7(Reader)은 늦으면 덮어쓰인 칸을 잃고, 잃은 개수를 센다. P1이 다시 시작하면 epoch가 바뀌어 처음부터 읽는다.
// - 칸마다 seqlock: 쓰는 동안 n = kWriting, 다 쓰면 n = 칸 번호. 읽은 뒤 n이 그대로인지 다시 확인한다.
#pragma once
#include <atomic>
#include <limits>
#include <cstdint>
#include <string>
#include <vector>

#include "features.hpp"

namespace mm::ring {

constexpr const char* kDefaultName = "/mermaid_samples";
constexpr uint32_t kMagic = 0x52534D4D;  // "MMSR"
constexpr uint32_t kVersion = 1;
constexpr uint32_t kDefaultCap = 3000;   // 30초 (100 Hz)
constexpr uint64_t kWriting = ~0ULL;

enum Kind : uint32_t { SAMPLE = 0, RIDE_START = 1, RIDE_END = 2 };

// RIDE_START 칸의 row는 샘플이 아니라 주행 정보다 (P7이 주행 파일 meta에 적는다). 모르면 NaN (재생)
enum StartInfo : int {
    S_BIAS_X = 0, S_BIAS_Y = 1, S_BIAS_Z = 2,  // 이번 주행의 자이로 바이어스 (°/s, 센서 축)
    S_BIAS_UNCAL = 3,                          // 1: 정지 판정을 못 얻고 저장값도 없어 0을 씀
    S_ACCEL_RANGE_G = 4, S_GYRO_RANGE_DPS = 5,
};
inline Row start_info_unknown() {
    Row r;
    r.fill(std::numeric_limits<double>::quiet_NaN());
    return r;
}

struct Header {
    std::atomic<uint32_t> magic;  // 다 만든 뒤에 kMagic (그 전에는 0)
    uint32_t version;
    uint32_t cap;
    uint32_t col_mask;            // 값이 있는 trip 칸 (bit i = 칸 i). LIVE: t, ax..gz = 0x7F
    std::atomic<uint64_t> epoch;  // P1이 링을 열 때마다 +1
    std::atomic<uint64_t> head;   // 지금까지 쓴 칸 수 (P1만 씀)
};

struct Slot {
    std::atomic<uint64_t> n;  // 이 칸의 일련번호 (= 쓸 때의 head). 쓰는 중이면 kWriting
    uint64_t mono_ns;         // P1이 읽은 시각 (CLOCK_MONOTONIC)
    uint32_t kind;            // Kind
    uint32_t flags;           // imu::FLAG_HOLD 등
    uint64_t seq;             // SAMPLE: 주행 안 번호. RIDE_END: 보낸 샘플 수
    Row row;                  // trip_format 순서
};

static_assert(std::atomic<uint64_t>::is_always_lock_free, "64-bit atomics must be lock-free for shared memory");
static_assert(std::atomic<uint32_t>::is_always_lock_free, "32-bit atomics must be lock-free for shared memory");

size_t region_bytes(uint32_t cap);

// 읽은 칸 (원자 변수 없는 복사본)
struct Item {
    uint64_t n = 0, mono_ns = 0, seq = 0;
    uint32_t kind = 0, flags = 0;
    Row row{};
};

class Writer {
public:
    Writer() = default;
    ~Writer();
    Writer(const Writer&) = delete;
    Writer& operator=(const Writer&) = delete;
    // 만들거나 다시 연다 (크기가 다르면 다시 만든다). 실패하면 false와 err. P1은 실패해도 계속 돈다
    bool open(const std::string& name, uint32_t cap, uint32_t col_mask, std::string& err);
    bool is_open() const { return hdr_ != nullptr; }
    void push(uint32_t kind, uint32_t flags, uint64_t seq, uint64_t mono_ns, const Row& row);
    uint64_t epoch() const;
    void close();

private:
    Header* hdr_ = nullptr;
    Slot* slots_ = nullptr;
    size_t bytes_ = 0;
};

struct ReadStats {
    uint64_t lost = 0;        // 늦어서 덮어쓰인 칸
    uint64_t torn = 0;        // 복사하는 사이 덮어쓰인 칸
    uint64_t epoch_changes = 0;
};

class Reader {
public:
    Reader() = default;
    ~Reader();
    Reader(const Reader&) = delete;
    Reader& operator=(const Reader&) = delete;
    // 링이 아직 없으면 false (P7은 1초마다 다시 시도)
    bool open(const std::string& name, std::string& err);
    bool is_open() const { return hdr_ != nullptr; }
    // 새 칸을 out 끝에 붙인다. 최대 max개
    size_t poll(std::vector<Item>& out, size_t max = ~size_t(0));
    uint32_t col_mask() const;
    // 지금까지 쓴 칸은 건너뛴다 (P7이 처음 시작할 때: 지난 실행의 칸을 다시 기록하지 않게)
    void skip_to_head();
    const ReadStats& stats() const { return st_; }
    void close();

private:
    Header* hdr_ = nullptr;
    Slot* slots_ = nullptr;
    size_t bytes_ = 0;
    uint64_t tail_ = 0, epoch_ = 0;
    ReadStats st_;
};

}  // namespace mm::ring
