// P7 로거 코어 테스트: 주행 파일 내용, 1초 묶음과 hold, 늦은 LOG_*, 주행 밖 상태, P1 재시작, 중간 합류,
// 복구(.open → 닫힘 / .broken), 저장 공간 규칙, 큐 넘침.
#include <sqlite3.h>
#include <sys/stat.h>
#include <unistd.h>

#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <string>
#include <vector>

#include "ride_log.hpp"

using namespace mm;
using namespace mm::rlog;

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

static const uint64_t S = 1000000000ULL;

static bool exists(const std::string& p) {
    struct stat sb;
    return stat(p.c_str(), &sb) == 0;
}

// 첫 행 첫 칸 (없으면 "<none>")
static std::string q1(const std::string& db, const std::string& sql) {
    sqlite3* d = nullptr;
    std::string out = "<none>";
    if (sqlite3_open_v2(db.c_str(), &d, SQLITE_OPEN_READONLY, nullptr) == SQLITE_OK) {
        sqlite3_stmt* s = nullptr;
        if (sqlite3_prepare_v2(d, sql.c_str(), -1, &s, nullptr) == SQLITE_OK && sqlite3_step(s) == SQLITE_ROW) {
            const unsigned char* t = sqlite3_column_text(s, 0);
            out = t ? reinterpret_cast<const char*>(t) : "NULL";
        } else if (s == nullptr) {
            out = std::string("<error ") + sqlite3_errmsg(d) + ">";
        }
        sqlite3_finalize(s);
    }
    sqlite3_close(d);
    return out;
}

static std::vector<char> blob(const std::string& db, const std::string& sql) {
    sqlite3* d = nullptr;
    std::vector<char> out;
    if (sqlite3_open_v2(db.c_str(), &d, SQLITE_OPEN_READONLY, nullptr) == SQLITE_OK) {
        sqlite3_stmt* s = nullptr;
        if (sqlite3_prepare_v2(d, sql.c_str(), -1, &s, nullptr) == SQLITE_OK && sqlite3_step(s) == SQLITE_ROW) {
            const char* b = static_cast<const char*>(sqlite3_column_blob(s, 0));
            out.assign(b, b + sqlite3_column_bytes(s, 0));
        }
        sqlite3_finalize(s);
    }
    sqlite3_close(d);
    return out;
}

static ring::Item start_item(uint64_t mono) {
    ring::Item it;
    it.kind = ring::RIDE_START;
    it.mono_ns = mono;
    it.row = ring::start_info_unknown();
    it.row[ring::S_BIAS_X] = 1.0;
    it.row[ring::S_BIAS_Y] = -0.5;
    it.row[ring::S_BIAS_Z] = 0.25;
    it.row[ring::S_BIAS_UNCAL] = 0;
    it.row[ring::S_ACCEL_RANGE_G] = 4;
    it.row[ring::S_GYRO_RANGE_DPS] = 250;
    return it;
}

static ring::Item sample(uint64_t seq, uint64_t mono, uint32_t flags = 0) {
    ring::Item it;
    it.kind = ring::SAMPLE;
    it.seq = seq;
    it.mono_ns = mono;
    it.flags = flags;
    it.row.fill(std::nan(""));
    it.row[proto::C_T] = seq / 100.0;
    for (int c = proto::C_AX; c <= proto::C_GZ; c++) it.row[c] = c * 1000.0 + static_cast<double>(seq);
    return it;
}

static ring::Item end_item(uint64_t sent, uint64_t mono) {
    ring::Item it;
    it.kind = ring::RIDE_END;
    it.seq = sent;
    it.mono_ns = mono;
    return it;
}

static msg::LogInfer infer_msg(uint64_t seq, double t, uint64_t mono) {
    msg::LogInfer m;
    m.seq = seq;
    m.t = t;
    m.mono_ns = mono;
    msg::copy_text(m.model, std::string("v007"));
    m.n = 2;
    msg::copy_id(m.id[0], "phone-a");
    msg::copy_id(m.id[1], "phone-b");
    for (int r = 0; r < 2; r++) {
        for (int f = 0; f < proto::kNumFeatures; f++) m.x[r][f] = r * 100 + f + 0.5;
        m.x[r][3] = std::nan("");
        m.p[r] = {0.9, 0.7, 0.5, 0.3, 0.1};
        m.cal_a[r] = 1;
        m.cal_b[r] = 0;
    }
    m.p[1][4] = std::nan("");
    return m;
}

static msg::LogCtx ctx_msg(const char* sender, msg::CtxKind k, const char* value, uint64_t mono) {
    msg::LogCtx m;
    m.mono_ns = mono;
    msg::copy_id(m.sender, sender);
    m.kind = k;
    msg::copy_text(m.value, std::string(value));
    return m;
}

static std::string fresh_dir(const char* tag) {
    char tmpl[256];
    std::snprintf(tmpl, sizeof tmpl, "/tmp/mmlog_%s_XXXXXX", tag);
    const char* d = mkdtemp(tmpl);
    return d ? d : "/tmp";
}

static Config cfg_for(const std::string& dir, double* free_mb = nullptr) {
    Config c;
    c.dir = dir;
    c.device_id = "pi01";
    c.meta["git_commit"] = "abc1234";
    if (free_mb) c.free_mb = [free_mb](const std::string&) { return *free_mb; };
    else c.free_mb = [](const std::string&) { return 1e9; };
    c.warn = [](const std::string&) {};
    return c;
}

static void test_queue() {
    MsgQueue q(4, 16);
    char b[16] = {};
    for (int i = 0; i < 6; i++) {
        b[0] = static_cast<char>(i);
        CHECK(q.push(b, 8), "push %d", i);
    }
    CHECK(!q.push(b, 17), "too big rejected");
    CHECK(q.dropped() == 2 && q.size() == 4, "drop oldest: dropped %llu size %zu", (unsigned long long)q.dropped(), q.size());
    std::vector<char> out;
    CHECK(q.pop(out) && out.size() == 8 && out[0] == 2, "oldest kept is #2");
    int n = 1;
    while (q.pop(out)) n++;
    CHECK(n == 4 && out[0] == 5, "rest in order");
}

static void test_normal_ride() {
    const std::string dir = fresh_dir("ride");
    std::vector<std::string> closed;
    Config c = cfg_for(dir);
    c.on_closed = [&](const std::string& p, const std::string& r) { closed.push_back(p + "|" + r); };
    Logger lg(c);
    lg.start();
    CHECK(lg.session() == 1, "first session 1");

    uint64_t now = 100 * S;
    // 주행 전에 받은 화면 상태: 주행 파일에 t NULL로 들어간다
    msg::LogCtx scr = ctx_msg("phone-a", msg::CtxKind::SCR, "{\"on\":1,\"att\":1}", now - 5 * S);
    lg.on_msg(&scr, sizeof scr, now);
    msg::LogCtx hello = ctx_msg("phone-a", msg::CtxKind::HELLO, "{\"conn\":1}", now - 4 * S);
    lg.on_msg(&hello, sizeof hello, now);
    CHECK(lg.stats().outside_ride == 1, "HELLO outside a ride is counted, SCR kept as state");

    lg.on_item(start_item(now), 0x7F, now);
    CHECK(lg.ride_open(), "ride open after RIDE_START");
    const uint64_t m0 = now + 10 * 1000000ULL;
    for (uint64_t i = 0; i < 250; i++) lg.on_item(sample(i, m0 + i * 10000000ULL, (i == 120 || i == 121) ? 1u : 0u), 0x7F, now);
    lg.tick(now + 1 * S);
    msg::LogInfer inf = infer_msg(0, 0.99, m0 + 100 * 10000000ULL);
    lg.on_msg(&inf, sizeof inf, now);
    msg::LogDecision dec;
    dec.seq = 0;
    dec.t = 0.99;
    dec.n = 2;
    msg::copy_id(dec.d[0].device, "airflow-01");
    dec.d[0].on = 1;
    dec.d[0].tier = 1;
    dec.d[0].changed = 1;
    msg::copy_id(dec.d[1].device, "phone-a");
    lg.on_msg(&dec, sizeof dec, now);
    msg::LogCtx ovr = ctx_msg("phone-a", msg::CtxKind::OVR, "{\"device\":\"airflow-01\",\"on\":0}", m0 + 150 * 10000000ULL);
    lg.on_msg(&ovr, sizeof ovr, now);
    msg::LogSym sym;
    sym.mono_ns = m0 + 200 * 10000000ULL;
    msg::copy_id(sym.phone, "phone-a");
    sym.misc = 4;
    lg.on_msg(&sym, sizeof sym, now);
    msg::LogSym bad = sym;
    bad.misc = 11;
    lg.on_msg(&bad, sizeof bad, now);
    CHECK(lg.stats().bad_msgs == 1, "misc 11 rejected");
    char junk[4] = {1, 2, 3, 4};
    lg.on_msg(junk, sizeof junk, now);
    CHECK(lg.stats().bad_msgs == 2, "short message rejected");

    lg.on_item(end_item(250, m0 + 250 * 10000000ULL), 0x7F, now + 3 * S);
    CHECK(lg.ride_open(), "still open during grace");
    // 늦게 온 주기 로그는 같은 주행에
    msg::LogInfer late = infer_msg(1, 2.49, m0 + 251 * 10000000ULL);
    lg.on_msg(&late, sizeof late, now + 3 * S);
    lg.tick(now + 4 * S);
    CHECK(lg.ride_open(), "grace is 2 s");
    lg.tick(now + 5 * S + 1);
    CHECK(!lg.ride_open(), "closed after grace");
    lg.on_msg(&late, sizeof late, now + 6 * S);
    CHECK(lg.stats().outside_ride == 2, "infer after close is outside");

    const std::string f = dir + "/rides/pi01-00001-001.sqlite";
    CHECK(closed.size() == 1 && closed[0] == f + "|ride_end", "on_closed: %s", closed.empty() ? "-" : closed[0].c_str());
    CHECK(exists(f) && !exists(dir + "/rides/pi01-00001-001.open.sqlite"), "renamed to closed name");
    CHECK(!exists(f + "-wal"), "no WAL left (single file)");
    const std::string op = dir + "/rides/pi01-00001-001.open.sqlite";
    CHECK(!exists(op + "-wal") && !exists(op + "-shm"), "no WAL pieces left under the open name");
    CHECK(q1(f, "PRAGMA journal_mode") == "delete", "journal mode delete: %s", q1(f, "PRAGMA journal_mode").c_str());
    auto meta = [&](const char* k) { return q1(f, std::string("SELECT value FROM meta WHERE key='") + k + "'"); };
    CHECK(meta("schema_version") == "1", "schema_version");
    CHECK(meta("ride_id") == "pi01-00001-001", "ride_id %s", meta("ride_id").c_str());
    CHECK(meta("source") == "live", "source live");
    CHECK(meta("columns") == "[\"ax\", \"ay\", \"az\", \"gx\", \"gy\", \"gz\"]", "columns %s", meta("columns").c_str());
    CHECK(meta("imu_bias_dps") == "[1, -0.5, 0.25]", "bias %s", meta("imu_bias_dps").c_str());
    CHECK(meta("accel_range_g") == "4" && meta("gyro_range_dps") == "250", "ranges");
    CHECK(meta("bias_uncalibrated") == "0", "bias calibrated");
    CHECK(meta("model_version") == "v007", "model version from LOG_INFER");
    CHECK(meta("git_commit") == "abc1234", "static meta");
    CHECK(meta("closed_reason") == "ride_end", "closed_reason");
    CHECK(meta("p1_samples") == "250", "p1 sample count");
    CHECK(meta("joined_at_seq") == "<none>", "not joined");

    CHECK(q1(f, "SELECT count(*) FROM samples") == "3", "3 chunks");
    CHECK(q1(f, "SELECT group_concat(n) FROM (SELECT n FROM samples ORDER BY seq0)") == "100,100,50", "chunk sizes");
    CHECK(q1(f, "SELECT sum(n) FROM samples") == "250", "every sample stored");
    CHECK(q1(f, "SELECT count(*) FROM samples WHERE hold IS NOT NULL") == "1", "one chunk has holds");
    std::vector<char> d = blob(f, "SELECT data FROM samples WHERE seq0=100");
    CHECK(d.size() == 100 * 6 * sizeof(float), "data size %zu", d.size());
    if (d.size() == 100 * 6 * sizeof(float)) {
        const float* v = reinterpret_cast<const float*>(d.data());
        CHECK(v[0] == 1100.0f && v[5] == 6100.0f && v[6 * 99 + 2] == 3199.0f, "row-major float32 values");
    }
    std::vector<char> hb = blob(f, "SELECT hold FROM samples WHERE seq0=100");
    CHECK(hb.size() == 13 && static_cast<uint8_t>(hb[2]) == 0x30, "hold bits 20,21 of chunk 100");

    CHECK(q1(f, "SELECT count(*) FROM infer") == "4", "infer rows 2 cycles x 2 phones");
    CHECK(q1(f, "SELECT p6 FROM infer WHERE seq=0 AND phone='phone-b'") == "NULL", "NaN prob stored as NULL");
    CHECK(q1(f, "SELECT p2 FROM infer WHERE seq=0 AND phone='phone-a'") == "0.9", "p2");
    std::vector<char> x = blob(f, "SELECT x FROM infer WHERE seq=0 AND phone='phone-b'");
    CHECK(x.size() == proto::kNumFeatures * sizeof(double), "x blob kNumFeatures doubles");
    if (x.size() == proto::kNumFeatures * sizeof(double)) {
        const double* v = reinterpret_cast<const double*>(x.data());
        constexpr int last = proto::kNumFeatures - 1;
        CHECK(v[0] == 100.5 && std::isnan(v[3]) && v[last] == 100 + last + 0.5, "x values kept incl. NaN");
    }
    CHECK(q1(f, "SELECT count(*) FROM decision") == "2", "decision rows");
    CHECK(q1(f, "SELECT on_ || tier || changed FROM decision WHERE device='airflow-01'") == "111", "decision values");
    CHECK(q1(f, "SELECT count(*) FROM ctx WHERE kind='SCR' AND t IS NULL") == "1", "state before ride kept");
    CHECK(q1(f, "SELECT count(*) FROM ctx WHERE kind='HELLO'") == "0", "event outside ride not in file");
    CHECK(q1(f, "SELECT printf('%.2f', t) FROM ctx WHERE kind='OVR'") == "1.50", "ctx t from mono_ns: %s",
          q1(f, "SELECT t FROM ctx WHERE kind='OVR'").c_str());
    CHECK(q1(f, "SELECT printf('%.2f', t) || ' ' || misc FROM label") == "2.00 4", "label");
    CHECK(q1(f, "SELECT value FROM health WHERE key='hold_samples'") == "2.0", "hold health %s",
          q1(f, "SELECT value FROM health WHERE key='hold_samples'").c_str());
    CHECK(q1(f, "SELECT value FROM health WHERE key='ring_overrun'") == "0.0", "ring overrun health");
}

static void test_gap_restart_join() {
    const std::string dir = fresh_dir("gap");
    Logger lg(cfg_for(dir));
    lg.start();
    uint64_t now = 10 * S;
    lg.on_item(start_item(now), 0xFFFFFFFFu, now);
    for (uint64_t i = 0; i < 30; i++) lg.on_item(sample(i, now + i * 10000000ULL), 0xFFFFFFFFu, now);
    lg.on_ring_lost(70);
    for (uint64_t i = 100; i < 130; i++) lg.on_item(sample(i, now + i * 10000000ULL), 0xFFFFFFFFu, now);
    lg.on_p1_restart(now + 2 * S);
    CHECK(!lg.ride_open(), "closed on P1 restart");
    const std::string f1 = dir + "/rides/pi01-00001-001.sqlite";
    CHECK(q1(f1, "SELECT value FROM meta WHERE key='closed_reason'") == "p1_restart", "reason p1_restart");
    CHECK(q1(f1, "SELECT group_concat(seq0) FROM (SELECT seq0 FROM samples ORDER BY seq0)") == "0,100", "gap starts new chunk");
    CHECK(q1(f1, "SELECT value FROM meta WHERE key='source'") == "replay", "replay source");
    CHECK(q1(f1, "SELECT length(data) FROM samples WHERE seq0=0") == std::to_string(30 * 31 * 4), "31 columns in replay");
    CHECK(q1(f1, "SELECT value FROM health WHERE key='ring_overrun'") == "70.0", "ring overrun counted");
    CHECK(q1(f1, "SELECT value FROM meta WHERE key='imu_bias_dps'") == "[1, -0.5, 0.25]", "bias meta");

    // P7이 주행 중간에 시작: RIDE_START 없이 샘플부터
    for (uint64_t i = 500; i < 520; i++) lg.on_item(sample(i, now + 10 * S + i * 10000000ULL), 0x7F, now + 10 * S);
    CHECK(lg.ride_open(), "joined ride opened");
    lg.on_item(end_item(520, now + 20 * S), 0x7F, now + 20 * S);
    lg.tick(now + 23 * S);
    const std::string f2 = dir + "/rides/pi01-00001-002.sqlite";
    CHECK(q1(f2, "SELECT value FROM meta WHERE key='joined_at_seq'") == "500", "joined_at_seq");
    CHECK(q1(f2, "SELECT value FROM meta WHERE key='imu_bias_dps'") == "<none>", "no bias when joined");
    CHECK(q1(f2, "SELECT seq0 FROM samples") == "500", "joined samples kept with their seq");
}

static void test_recovery() {
    const std::string dir = fresh_dir("rec");
    {
        Logger lg(cfg_for(dir));
        lg.start();
        lg.on_item(start_item(S), 0x7F, S);
        for (uint64_t i = 0; i < 150; i++) lg.on_item(sample(i, S + i * 10000000ULL), 0x7F, S);
        lg.tick(3 * S);
        // 전원이 꺼진 것처럼: 닫지 않고 끝
    }
    std::ofstream(dir + "/rides/pi01-00000-009.open.sqlite") << "this is not a database";
    Logger lg2(cfg_for(dir));
    lg2.start();
    CHECK(lg2.session() == 2, "session count kept across starts");
    CHECK(lg2.stats().recovered == 1 && lg2.stats().broken == 1, "recovered %llu broken %llu",
          (unsigned long long)lg2.stats().recovered, (unsigned long long)lg2.stats().broken);
    const std::string f = dir + "/rides/pi01-00001-001.sqlite";
    CHECK(exists(f), "recovered file renamed");
    CHECK(q1(f, "SELECT value FROM meta WHERE key='closed_reason'") == "recovered", "closed_reason recovered");
    CHECK(q1(f, "SELECT sum(n) FROM samples") == "100", "committed chunk survives (%s)", q1(f, "SELECT sum(n) FROM samples").c_str());
    CHECK(exists(dir + "/rides/pi01-00000-009.broken.sqlite"), "broken file kept, renamed");
    lg2.on_item(start_item(10 * S), 0x7F, 10 * S);
    lg2.shutdown(11 * S);
    CHECK(q1(dir + "/rides/pi01-00002-001.sqlite", "SELECT value FROM meta WHERE key='closed_reason'") == "shutdown",
          "new session numbering and shutdown reason");
}

static void test_storage() {
    const std::string dir = fresh_dir("disk");
    double free_mb = 5000;
    Logger lg(cfg_for(dir, &free_mb));
    lg.start();
    const std::string rides = dir + "/rides/";
    for (const char* n : {"pi01-00000-001", "pi01-00000-002", "pi01-00000-003"}) std::ofstream(rides + n + ".sqlite") << "x";
    std::ofstream(rides + "pi01-00000-001.uploaded");
    std::ofstream(rides + "pi01-00000-003.uploaded");
    free_mb = 900;  // min 1024 아래
    lg.tick(0);
    CHECK(!exists(rides + "pi01-00000-001.sqlite") && !exists(rides + "pi01-00000-001.uploaded"), "oldest uploaded deleted");
    CHECK(!exists(rides + "pi01-00000-003.sqlite"), "next uploaded deleted while still low");
    CHECK(exists(rides + "pi01-00000-002.sqlite"), "not uploaded: never deleted");
    CHECK(lg.stats().deleted_uploaded == 2 && lg.stats().samples_paused, "paused after deleting all uploaded");

    lg.on_item(start_item(S), 0x7F, S);
    for (uint64_t i = 0; i < 200; i++) lg.on_item(sample(i, S + i * 10000000ULL), 0x7F, S);
    lg.on_item(end_item(200, 4 * S), 0x7F, 4 * S);
    lg.tick(7 * S);
    const std::string f = rides + "pi01-00001-001.sqlite";
    CHECK(exists(f), "ride still recorded while paused");
    CHECK(q1(f, "SELECT count(*) FROM samples") == "0", "samples paused");
    CHECK(q1(f, "SELECT value FROM meta WHERE key='closed_reason'") == "ride_end", "other tables still written");

    free_mb = 150;  // 200 아래: 새 주행 파일을 만들지 않는다
    lg.on_item(start_item(10 * S), 0x7F, 10 * S);
    CHECK(!lg.ride_open() && lg.stats().rides_refused == 1, "ride refused below 200 MB");
    for (uint64_t i = 0; i < 10; i++) lg.on_item(sample(i, 10 * S + i * 10000000ULL), 0x7F, 10 * S);
    CHECK(!lg.ride_open(), "samples of a refused ride do not open a file");
    msg::LogInfer inf = infer_msg(0, 9.99, 11 * S);
    lg.on_msg(&inf, sizeof inf, 11 * S);
    lg.on_item(end_item(10, 12 * S), 0x7F, 12 * S);
    free_mb = 5000;
    lg.tick(100 * S);
    CHECK(!lg.stats().samples_paused, "resumed when space is back");
    lg.on_item(start_item(200 * S), 0x7F, 200 * S);
    CHECK(lg.ride_open(), "next ride recorded again");
    lg.shutdown(201 * S);
}

int main() {
    test_queue();
    test_normal_ride();
    test_gap_restart_join();
    test_recovery();
    test_storage();
    if (failures) {
        std::printf("%d failure(s)\n", failures);
        return 1;
    }
    std::printf("ride_log: all passed\n");
    return 0;
}
