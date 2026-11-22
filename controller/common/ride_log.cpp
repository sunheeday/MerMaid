// P7 로거 코어. 설명은 ride_log.hpp, 설계는 docs/controller/DATA_LOGGING.md §6
#include "ride_log.hpp"

#include <dirent.h>
#include <fcntl.h>
#include <sqlite3.h>
#include <sys/statvfs.h>
#include <unistd.h>

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <ctime>
#include <stdexcept>

#include "store.hpp"

static_assert(__BYTE_ORDER__ == __ORDER_LITTLE_ENDIAN__, "BLOB columns are written as little-endian");

namespace mm::rlog {

// ---------------- MsgQueue ----------------

MsgQueue::MsgQueue(size_t cap, size_t max_bytes) : cap_(cap), max_(max_bytes), buf_(cap * max_bytes), len_(cap) {}

bool MsgQueue::push(const void* p, size_t n) {
    if (n > max_ || cap_ == 0) return false;
    std::lock_guard<std::mutex> lk(mu_);
    if (count_ == cap_) {  // 가장 오래된 것을 버린다
        head_ = (head_ + 1) % cap_;
        count_--;
        dropped_++;
    }
    const size_t i = (head_ + count_) % cap_;
    std::memcpy(&buf_[i * max_], p, n);
    len_[i] = n;
    count_++;
    return true;
}

bool MsgQueue::pop(std::vector<char>& out) {
    std::lock_guard<std::mutex> lk(mu_);
    if (count_ == 0) return false;
    out.assign(&buf_[head_ * max_], &buf_[head_ * max_] + len_[head_]);
    head_ = (head_ + 1) % cap_;
    count_--;
    return true;
}

uint64_t MsgQueue::dropped() const {
    std::lock_guard<std::mutex> lk(mu_);
    return dropped_;
}

size_t MsgQueue::size() const {
    std::lock_guard<std::mutex> lk(mu_);
    return count_;
}

// ---------------- RideFile (SQLite) ----------------

namespace {


std::string id_of(const char* s, size_t n) { return std::string(s, strnlen(s, n)); }

std::string num_json(double v) {
    if (!std::isfinite(v)) return "null";
    char b[40];
    std::snprintf(b, sizeof b, "%.9g", v);
    return b;
}

bool ends_with(const std::string& s, const std::string& suf) {
    return s.size() >= suf.size() && s.compare(s.size() - suf.size(), suf.size(), suf) == 0;
}

std::vector<std::string> list_dir(const std::string& dir) {
    std::vector<std::string> out;
    DIR* d = opendir(dir.c_str());
    if (!d) return out;
    while (dirent* e = readdir(d)) {
        const std::string n = e->d_name;
        if (n != "." && n != "..") out.push_back(n);
    }
    closedir(d);
    std::sort(out.begin(), out.end());
    return out;
}

void fsync_dir(const std::string& dir) {
    const int fd = open(dir.c_str(), O_RDONLY);
    if (fd >= 0) {
        fsync(fd);
        close(fd);
    }
}

}  // namespace

class RideFile {
public:
    ~RideFile() { close_db(); }

    // create: 새 파일 + 표. 아니면 있는 파일 열기 (복구)
    bool open(const std::string& path, bool create, std::string& err) {
        const int flags = SQLITE_OPEN_READWRITE | (create ? SQLITE_OPEN_CREATE : 0);
        if (sqlite3_open_v2(path.c_str(), &db_, flags, nullptr) != SQLITE_OK) {
            err = db_ ? sqlite3_errmsg(db_) : "sqlite3_open_v2 failed";
            close_db();
            return false;
        }
        if (!exec("PRAGMA journal_mode=WAL") || !exec("PRAGMA synchronous=NORMAL") ||
            !exec("PRAGMA temp_store=MEMORY") || !exec("PRAGMA foreign_keys=ON")) {
            err = last_err_;
            return false;
        }
        if (create && (!exec("BEGIN") || !exec(proto::kRideSchemaSql) || !exec("COMMIT"))) {
            err = last_err_;
            return false;
        }
        return true;
    }

    bool exec(const char* sql) {
        char* msg = nullptr;
        if (sqlite3_exec(db_, sql, nullptr, nullptr, &msg) != SQLITE_OK) {
            last_err_ = std::string(sql).substr(0, 40) + ": " + (msg ? msg : "?");
            sqlite3_free(msg);
            errors_++;
            return false;
        }
        return true;
    }

    // 문장 하나를 준비해서 bind 함수로 값을 넣고 실행. 같은 SQL은 다시 쓴다
    template <class F>
    bool run(const char* sql, F&& bind) {
        sqlite3_stmt*& s = stmts_[sql];
        if (!s && sqlite3_prepare_v2(db_, sql, -1, &s, nullptr) != SQLITE_OK) {
            last_err_ = std::string("prepare: ") + sqlite3_errmsg(db_);
            errors_++;
            s = nullptr;
            return false;
        }
        sqlite3_reset(s);
        sqlite3_clear_bindings(s);
        bind(s);
        const int rc = sqlite3_step(s);
        if (rc != SQLITE_DONE) {
            last_err_ = std::string(sql).substr(0, 40) + ": " + sqlite3_errmsg(db_);
            errors_++;
            return false;
        }
        return true;
    }

    bool meta(const std::string& k, const std::string& v) {
        return run("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", [&](sqlite3_stmt* s) {
            sqlite3_bind_text(s, 1, k.c_str(), -1, SQLITE_TRANSIENT);
            sqlite3_bind_text(s, 2, v.c_str(), -1, SQLITE_TRANSIENT);
        });
    }

    bool quick_check() {
        sqlite3_stmt* s = nullptr;
        bool ok = false;
        if (sqlite3_prepare_v2(db_, "PRAGMA quick_check", -1, &s, nullptr) == SQLITE_OK && sqlite3_step(s) == SQLITE_ROW) {
            const unsigned char* t = sqlite3_column_text(s, 0);
            ok = t && std::strcmp(reinterpret_cast<const char*>(t), "ok") == 0;
        }
        sqlite3_finalize(s);
        if (!ok) return false;
        // 표가 다 만들어지기 전에 꺼진 파일은 meta가 없다
        s = nullptr;
        ok = sqlite3_prepare_v2(db_, "SELECT count(*) FROM meta", -1, &s, nullptr) == SQLITE_OK &&
             sqlite3_step(s) == SQLITE_ROW;
        sqlite3_finalize(s);
        return ok;
    }

    // WAL을 본 파일에 합치고 rollback journal로 바꿔 닫는다: 파일 하나로 완결
    bool finalize() {
        const bool ok = exec("PRAGMA wal_checkpoint(TRUNCATE)") && exec("PRAGMA journal_mode=DELETE");
        close_db();
        return ok;
    }

    void close_db() {
        for (auto& kv : stmts_) sqlite3_finalize(kv.second);
        stmts_.clear();
        if (db_) sqlite3_close(db_);
        db_ = nullptr;
    }

    int take_errors() {
        const int e = errors_;
        errors_ = 0;
        return e;
    }
    const std::string& last_err() const { return last_err_; }

private:
    sqlite3* db_ = nullptr;
    std::map<const char*, sqlite3_stmt*> stmts_;
    int errors_ = 0;
    std::string last_err_;
};

namespace {
void bind_real_or_null(sqlite3_stmt* s, int i, double v) {
    if (std::isfinite(v)) sqlite3_bind_double(s, i, v);
    else sqlite3_bind_null(s, i);
}
}  // namespace

double statvfs_free_mb(const std::string& dir) {
    struct statvfs v;
    if (statvfs(dir.c_str(), &v) != 0) return 1e12;  // 모르면 막지 않는다
    return static_cast<double>(v.f_bavail) * static_cast<double>(v.f_frsize) / 1048576.0;
}

// ---------------- Logger ----------------

Logger::Logger(Config c) : cfg_(std::move(c)) {}

Logger::~Logger() = default;

void Logger::warn(const std::string& s) {
    if (cfg_.warn) cfg_.warn(s);
    else std::fprintf(stderr, "p7: %s\n", s.c_str());
}

double Logger::free_mb() { return cfg_.free_mb ? cfg_.free_mb(cfg_.dir) : statvfs_free_mb(cfg_.dir); }

void Logger::start() {
    store::make_dirs(rides_dir());
    const std::string sc = cfg_.dir + "/session_count";
    std::string text;
    uint64_t n = 0;
    if (store::read_file(sc, text)) n = std::strtoull(text.c_str(), nullptr, 10);
    session_ = n + 1;
    store::write_atomic(sc, std::to_string(session_) + "\n");
    recover();
}

void Logger::recover() {
    const std::string dir = rides_dir();
    const std::string suf = ".open.sqlite";
    for (const std::string& name : list_dir(dir)) {
        if (!ends_with(name, suf)) continue;
        const std::string base = name.substr(0, name.size() - suf.size());
        const std::string path = dir + "/" + name;
        RideFile f;
        std::string err;
        bool ok = f.open(path, false, err) && f.quick_check();
        if (ok) ok = f.meta("closed_reason", "recovered") && f.finalize();
        else f.close_db();
        if (ok && std::rename(path.c_str(), (dir + "/" + base + ".sqlite").c_str()) == 0) {
            std::remove((path + "-wal").c_str());
            std::remove((path + "-shm").c_str());
            st_.recovered++;
            warn("recovered " + base);
        } else {
            // 지우지 않고 이름만 바꿔 둔다 (WAL 조각도 같이, sqlite3로 열어 볼 수 있게)
            const std::string to = dir + "/" + base + ".broken.sqlite";
            std::rename(path.c_str(), to.c_str());
            std::rename((path + "-wal").c_str(), (to + "-wal").c_str());
            std::rename((path + "-shm").c_str(), (to + "-shm").c_str());
            st_.broken++;
            warn("broken ride file kept as " + base + ".broken.sqlite" + (err.empty() ? "" : " (" + err + ")"));
        }
    }
    fsync_dir(dir);
}

void Logger::ensure_txn() {
    if (!file_ || in_txn_) return;
    if (file_->exec("BEGIN")) {
        in_txn_ = true;
    }
}

void Logger::commit() {
    if (!file_ || !in_txn_) return;
    file_->exec("COMMIT");
    in_txn_ = false;
    const int e = file_->take_errors();
    if (e) {
        if (st_.sqlite_errors == 0) warn("sqlite: " + file_->last_err());
        st_.sqlite_errors += static_cast<uint64_t>(e);
    }
}

// 주행 안/밖은 **사건이 생긴 시각**으로만 정한다 (P7에 닿은 때나 그때 샘플이 있었는지와 무관):
// mono_ns가 주행 시작(mono_start_ns)보다 앞이면 주행 전 사건 → ok = false (t NULL).
// 보내는 쪽은 사건을 큐에 두었다가 최대 100 ms 뒤에 보내므로, 주행 전 사건이 주행이 열린 뒤에 닿을 수 있다
double Logger::t_of(uint64_t mono_ns, bool& ok) const {
    ok = !anchors_.empty() && mono_ns >= ride_mono0_;
    if (!ok) return 0;
    auto it = std::upper_bound(anchors_.begin(), anchors_.end(), std::make_pair(mono_ns, UINT64_MAX));
    if (it != anchors_.begin()) --it;
    return static_cast<double>(it->second) / proto::kSampleRateHz +
           (static_cast<double>(mono_ns) - static_cast<double>(it->first)) / 1e9;
}

void Logger::health(uint64_t mono_ns, const std::string& key, double value) {
    if (!writable()) return;
    ensure_txn();
    bool ok;
    const double t = t_of(mono_ns, ok);
    if (file_->run("INSERT INTO health (mono_ns, t, key, value) VALUES (?, ?, ?, ?)", [&](sqlite3_stmt* s) {
            sqlite3_bind_int64(s, 1, static_cast<sqlite3_int64>(mono_ns));
            if (ok) sqlite3_bind_double(s, 2, t);
            else sqlite3_bind_null(s, 2);
            sqlite3_bind_text(s, 3, key.c_str(), -1, SQLITE_TRANSIENT);
            sqlite3_bind_double(s, 4, value);
        }))
        st_.health_rows++;
}

void Logger::open_ride(const ring::Item& it, uint32_t col_mask, uint64_t now) {
    check_storage(now);  // 모자라면 올린 주행부터 지운다
    const double f = free_mb();
    ride_no_++;
    closing_ = false;
    refused_ = false;
    if (f < cfg_.refuse_mb) {
        refused_ = true;
        st_.rides_refused++;
        warn("only " + std::to_string(static_cast<int>(f)) + " MB free, not recording this ride");
        return;
    }
    char id[160];
    std::snprintf(id, sizeof id, "%s-%05llu-%03llu", cfg_.device_id.c_str(), (unsigned long long)session_,
                  (unsigned long long)ride_no_);
    open_path_ = rides_dir() + "/" + id + ".open.sqlite";
    final_path_ = rides_dir() + "/" + id + ".sqlite";
    std::remove(open_path_.c_str());  // 같은 세션·번호는 없어야 하지만, 남은 조각이 있으면 새로
    auto rf = std::make_unique<RideFile>();
    std::string err;
    if (!rf->open(open_path_, true, err)) {
        refused_ = true;
        st_.sqlite_errors++;
        warn("cannot create " + open_path_ + ": " + err);
        return;
    }
    file_ = std::move(rf);
    in_txn_ = false;
    model_written_ = false;
    col_mask_ = col_mask;
    cols_.clear();
    std::string columns = "[";
    for (int c = 1; c < proto::kTripCols; c++)
        if (col_mask & (1u << c)) {
            cols_.push_back(c);
            columns += std::string(cols_.size() > 1 ? ", " : "") + "\"" + proto::kTripNames[c] + "\"";
        }
    columns += "]";
    const bool joined = it.kind != ring::RIDE_START;  // P7이 주행 중간에 시작: 링에 남은 샘플부터
    ride_mono0_ = it.mono_ns;
    anchors_.clear();
    // 첫 기준점은 주행 시작 자체: 첫 샘플(약 10 ms 뒤)이 오기 전에 닿은 사건도 주행 시각을 받는다
    anchors_.emplace_back(it.mono_ns, joined ? it.seq : 0);
    chunk_n_ = 0;
    next_seq_ = 0;
    ring_lost_ride_ = hold_ride_ = 0;
    rate_mono_ = last_mono_ = it.mono_ns;
    rate_seq_ = last_seq_ = joined ? it.seq : 0;
    next_rate_ = it.mono_ns + kRateHealthNs;

    ensure_txn();
    RideFile& fl = *file_;
    fl.meta("schema_version", std::to_string(kSchemaVersion));
    fl.meta("ride_id", id);
    fl.meta("device_id", cfg_.device_id);
    fl.meta("session", std::to_string(session_));
    fl.meta("ride_no", std::to_string(ride_no_));
    fl.meta("source", col_mask == 0x7Fu ? "live" : "replay");
    fl.meta("columns", columns);
    fl.meta("sample_rate_hz", num_json(proto::kSampleRateHz));
    fl.meta("mono_start_ns", std::to_string(it.mono_ns));
    if (joined) fl.meta("joined_at_seq", std::to_string(it.seq));
    const time_t wall = std::time(nullptr);
    if (wall > 1704067200) {  // 2024-01-01 이후면 NTP가 맞춘 것으로 본다 (RTC 없는 Pi는 1970이나 지난 값)
        char b[32];
        std::strftime(b, sizeof b, "%Y-%m-%dT%H:%M:%SZ", std::gmtime(&wall));
        fl.meta("wall_start", b);
    }
    if (!joined) {
        const Row& r = it.row;
        if (std::isfinite(r[ring::S_BIAS_X]))
            fl.meta("imu_bias_dps",
                    "[" + num_json(r[ring::S_BIAS_X]) + ", " + num_json(r[ring::S_BIAS_Y]) + ", " + num_json(r[ring::S_BIAS_Z]) + "]");
        if (std::isfinite(r[ring::S_BIAS_UNCAL])) fl.meta("bias_uncalibrated", r[ring::S_BIAS_UNCAL] != 0 ? "1" : "0");
        if (std::isfinite(r[ring::S_ACCEL_RANGE_G])) fl.meta("accel_range_g", num_json(r[ring::S_ACCEL_RANGE_G]));
        if (std::isfinite(r[ring::S_GYRO_RANGE_DPS])) fl.meta("gyro_range_dps", num_json(r[ring::S_GYRO_RANGE_DPS]));
    }
    for (const auto& kv : cfg_.meta) fl.meta(kv.first, kv.second);
    // 주행 전에 받은 마지막 화면·프로필 상태 (mono_ns < mono_start_ns로 구분)
    for (const auto& kv : snapshot_) {
        const Ctx& c = kv.second;
        if (fl.run("INSERT INTO ctx (mono_ns, t, sender, kind, value) VALUES (?, NULL, ?, ?, ?)", [&](sqlite3_stmt* s) {
                sqlite3_bind_int64(s, 1, static_cast<sqlite3_int64>(c.mono_ns));
                sqlite3_bind_text(s, 2, c.sender.c_str(), -1, SQLITE_TRANSIENT);
                sqlite3_bind_text(s, 3, c.kind.c_str(), -1, SQLITE_TRANSIENT);
                sqlite3_bind_text(s, 4, c.value.c_str(), -1, SQLITE_TRANSIENT);
            }))
            st_.ctx_rows++;
    }
    // 주행 전에 있었던 기기 사건 (폰은 보통 주행이 시작되기 전에 붙는다: HELLO, CONNECT, 첫 INT …). t는 NULL
    for (const Ctx& c : pre_) {
        if (fl.run("INSERT INTO ctx (mono_ns, t, sender, kind, value) VALUES (?, NULL, ?, ?, ?)", [&](sqlite3_stmt* s) {
                sqlite3_bind_int64(s, 1, static_cast<sqlite3_int64>(c.mono_ns));
                sqlite3_bind_text(s, 2, c.sender.c_str(), -1, SQLITE_TRANSIENT);
                sqlite3_bind_text(s, 3, c.kind.c_str(), -1, SQLITE_TRANSIENT);
                sqlite3_bind_text(s, 4, c.value.c_str(), -1, SQLITE_TRANSIENT);
            }))
            st_.ctx_rows++;
    }
    pre_.clear();
    st_.rides_opened++;
    commit();  // meta는 바로 디스크에
}

void Logger::flush_chunk() {
    if (chunk_n_ == 0) return;
    if (writable() && !st_.samples_paused) {
        ensure_txn();
        const size_t hold_bytes = (static_cast<size_t>(chunk_n_) + 7) / 8;
        file_->run("INSERT OR REPLACE INTO samples (seq0, n, mono_ns0, data, hold) VALUES (?, ?, ?, ?, ?)",
                   [&](sqlite3_stmt* s) {
                       sqlite3_bind_int64(s, 1, static_cast<sqlite3_int64>(chunk_seq0_));
                       sqlite3_bind_int(s, 2, chunk_n_);
                       sqlite3_bind_int64(s, 3, static_cast<sqlite3_int64>(chunk_mono0_));
                       sqlite3_bind_blob(s, 4, chunk_.data(), static_cast<int>(chunk_.size() * sizeof(float)),
                                         SQLITE_TRANSIENT);
                       if (chunk_has_hold_)
                           sqlite3_bind_blob(s, 5, chunk_hold_.data(), static_cast<int>(hold_bytes), SQLITE_TRANSIENT);
                       else
                           sqlite3_bind_null(s, 5);
                   });
    }
    chunk_n_ = 0;
    chunk_.clear();
    chunk_hold_.clear();
    chunk_has_hold_ = false;
}

void Logger::on_item(const ring::Item& it, uint32_t col_mask, uint64_t now) {
    if (it.kind == ring::RIDE_START) {
        if (file_ || refused_) close_ride(closing_ ? "ride_end" : "p1_restart", now);
        open_ride(it, col_mask, now);
        return;
    }
    if (it.kind == ring::RIDE_END) {
        if (!file_ && !refused_) return;
        flush_chunk();
        if (file_) file_->meta("p1_samples", std::to_string(it.seq));
        closing_ = true;
        close_at_ = now + kCloseGraceNs;
        if (refused_) close_ride("ride_end", now);
        return;
    }
    // SAMPLE
    if (closing_) {
        st_.outside_ride++;
        return;
    }
    if (!file_ && !refused_) open_ride(it, col_mask, now);  // 주행 중간에 P7이 (다시) 시작
    st_.samples++;
    if (it.flags & 1u) {
        st_.hold_samples++;
        hold_ride_++;
    }
    if (!file_) return;
    if (chunk_n_ > 0 && it.seq != next_seq_) flush_chunk();  // 링이 넘쳐 빠짐: 새 묶음
    if (chunk_n_ == 0) {
        chunk_seq0_ = it.seq;
        chunk_mono0_ = it.mono_ns;
        anchors_.emplace_back(it.mono_ns, it.seq);
        chunk_.reserve(static_cast<size_t>(kChunkSamples) * cols_.size());
        chunk_hold_.assign((kChunkSamples + 7) / 8, 0);
    }
    for (int c : cols_) chunk_.push_back(static_cast<float>(it.row[static_cast<size_t>(c)]));
    if (it.flags & 1u) {
        chunk_hold_[static_cast<size_t>(chunk_n_) / 8] |= static_cast<uint8_t>(1u << (chunk_n_ % 8));
        chunk_has_hold_ = true;
    }
    chunk_n_++;
    next_seq_ = it.seq + 1;
    last_mono_ = it.mono_ns;
    last_seq_ = it.seq;
    if (chunk_n_ >= kChunkSamples) flush_chunk();
    if (it.mono_ns >= next_rate_ && it.mono_ns > rate_mono_) {
        health(it.mono_ns, "sample_rate_hz",
               static_cast<double>(it.seq - rate_seq_) * 1e9 / static_cast<double>(it.mono_ns - rate_mono_));
        rate_mono_ = it.mono_ns;
        rate_seq_ = it.seq;
        next_rate_ = it.mono_ns + kRateHealthNs;
    }
}

void Logger::on_ring_lost(uint64_t n) {
    st_.ring_lost += n;
    if (file_) ring_lost_ride_ += n;
}

void Logger::on_p1_restart(uint64_t now) {
    if (file_ || refused_) close_ride(closing_ ? "ride_end" : "p1_restart", now);
}

// P6·P5는 P7로 보내기가 2 ms 안에 회신을 못 받으면 같은 사건을 다음 바퀴에 다시 보낸다 (사건을 잃지 않으려고).
// 그런데 회신만 늦었고 P7은 이미 받은 경우가 있다 → 같은 사건이 두 번 온다. 사건마다 생긴 시각(ns)이 찍혀 있으므로
// 시각과 내용이 모두 같은 것은 같은 사건이다: 한 번만 적는다
bool Logger::seen_before(const std::string& key) {
    if (std::find(recent_.begin(), recent_.end(), key) != recent_.end()) {
        st_.dup_msgs++;
        return true;
    }
    if (recent_.size() >= kRecentKeys) recent_.pop_front();
    recent_.push_back(key);
    return false;
}

void Logger::on_msg(const void* p, size_t n, uint64_t now, uint64_t queue_drop_total) {
    (void)now;
    queue_drop_seen_ = std::max(queue_drop_seen_, queue_drop_total);
    if (n < sizeof(msg::Hdr)) {
        st_.bad_msgs++;
        return;
    }
    msg::Hdr h;
    std::memcpy(&h, p, sizeof h);
    switch (h.type) {
        case msg::LOG_INFER: {
            if (n < sizeof(msg::LogInfer)) break;
            static msg::LogInfer m;  // 6.7 KB: 쓰기 스레드 하나만 쓴다
            std::memcpy(&m, p, sizeof m);
            if (!writable()) {
                st_.outside_ride++;
                return;
            }
            ensure_txn();
            if (!model_written_ && m.model[0]) {
                file_->meta("model_version", id_of(m.model, sizeof m.model));
                model_written_ = true;
            }
            const uint32_t rows = std::min<uint32_t>(m.n, msg::kMaxPhones);
            for (uint32_t r = 0; r < rows; r++) {
                const std::string phone = id_of(m.id[r], msg::kIdLen);
                if (file_->run("INSERT OR REPLACE INTO infer (seq, t, mono_ns, phone, x, p2, p3, p4, p5, p6, cal_a, cal_b) "
                               "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                               [&](sqlite3_stmt* s) {
                                   sqlite3_bind_int64(s, 1, static_cast<sqlite3_int64>(m.seq));
                                   sqlite3_bind_double(s, 2, m.t);
                                   sqlite3_bind_int64(s, 3, static_cast<sqlite3_int64>(m.mono_ns));
                                   sqlite3_bind_text(s, 4, phone.c_str(), -1, SQLITE_TRANSIENT);
                                   sqlite3_bind_blob(s, 5, m.x[r], sizeof m.x[r], SQLITE_TRANSIENT);
                                   for (int k = 0; k < 5; k++) bind_real_or_null(s, 6 + k, m.p[r][static_cast<size_t>(k)]);
                                   sqlite3_bind_double(s, 11, m.cal_a[r]);
                                   sqlite3_bind_double(s, 12, m.cal_b[r]);
                               }))
                    st_.infer_rows++;
            }
            return;
        }
        case msg::LOG_DECISION: {
            if (n < sizeof(msg::LogDecision)) break;
            msg::LogDecision m;
            std::memcpy(&m, p, sizeof m);
            if (!writable()) {
                st_.outside_ride++;
                return;
            }
            ensure_txn();
            const uint32_t rows = std::min<uint32_t>(m.n, msg::kMaxDevices);
            for (uint32_t r = 0; r < rows; r++) {
                const std::string dev = id_of(m.d[r].device, msg::kIdLen);
                if (file_->run("INSERT OR REPLACE INTO decision (seq, t, device, on_, tier, changed) VALUES (?, ?, ?, ?, ?, ?)",
                               [&](sqlite3_stmt* s) {
                                   sqlite3_bind_int64(s, 1, static_cast<sqlite3_int64>(m.seq));
                                   sqlite3_bind_double(s, 2, m.t);
                                   sqlite3_bind_text(s, 3, dev.c_str(), -1, SQLITE_TRANSIENT);
                                   sqlite3_bind_int(s, 4, m.d[r].on);
                                   sqlite3_bind_int(s, 5, m.d[r].tier);
                                   sqlite3_bind_int(s, 6, m.d[r].changed);
                               }))
                    st_.decision_rows++;
            }
            return;
        }
        case msg::LOG_CTX: {
            if (n < sizeof(msg::LogCtx)) break;
            msg::LogCtx m;
            std::memcpy(&m, p, sizeof m);
            Ctx c{m.mono_ns, id_of(m.sender, msg::kIdLen), msg::ctx_kind_name(m.kind), id_of(m.value, sizeof m.value)};
            if (seen_before(std::to_string(c.mono_ns) + "|" + c.sender + "|" + c.kind + "|" + c.value)) return;
            if (m.kind == msg::CtxKind::SCR || m.kind == msg::CtxKind::PROF) snapshot_[{c.sender, c.kind}] = c;
            if (!writable()) {
                if (m.kind == msg::CtxKind::SCR || m.kind == msg::CtxKind::PROF) return;  // 마지막 값은 위에서 기억
                if (refused_) {  // 공간이 없어 기록하지 않는 주행
                    st_.outside_ride++;
                    return;
                }
                // 주행 밖: 다음 주행 파일을 열 때 적는다. 넘치면 가장 오래된 것을 버리고 센다
                if (pre_.size() >= kPreRideCtx) {
                    pre_.pop_front();
                    st_.outside_ride++;
                }
                pre_.push_back(c);
                return;
            }
            ensure_txn();
            bool ok;
            const double t = t_of(m.mono_ns, ok);
            if (file_->run("INSERT INTO ctx (mono_ns, t, sender, kind, value) VALUES (?, ?, ?, ?, ?)", [&](sqlite3_stmt* s) {
                    sqlite3_bind_int64(s, 1, static_cast<sqlite3_int64>(c.mono_ns));
                    if (ok) sqlite3_bind_double(s, 2, t);
                    else sqlite3_bind_null(s, 2);
                    sqlite3_bind_text(s, 3, c.sender.c_str(), -1, SQLITE_TRANSIENT);
                    sqlite3_bind_text(s, 4, c.kind.c_str(), -1, SQLITE_TRANSIENT);
                    sqlite3_bind_text(s, 5, c.value.c_str(), -1, SQLITE_TRANSIENT);
                }))
                st_.ctx_rows++;
            return;
        }
        case msg::LOG_SYM: {
            if (n < sizeof(msg::LogSym)) break;
            msg::LogSym m;
            std::memcpy(&m, p, sizeof m);
            if (m.misc < 0 || m.misc > 10) break;
            if (seen_before(std::to_string(m.mono_ns) + "|" + id_of(m.phone, msg::kIdLen) + "|SYM|" + std::to_string(m.misc)))
                return;
            if (!writable()) {
                st_.outside_ride++;
                return;
            }
            ensure_txn();
            bool ok;
            const double t = t_of(m.mono_ns, ok);
            const std::string phone = id_of(m.phone, msg::kIdLen);
            if (file_->run("INSERT INTO label (mono_ns, t, phone, misc) VALUES (?, ?, ?, ?)", [&](sqlite3_stmt* s) {
                    sqlite3_bind_int64(s, 1, static_cast<sqlite3_int64>(m.mono_ns));
                    if (ok) sqlite3_bind_double(s, 2, t);
                    else sqlite3_bind_null(s, 2);
                    sqlite3_bind_text(s, 3, phone.c_str(), -1, SQLITE_TRANSIENT);
                    sqlite3_bind_int(s, 4, m.misc);
                }))
                st_.label_rows++;
            return;
        }
        case msg::LOG_GPS: {
            // M09 GPS (#18): 주행 안에서만 남긴다 (주행 밖의 속도는 쓸 데가 없다). 다시 보낸 것은 같은 시각·폰으로 알아본다
            if (n < sizeof(msg::LogGps)) break;
            msg::LogGps m;
            std::memcpy(&m, p, sizeof m);
            if (!std::isfinite(m.spd) || !std::isfinite(m.acc)) break;
            const std::string phone = id_of(m.phone, msg::kIdLen);
            if (seen_before(std::to_string(m.mono_ns) + "|" + phone + "|GPS")) return;
            if (!writable()) {
                st_.outside_ride++;
                return;
            }
            ensure_txn();
            bool ok;
            const double t = t_of(m.mono_ns, ok);
            if (file_->run("INSERT INTO gps (mono_ns, t, phone, spd, brg, acc, alt) VALUES (?, ?, ?, ?, ?, ?, ?)",
                           [&](sqlite3_stmt* s) {
                               sqlite3_bind_int64(s, 1, static_cast<sqlite3_int64>(m.mono_ns));
                               if (ok) sqlite3_bind_double(s, 2, t);
                               else sqlite3_bind_null(s, 2);
                               sqlite3_bind_text(s, 3, phone.c_str(), -1, SQLITE_TRANSIENT);
                               sqlite3_bind_double(s, 4, m.spd);
                               bind_real_or_null(s, 5, m.brg);
                               sqlite3_bind_double(s, 6, m.acc);
                               bind_real_or_null(s, 7, m.alt);
                           }))
                st_.gps_rows++;
            return;
        }
        default:
            break;
    }
    st_.bad_msgs++;
}

void Logger::close_ride(const std::string& reason, uint64_t now) {
    const bool was_refused = refused_;
    refused_ = false;
    closing_ = false;
    if (was_refused || !file_) return;
    flush_chunk();
    ensure_txn();
    const uint64_t qd = queue_drop_seen_;
    health(last_mono_, "ring_overrun", static_cast<double>(ring_lost_ride_));
    health(last_mono_, "queue_drop", static_cast<double>(qd));
    health(last_mono_, "hold_samples", static_cast<double>(hold_ride_));
    file_->meta("closed_reason", reason);
    file_->meta("mono_end_ns", std::to_string(now));
    commit();
    if (!file_->finalize()) {
        st_.sqlite_errors++;
        warn("finalize " + open_path_ + ": " + file_->last_err());
    }
    file_.reset();
    in_txn_ = false;
    if (std::rename(open_path_.c_str(), final_path_.c_str()) != 0) {
        warn("rename " + open_path_ + " failed: " + std::strerror(errno));
        return;
    }
    // 연결을 닫은 뒤라 남은 WAL 조각은 쓰이지 않는다 (일부 SQLite 빌드는 -shm을 지우지 않는다)
    std::remove((open_path_ + "-wal").c_str());
    std::remove((open_path_ + "-shm").c_str());
    fsync_dir(rides_dir());
    st_.rides_closed++;
    if (cfg_.on_closed) cfg_.on_closed(final_path_, reason);
}

void Logger::check_storage(uint64_t now) {
    (void)now;
    double f = free_mb();
    if (f < cfg_.min_free_mb) {
        // 서버가 받은(.uploaded) 주행만, 가장 오래된 것부터. 올리지 않은 주행은 절대 지우지 않는다
        const std::string dir = rides_dir();
        for (const std::string& name : list_dir(dir)) {
            if (f >= cfg_.min_free_mb) break;
            if (!ends_with(name, ".uploaded")) continue;
            const std::string base = name.substr(0, name.size() - std::strlen(".uploaded"));
            const std::string ride = dir + "/" + base + ".sqlite";
            if (ride == final_path_ && file_) continue;
            std::remove(ride.c_str());
            std::remove((dir + "/" + name).c_str());
            st_.deleted_uploaded++;
            warn("low space: deleted uploaded ride " + base);
            f = free_mb();
        }
    }
    const bool pause = f < cfg_.min_free_mb;
    if (pause != st_.samples_paused) {
        st_.samples_paused = pause;
        warn(pause ? "low space: samples paused" : "space ok: samples resumed");
        health(last_mono_, "samples_paused", pause ? 1 : 0);
    }
}

void Logger::tick(uint64_t now) {
    if (closing_ && now >= close_at_) close_ride("ride_end", now);
    if (in_txn_ && now - last_commit_ >= kCommitNs) {
        commit();
        last_commit_ = now;
    } else if (!in_txn_) {
        last_commit_ = now;
    }
    if (now >= next_storage_) {
        check_storage(now);
        next_storage_ = now + kStorageCheckNs;
    }
}

void Logger::shutdown(uint64_t now) {
    if (file_ || refused_) close_ride(closing_ ? "ride_end" : "shutdown", now);
}

}  // namespace mm::rlog
