// P7 로거 코어: 링의 샘플과 P3·P4·P6의 LOG_* 메시지를 주행마다 SQLite 파일 하나에 쓴다.
// 설계: docs/DATA_LOGGING.md §6. 프로세스(스레드, QNX 메시지)는 qnx/p7_logger.cpp, 이 파일은 시험할 수 있는 판단과 쓰기.
//
//   <dir>/session_count              시작할 때마다 +1
//   <dir>/rides/<id>.open.sqlite     기록 중 → 닫으면 <id>.sqlite (파일 하나로 완결)
//   <id> = <device>-<session:05d>-<ride:03d>  (Pi에 RTC가 없어 순서는 세션과 주행 번호로)
//
// Logger는 쓰기 스레드 하나만 만진다 (잠금 없음). 받기 스레드와는 MsgQueue로만 만난다.
#pragma once
#include <cstdint>
#include <functional>
#include <map>
#include <memory>
#include <mutex>
#include <string>
#include <vector>

#include "log_messages.hpp"
#include "sample_ring.hpp"

namespace mm::rlog {

constexpr int kSchemaVersion = 1;
constexpr int kChunkSamples = 100;                       // samples 표 한 행 = 1초
constexpr uint64_t kCommitNs = 1000000000ULL;            // 트랜잭션 1초에 한 번
constexpr uint64_t kCloseGraceNs = 2000000000ULL;        // RIDE_END 뒤 2초는 늦게 온 LOG_*를 같은 주행에
constexpr uint64_t kStorageCheckNs = 60000000000ULL;     // 남은 공간 확인 60초마다
constexpr uint64_t kRateHealthNs = 60000000000ULL;       // sample_rate_hz 1분마다

// 받기 스레드 → 쓰기 스레드. 칸 수와 칸 크기가 고정이라 메모리가 늘지 않는다. 차면 가장 오래된 것을 버린다
class MsgQueue {
public:
    explicit MsgQueue(size_t cap = 512, size_t max_bytes = msg::kMaxLogBytes);
    bool push(const void* p, size_t n);   // n이 칸보다 크면 false (버림)
    bool pop(std::vector<char>& out);     // 없으면 false
    uint64_t dropped() const;
    size_t size() const;

private:
    mutable std::mutex mu_;
    size_t cap_, max_;
    std::vector<char> buf_;
    std::vector<size_t> len_;
    size_t head_ = 0, count_ = 0;
    uint64_t dropped_ = 0;
};

struct Config {
    std::string dir;                                 // 로그 폴더 (rides/는 아래에 만든다)
    std::string device_id = "pi01";
    double min_free_mb = 1024;                       // 아래면 올린(.uploaded) 주행부터 지우고, 그래도면 samples 멈춤
    double refuse_mb = 200;                          // 아래면 새 주행 파일을 만들지 않는다
    std::map<std::string, std::string> meta;         // 모든 주행에 적을 고정 meta (imu_mount, policy_json, git_commit …)
    std::function<double(const std::string&)> free_mb;  // 남은 공간 (MB). 비우면 statvfs
    std::function<void(const std::string& path, const std::string& reason)> on_closed;  // 주행 파일을 닫은 뒤
    std::function<void(const std::string&)> warn;    // 경고 한 줄 (비우면 stderr)
};

struct Stats {
    uint64_t rides_opened = 0, rides_closed = 0, rides_refused = 0, recovered = 0, broken = 0;
    uint64_t samples = 0, hold_samples = 0, ring_lost = 0;
    uint64_t infer_rows = 0, decision_rows = 0, ctx_rows = 0, label_rows = 0, health_rows = 0;
    uint64_t outside_ride = 0, bad_msgs = 0, sqlite_errors = 0, deleted_uploaded = 0;
    bool samples_paused = false;
};

class RideFile;

class Logger {
public:
    explicit Logger(Config c);
    ~Logger();
    Logger(const Logger&) = delete;
    Logger& operator=(const Logger&) = delete;

    // session_count +1, 남은 *.open.sqlite 복구. 실패하면 예외 (폴더를 못 만듦)
    void start();
    // 링에서 읽은 칸 (순서대로). col_mask: 링 헤더의 값 있는 칸
    void on_item(const ring::Item& it, uint32_t col_mask, uint64_t now);
    void on_ring_lost(uint64_t n);          // 링이 넘쳐 잃은 칸 수 (Reader 통계의 늘어난 만큼)
    void on_p1_restart(uint64_t now);       // 링 epoch가 바뀜: 열린 주행을 닫는다
    void on_msg(const void* p, size_t n, uint64_t now, uint64_t queue_drop_total = 0);
    void tick(uint64_t now);                // 쓰기 스레드가 200 ms마다: 커밋, 늦은 닫기, 공간 확인
    void shutdown(uint64_t now);            // 열린 주행을 'shutdown'으로 닫는다

    const Stats& stats() const { return st_; }
    uint64_t session() const { return session_; }
    bool ride_open() const { return static_cast<bool>(file_); }
    std::string rides_dir() const { return cfg_.dir + "/rides"; }

private:
    struct Ctx {
        uint64_t mono_ns;
        std::string sender, kind, value;
    };
    Config cfg_;
    Stats st_;
    uint64_t session_ = 0, ride_no_ = 0;
    std::unique_ptr<RideFile> file_;
    std::string open_path_, final_path_;
    bool closing_ = false, refused_ = false, in_txn_ = false, model_written_ = false;
    uint64_t close_at_ = 0, last_commit_ = 0, next_storage_ = 0, next_rate_ = 0;
    uint64_t ride_mono0_ = 0, queue_drop_seen_ = 0, ring_lost_ride_ = 0, hold_ride_ = 0;
    uint32_t col_mask_ = 0;
    std::vector<int> cols_;                 // samples.data에 넣을 trip 칸
    // 지금 모으는 1초 묶음
    uint64_t chunk_seq0_ = 0, chunk_mono0_ = 0, next_seq_ = 0;
    std::vector<float> chunk_;
    std::vector<uint8_t> chunk_hold_;
    bool chunk_has_hold_ = false;
    int chunk_n_ = 0;
    // mono_ns → 주행 시각: 묶음 첫 샘플의 (mono_ns, seq)
    std::vector<std::pair<uint64_t, uint64_t>> anchors_;
    uint64_t rate_mono_ = 0, rate_seq_ = 0, last_mono_ = 0, last_seq_ = 0;
    std::map<std::pair<std::string, std::string>, Ctx> snapshot_;  // 주행 밖에서 받은 마지막 SCR·PROF

    void warn(const std::string& s);
    double free_mb();
    void open_ride(const ring::Item& it, uint32_t col_mask, uint64_t now);
    void close_ride(const std::string& reason, uint64_t now);
    void flush_chunk();
    void ensure_txn();
    void commit();
    bool writable() const { return file_ && !refused_; }
    double t_of(uint64_t mono_ns, bool& ok) const;
    void health(uint64_t mono_ns, const std::string& key, double value);
    void check_storage(uint64_t now);
    void recover();
};

// 남은 공간 (MB), statvfs
double statvfs_free_mb(const std::string& dir);

}  // namespace mm::rlog
