// P1 → P7 공유 메모리 링. 설명은 sample_ring.hpp
#include "sample_ring.hpp"

#include <fcntl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

#include <cerrno>
#include <cstring>
#include <new>

namespace mm::ring {

namespace {
constexpr size_t kHeaderBytes = (sizeof(Header) + 63) / 64 * 64;

std::string sys_err(const char* what) { return std::string(what) + ": " + std::strerror(errno); }
}  // namespace

size_t region_bytes(uint32_t cap) { return kHeaderBytes + static_cast<size_t>(cap) * sizeof(Slot); }

// ---------------- Writer ----------------

Writer::~Writer() { close(); }

void Writer::close() {
    if (hdr_) munmap(hdr_, bytes_);
    hdr_ = nullptr;
    slots_ = nullptr;
    bytes_ = 0;
}

bool Writer::open(const std::string& name, uint32_t cap, uint32_t col_mask, std::string& err) {
    close();
    if (cap == 0) {
        err = "ring: cap must be > 0";
        return false;
    }
    const size_t bytes = region_bytes(cap);
    int fd = shm_open(name.c_str(), O_RDWR | O_CREAT, 0644);
    if (fd < 0) {
        err = sys_err("shm_open");
        return false;
    }
    struct stat sb;
    if (fstat(fd, &sb) != 0) {
        err = sys_err("fstat");
        ::close(fd);
        return false;
    }
    uint64_t old_epoch = 0;
    // 이미 있는 링을 그대로 쓸 수 있는지는 헤더로 판단한다 (macOS는 st_size를 페이지 단위로 올려서 보고한다)
    bool reuse = false;
    if (sb.st_size != 0 && static_cast<size_t>(sb.st_size) >= kHeaderBytes) {
        void* p = mmap(nullptr, kHeaderBytes, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
        if (p != MAP_FAILED) {
            auto* h = static_cast<Header*>(p);
            if (h->magic.load() == kMagic) {
                old_epoch = h->epoch.load();
                reuse = h->version == kVersion && h->cap == cap && static_cast<size_t>(sb.st_size) >= bytes;
            }
            if (!reuse) h->magic.store(0);  // 옛 링을 읽던 쪽에 무효를 알린다
            munmap(p, kHeaderBytes);
        }
    }
    if (sb.st_size != 0 && !reuse) {
        // 크기나 버전이 다른 옛 링: 지운 뒤 새로 만든다 (macOS는 이미 크기가 정해진 공유 메모리의 ftruncate를 거부한다)
        ::close(fd);
        shm_unlink(name.c_str());
        fd = shm_open(name.c_str(), O_RDWR | O_CREAT, 0644);
        if (fd < 0) {
            err = sys_err("shm_open (recreate)");
            return false;
        }
        sb.st_size = 0;
    }
    const bool fresh = sb.st_size == 0;
    if (fresh && ftruncate(fd, static_cast<off_t>(bytes)) != 0) {
        err = sys_err("ftruncate");
        ::close(fd);
        return false;
    }
    void* p = mmap(nullptr, bytes, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    ::close(fd);
    if (p == MAP_FAILED) {
        err = sys_err("mmap");
        return false;
    }
    auto* base = static_cast<char*>(p);
    Header* h = fresh ? new (base) Header{} : reinterpret_cast<Header*>(base);
    if (!fresh && h->magic.load() == kMagic) old_epoch = h->epoch.load();
    h->magic.store(0, std::memory_order_release);
    h->version = kVersion;
    h->cap = cap;
    h->col_mask = col_mask;
    h->head.store(0, std::memory_order_relaxed);
    Slot* s = reinterpret_cast<Slot*>(base + kHeaderBytes);
    for (uint32_t i = 0; i < cap; i++) {
        if (fresh) new (&s[i]) Slot{};
        s[i].n.store(kWriting, std::memory_order_relaxed);
    }
    h->epoch.store(old_epoch + 1, std::memory_order_release);
    h->magic.store(kMagic, std::memory_order_release);
    hdr_ = h;
    slots_ = s;
    bytes_ = bytes;
    return true;
}

void Writer::push(uint32_t kind, uint32_t flags, uint64_t seq, uint64_t mono_ns, const Row& row) {
    if (!hdr_) return;
    const uint64_t idx = hdr_->head.load(std::memory_order_relaxed);
    Slot& s = slots_[idx % hdr_->cap];
    s.n.store(kWriting, std::memory_order_relaxed);
    std::atomic_thread_fence(std::memory_order_release);
    s.mono_ns = mono_ns;
    s.kind = kind;
    s.flags = flags;
    s.seq = seq;
    std::memcpy(s.row.data(), row.data(), sizeof(double) * row.size());
    s.n.store(idx, std::memory_order_release);
    hdr_->head.store(idx + 1, std::memory_order_release);
}

uint64_t Writer::epoch() const { return hdr_ ? hdr_->epoch.load() : 0; }

// ---------------- Reader ----------------

Reader::~Reader() { close(); }

void Reader::close() {
    if (hdr_) munmap(hdr_, bytes_);
    hdr_ = nullptr;
    slots_ = nullptr;
    bytes_ = 0;
}

bool Reader::open(const std::string& name, std::string& err) {
    close();
    const int fd = shm_open(name.c_str(), O_RDONLY, 0);
    if (fd < 0) {
        err = sys_err("shm_open");
        return false;
    }
    struct stat sb;
    if (fstat(fd, &sb) != 0 || static_cast<size_t>(sb.st_size) < kHeaderBytes) {
        err = "ring: not initialised yet";
        ::close(fd);
        return false;
    }
    void* hp = mmap(nullptr, kHeaderBytes, PROT_READ, MAP_SHARED, fd, 0);
    if (hp == MAP_FAILED) {
        err = sys_err("mmap header");
        ::close(fd);
        return false;
    }
    const auto* h = static_cast<const Header*>(hp);
    const bool ok = h->magic.load(std::memory_order_acquire) == kMagic && h->version == kVersion &&
                    static_cast<size_t>(sb.st_size) >= region_bytes(h->cap);
    const uint32_t cap = h->cap;
    munmap(hp, kHeaderBytes);
    if (!ok) {
        err = "ring: bad header (writer not ready or different version)";
        ::close(fd);
        return false;
    }
    const size_t bytes = region_bytes(cap);
    void* p = mmap(nullptr, bytes, PROT_READ, MAP_SHARED, fd, 0);
    ::close(fd);
    if (p == MAP_FAILED) {
        err = sys_err("mmap");
        return false;
    }
    hdr_ = static_cast<Header*>(p);
    slots_ = reinterpret_cast<Slot*>(static_cast<char*>(p) + kHeaderBytes);
    bytes_ = bytes;
    epoch_ = hdr_->epoch.load(std::memory_order_acquire);
    const uint64_t head = hdr_->head.load(std::memory_order_acquire);
    tail_ = head > cap ? head - cap : 0;  // 남아 있는 가장 오래된 칸부터
    return true;
}

uint32_t Reader::col_mask() const { return hdr_ ? hdr_->col_mask : 0; }

void Reader::skip_to_head() {
    if (hdr_) tail_ = hdr_->head.load(std::memory_order_acquire);
}

size_t Reader::poll(std::vector<Item>& out, size_t max) {
    if (!hdr_) return 0;
    if (hdr_->magic.load(std::memory_order_acquire) != kMagic) {  // 쓰는 쪽이 링을 다시 만드는 중이거나 지웠다
        close();
        return 0;
    }
    const uint64_t e = hdr_->epoch.load(std::memory_order_acquire);
    if (e != epoch_) {  // P1이 다시 시작: 처음부터
        epoch_ = e;
        tail_ = 0;
        st_.epoch_changes++;
    }
    const uint32_t cap = hdr_->cap;
    const uint64_t h = hdr_->head.load(std::memory_order_acquire);
    if (h < tail_) tail_ = 0;  // epoch가 보이기 전에 head가 0으로 돌아간 경우
    if (h - tail_ > cap) {
        st_.lost += h - tail_ - cap;
        tail_ = h - cap;
    }
    size_t got = 0;
    while (tail_ < h && got < max) {
        const Slot& s = slots_[tail_ % cap];
        if (s.n.load(std::memory_order_acquire) != tail_) {  // 이미 다음 바퀴가 덮어씀 (또는 쓰는 중)
            st_.lost++;
            tail_++;
            continue;
        }
        Item it;
        it.n = tail_;
        it.mono_ns = s.mono_ns;
        it.kind = s.kind;
        it.flags = s.flags;
        it.seq = s.seq;
        std::memcpy(it.row.data(), s.row.data(), sizeof(double) * it.row.size());
        std::atomic_thread_fence(std::memory_order_acquire);
        if (s.n.load(std::memory_order_relaxed) != tail_) {  // 복사하는 사이 덮어씀
            st_.torn++;
            tail_++;
            continue;
        }
        out.push_back(it);
        got++;
        tail_++;
    }
    return got;
}

}  // namespace mm::ring
