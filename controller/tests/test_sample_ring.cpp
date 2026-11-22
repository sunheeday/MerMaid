// P1 → P7 공유 메모리 링 테스트: 순서, 넘침, P1 재시작(epoch), 크기 변경, 늦게 연 읽는 쪽, 동시 쓰기·읽기.
#include <sys/mman.h>
#include <unistd.h>

#include <atomic>
#include <cstdio>
#include <string>
#include <thread>
#include <vector>

#include "sample_ring.hpp"

using namespace mm;
using namespace mm::ring;

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

static Row row_for(uint64_t i) {
    Row r{};
    r[0] = static_cast<double>(i);
    r[1] = static_cast<double>(i) * 2.0;
    r[31] = -static_cast<double>(i);
    return r;
}

static bool row_ok(const Item& it) {
    return it.row[0] == static_cast<double>(it.seq) && it.row[1] == 2.0 * it.seq && it.row[31] == -1.0 * it.seq;
}

int main() {
    const std::string name = "/mmring_test_" + std::to_string(getpid());
    std::string err;

    {
        Reader early;
        CHECK(!early.open(name, err), "reader before writer: not found");
    }

    Writer w;
    CHECK(w.open(name, 100, 0x7F, err), "writer open: %s", err.c_str());
    CHECK(w.epoch() == 1, "first epoch 1");
    Reader r;
    CHECK(r.open(name, err), "reader open: %s", err.c_str());
    CHECK(r.col_mask() == 0x7F, "col mask shared");

    // 순서와 내용
    w.push(RIDE_START, 0, 0, 5, Row{});
    for (uint64_t i = 0; i < 10; i++) w.push(SAMPLE, i == 3 ? 1u : 0u, i, 1000 + i, row_for(i));
    std::vector<Item> got;
    CHECK(r.poll(got) == 11, "11 items");
    if (got.size() != 11) {
        std::printf("cannot continue\n");
        return 1;
    }
    CHECK(got[0].kind == RIDE_START && got[0].mono_ns == 5, "ride start first");
    bool ok = true;
    for (uint64_t i = 0; i < 10; i++) ok = ok && got[i + 1].seq == i && got[i + 1].mono_ns == 1000 + i && row_ok(got[i + 1]);
    CHECK(ok, "samples in order with rows");
    CHECK(got[4].flags == 1, "flags kept");
    got.clear();
    CHECK(r.poll(got) == 0, "nothing new");

    // max
    for (uint64_t i = 10; i < 20; i++) w.push(SAMPLE, 0, i, 0, row_for(i));
    CHECK(r.poll(got, 4) == 4 && got.back().seq == 13, "poll max 4");
    got.clear();
    CHECK(r.poll(got) == 6 && got.front().seq == 14, "rest later");
    got.clear();

    // 넘침: 읽지 않는 사이 cap + 50
    const uint64_t base = 20;
    for (uint64_t i = 0; i < 150; i++) w.push(SAMPLE, 0, base + i, 0, row_for(base + i));
    CHECK(r.poll(got) == 100, "only cap items left (%zu)", got.size());
    CHECK(r.stats().lost == 50, "lost 50 (%llu)", (unsigned long long)r.stats().lost);
    CHECK(!got.empty() && got.front().seq == base + 50 && got.back().seq == base + 149, "oldest kept is head - cap");
    got.clear();

    // 늦게 연 읽는 쪽은 남은 가장 오래된 칸부터
    {
        Reader late;
        CHECK(late.open(name, err), "late reader");
        std::vector<Item> g2;
        CHECK(late.poll(g2) == 100 && g2.front().seq == base + 50, "late reader gets the last cap items");
    }

    // P1 재시작: 같은 크기로 다시 열면 epoch + 1, head 0
    Writer w2;
    CHECK(w2.open(name, 100, 0x7F, err), "writer reopen: %s", err.c_str());
    CHECK(w2.epoch() == 2, "epoch 2");
    w.close();
    for (uint64_t i = 0; i < 3; i++) w2.push(SAMPLE, 0, i, 0, row_for(i));
    CHECK(r.poll(got) == 3 && got[0].n == 0 && got[0].seq == 0, "reader restarts from 0 after epoch change");
    CHECK(r.stats().epoch_changes == 1, "epoch change counted");
    got.clear();

    // 크기 변경: 옛 읽는 쪽은 닫히고, 다시 열면 새 링
    Writer w3;
    CHECK(w3.open(name, 64, 0xFFFFFFFFu, err), "writer reopen with new cap: %s", err.c_str());
    CHECK(w3.epoch() == 3, "epoch continues across recreate (%llu)", (unsigned long long)w3.epoch());
    CHECK(r.poll(got) == 0 && !r.is_open(), "old mapping invalidated");
    CHECK(r.open(name, err) && r.col_mask() == 0xFFFFFFFFu, "reopen new ring");
    w2.close();

    // 동시: 쓰는 쪽은 쉬지 않고, 읽는 쪽은 계속 읽는다. 받은 것은 순서대로, 내용이 온전해야 한다
    {
        const uint64_t total = 300000;
        std::atomic<bool> done{false};
        std::thread prod([&] {
            for (uint64_t i = 0; i < total; i++) w3.push(SAMPLE, 0, i, i, row_for(i));
            done = true;
        });
        uint64_t received = 0, last = 0, bad = 0, order = 0;
        bool first = true;
        std::vector<Item> buf;
        for (;;) {
            const bool fin = done.load();
            buf.clear();
            r.poll(buf);
            for (const Item& it : buf) {
                if (!row_ok(it) || it.mono_ns != it.seq) bad++;
                if (!first && it.seq <= last) order++;
                last = it.seq;
                first = false;
                received++;
            }
            if (fin && buf.empty()) break;
        }
        prod.join();
        const ReadStats& st = r.stats();
        CHECK(bad == 0, "no torn rows delivered (%llu)", (unsigned long long)bad);
        CHECK(order == 0, "strictly increasing (%llu)", (unsigned long long)order);
        CHECK(received + st.lost + st.torn >= total, "every item received or counted lost (%llu + %llu + %llu)",
              (unsigned long long)received, (unsigned long long)st.lost, (unsigned long long)st.torn);
        std::printf("concurrent: received %llu, lost %llu, torn %llu of %llu\n", (unsigned long long)received,
                    (unsigned long long)st.lost, (unsigned long long)st.torn, (unsigned long long)total);
    }

    w3.close();
    r.close();
    shm_unlink(name.c_str());
    if (failures) {
        std::printf("%d failure(s)\n", failures);
        return 1;
    }
    std::printf("sample_ring: all passed\n");
    return 0;
}
