// qnxsim 구현: 서버 이름 = Unix 소켓, 연결 하나 = coid, 요청 하나 = rcvid.
// 프레임: 요청 [u32 길이][바이트], 회신 [i32 errno(0=정상)][u32 길이][바이트]
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <sys/dispatch.h>
#include <sys/neutrino.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>

#include <atomic>
#include <condition_variable>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <map>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

namespace {

struct IgnoreSigpipe {
    IgnoreSigpipe() { signal(SIGPIPE, SIG_IGN); }  // 상대가 죽어도 write가 프로세스를 죽이지 않게
} g_ignore_sigpipe;

std::string dir() {
    const char* d = std::getenv("QNXSIM_DIR");
    return d && *d ? d : "/tmp/qnxsim";
}
std::string path_of(const char* name) { return dir() + "/" + name; }

bool read_full(int fd, void* p, size_t n) {
    auto* c = static_cast<char*>(p);
    while (n) {
        const ssize_t r = read(fd, c, n);
        if (r < 0 && errno == EINTR) continue;
        if (r <= 0) return false;
        c += r;
        n -= static_cast<size_t>(r);
    }
    return true;
}
bool write_full(int fd, const void* p, size_t n) {
    const auto* c = static_cast<const char*>(p);
    while (n) {
        const ssize_t r = write(fd, c, n);
        if (r < 0 && errno == EINTR) continue;
        if (r <= 0) return false;
        c += r;
        n -= static_cast<size_t>(r);
    }
    return true;
}

struct Pending {
    std::vector<char> data;
    bool done = false;
    int32_t err = 0;
    std::vector<char> reply;
};

struct Channel {
    std::mutex mu;
    std::condition_variable cv;
    std::deque<int> queue;
    std::map<int, std::shared_ptr<Pending>> pending;
    int listen_fd = -1;
    std::string path;
};

std::mutex g_mu;
std::map<int, Channel*> g_chan;
int g_next_chid = 1;
std::atomic<int> g_next_rcvid{1};

// 클라이언트 쪽: coid → 이름 (시간 초과 뒤 다시 연결할 때)
std::map<int, std::string> g_coid_name;
thread_local uint64_t t_timeout_ns = 0;

void serve(Channel* ch, int fd) {
    for (;;) {
        uint32_t len;
        if (!read_full(fd, &len, 4)) break;
        auto p = std::make_shared<Pending>();
        p->data.resize(len);
        if (len && !read_full(fd, p->data.data(), len)) break;
        const int rcvid = g_next_rcvid++;
        {
            std::unique_lock<std::mutex> lk(ch->mu);
            ch->pending[rcvid] = p;
            ch->queue.push_back(rcvid);
            ch->cv.notify_all();
            ch->cv.wait(lk, [&] { return p->done; });
            ch->pending.erase(rcvid);
        }
        const uint32_t rl = static_cast<uint32_t>(p->reply.size());
        if (!write_full(fd, &p->err, 4) || !write_full(fd, &rl, 4) || (rl && !write_full(fd, p->reply.data(), rl)))
            break;
    }
    close(fd);
}

Channel* find_by_rcvid(int rcvid, std::shared_ptr<Pending>& out) {
    std::lock_guard<std::mutex> g(g_mu);
    for (auto& kv : g_chan) {
        std::lock_guard<std::mutex> lk(kv.second->mu);
        auto it = kv.second->pending.find(rcvid);
        if (it != kv.second->pending.end()) {
            out = it->second;
            return kv.second;
        }
    }
    return nullptr;
}

int finish(int rcvid, int32_t err, const void* msg, size_t bytes) {
    std::shared_ptr<Pending> p;
    Channel* ch = find_by_rcvid(rcvid, p);
    if (!ch) {
        errno = ESRCH;
        return -1;
    }
    std::lock_guard<std::mutex> lk(ch->mu);
    p->err = err;
    if (msg && bytes) p->reply.assign(static_cast<const char*>(msg), static_cast<const char*>(msg) + bytes);
    p->done = true;
    ch->cv.notify_all();
    return 0;
}

int connect_to(const std::string& path) {
    const int fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0) return -1;
    sockaddr_un a{};
    a.sun_family = AF_UNIX;
    std::strncpy(a.sun_path, path.c_str(), sizeof a.sun_path - 1);
    if (connect(fd, reinterpret_cast<sockaddr*>(&a), sizeof a) < 0) {
        close(fd);
        errno = ENOENT;
        return -1;
    }
    return fd;
}

}  // namespace

extern "C" {

name_attach_t* name_attach(void*, const char* name, unsigned) {
    mkdir(dir().c_str(), 0700);
    auto* ch = new Channel;
    ch->path = path_of(name);
    unlink(ch->path.c_str());
    ch->listen_fd = socket(AF_UNIX, SOCK_STREAM, 0);
    sockaddr_un a{};
    a.sun_family = AF_UNIX;
    std::strncpy(a.sun_path, ch->path.c_str(), sizeof a.sun_path - 1);
    if (ch->listen_fd < 0 || bind(ch->listen_fd, reinterpret_cast<sockaddr*>(&a), sizeof a) < 0 ||
        listen(ch->listen_fd, 16) < 0) {
        delete ch;
        return nullptr;
    }
    int chid;
    {
        std::lock_guard<std::mutex> g(g_mu);
        chid = g_next_chid++;
        g_chan[chid] = ch;
    }
    std::thread([ch] {
        for (;;) {
            const int fd = accept(ch->listen_fd, nullptr, nullptr);
            if (fd < 0) {
                if (errno == EINTR) continue;
                return;
            }
            std::thread(serve, ch, fd).detach();
        }
    }).detach();
    return new name_attach_t{0, chid, 0};
}

int name_detach(name_attach_t* att, unsigned) {
    std::lock_guard<std::mutex> g(g_mu);
    auto it = g_chan.find(att->chid);
    if (it != g_chan.end()) {
        close(it->second->listen_fd);
        unlink(it->second->path.c_str());
    }
    delete att;
    return 0;
}

int MsgReceive(int chid, void* msg, size_t bytes, void*) {
    Channel* ch;
    {
        std::lock_guard<std::mutex> g(g_mu);
        auto it = g_chan.find(chid);
        if (it == g_chan.end()) {
            errno = EINVAL;
            return -1;
        }
        ch = it->second;
    }
    std::unique_lock<std::mutex> lk(ch->mu);
    ch->cv.wait(lk, [&] { return !ch->queue.empty(); });
    const int rcvid = ch->queue.front();
    ch->queue.pop_front();
    const auto& d = ch->pending[rcvid]->data;
    std::memset(msg, 0, bytes);
    std::memcpy(msg, d.data(), d.size() < bytes ? d.size() : bytes);
    return rcvid;
}

int MsgReply(int rcvid, long, const void* msg, size_t bytes) { return finish(rcvid, 0, msg, bytes); }
int MsgError(int rcvid, int err) { return finish(rcvid, err, nullptr, 0); }

int name_open(const char* name, int) {
    const int fd = connect_to(path_of(name));
    if (fd < 0) return -1;
    std::lock_guard<std::mutex> g(g_mu);
    g_coid_name[fd] = name;
    return fd;
}

int name_close(int coid) {
    {
        std::lock_guard<std::mutex> g(g_mu);
        g_coid_name.erase(coid);
    }
    return close(coid);
}

int ConnectDetach(int) { return 0; }

int TimerTimeout(clockid_t, int, const struct sigevent*, const uint64_t* ntime, uint64_t*) {
    t_timeout_ns = ntime ? *ntime : 0;
    return 0;
}

long MsgSend(int coid, const void* smsg, size_t sbytes, void* rmsg, size_t rbytes) {
    const uint64_t timeout_ns = t_timeout_ns;
    t_timeout_ns = 0;  // QNX처럼 다음 커널 호출 한 번에만 적용
    const uint32_t len = static_cast<uint32_t>(sbytes);
    if (!write_full(coid, &len, 4) || !write_full(coid, smsg, sbytes)) {
        errno = ESRCH;
        return -1;
    }
    if (timeout_ns) {
        pollfd p{coid, POLLIN, 0};
        const int r = poll(&p, 1, static_cast<int>(timeout_ns / 1000000ULL));
        if (r == 0) {
            // 시간 초과: 늦게 올 회신이 다음 요청과 섞이지 않게 같은 coid 번호로 새로 연결한다
            std::string name;
            {
                std::lock_guard<std::mutex> g(g_mu);
                name = g_coid_name[coid];
            }
            const int fd = connect_to(path_of(name.c_str()));
            if (fd >= 0) {
                dup2(fd, coid);
                close(fd);
            } else {
                shutdown(coid, SHUT_RDWR);
            }
            errno = ETIMEDOUT;
            return -1;
        }
    }
    int32_t err;
    uint32_t rl;
    if (!read_full(coid, &err, 4) || !read_full(coid, &rl, 4)) {
        errno = ESRCH;
        return -1;
    }
    std::vector<char> buf(rl);
    if (rl && !read_full(coid, buf.data(), rl)) {
        errno = ESRCH;
        return -1;
    }
    if (err) {
        errno = err;
        return -1;
    }
    if (rmsg && rbytes) std::memcpy(rmsg, buf.data(), rl < rbytes ? rl : rbytes);
    return 0;
}

}  // extern "C"
