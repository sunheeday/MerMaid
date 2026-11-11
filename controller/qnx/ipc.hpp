// QNX 메시지 전달 래퍼: name_attach / name_open, MsgReceive / MsgSend / MsgReply.
// - Server: 연결 요청(_IO_CONNECT)과 끊김 펄스는 안에서 처리하고 사용자 메시지만 돌려준다.
// - Client: 상대가 없거나 죽으면 실패를 돌려주고, 1초에 한 번까지 다시 연결을 시도한다.
// 시작 순서: 각 프로세스는 다음 단계에 연결된 뒤에 자기 이름을 붙인다 (P4 → P3 → P2 → P1).
//   이름이 보이면 받을 준비가 끝난 것이라, P1의 첫 샘플이 P3의 모델 로드를 기다리다 시간 초과되지 않는다.
//   보내기와 회신 기다리기에 시간 제한을 둔다 (상대가 멈춰도 보내는 쪽은 계속 돈다).
#pragma once
#include <errno.h>
#include <pthread.h>
#include <sched.h>
#include <sys/dispatch.h>
#include <sys/iomsg.h>
#include <sys/neutrino.h>
#include <time.h>

#include <cstdint>
#include <cstdio>
#include <cstring>
#include <stdexcept>
#include <string>

namespace mm::ipc {

inline uint64_t now_ns() {
    timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return static_cast<uint64_t>(ts.tv_sec) * 1000000000ULL + static_cast<uint64_t>(ts.tv_nsec);
}

// 이 스레드의 우선순위 (QNX 1~255, 보통 프로세스는 10). 권한이 없으면 경고만
inline void set_priority(int prio, const char* who) {
    sched_param sp{};
    sp.sched_priority = prio;
    const int rc = pthread_setschedparam(pthread_self(), SCHED_RR, &sp);
    if (rc != 0) std::fprintf(stderr, "%s: cannot set priority %d: %s\n", who, prio, std::strerror(rc));
}

class Server {
public:
    explicit Server(const char* name) {
        att_ = name_attach(nullptr, name, 0);
        if (!att_) throw std::runtime_error(std::string("name_attach ") + name + ": " + std::strerror(errno));
    }
    ~Server() { name_detach(att_, 0); }
    Server(const Server&) = delete;
    Server& operator=(const Server&) = delete;

    // 사용자 메시지 하나를 buf에 받는다. 돌려준 rcvid로 reply 해야 보낸 쪽이 풀린다.
    int receive(void* buf, size_t size) {
        for (;;) {
            const int rcvid = MsgReceive(att_->chid, buf, size, nullptr);
            if (rcvid == -1) {
                if (errno == EINTR) continue;
                throw std::runtime_error(std::string("MsgReceive: ") + std::strerror(errno));
            }
            if (rcvid == 0) {  // 펄스
                const auto* p = static_cast<const _pulse*>(buf);
                if (p->code == _PULSE_CODE_DISCONNECT) ConnectDetach(p->scoid);
                continue;
            }
            uint16_t type;
            std::memcpy(&type, buf, sizeof type);
            if (type == _IO_CONNECT) {
                MsgReply(rcvid, EOK, nullptr, 0);
                continue;
            }
            if (type > _IO_BASE && type <= _IO_MAX) {
                MsgError(rcvid, ENOSYS);
                continue;
            }
            return rcvid;
        }
    }
    static void reply(int rcvid, const void* data = nullptr, size_t n = 0) { MsgReply(rcvid, EOK, data, n); }
    static void error(int rcvid, int err) { MsgError(rcvid, err); }

private:
    name_attach_t* att_ = nullptr;
};

class Client {
public:
    Client(std::string name, uint64_t timeout_ns) : name_(std::move(name)), timeout_ns_(timeout_ns) {}
    ~Client() {
        if (coid_ != -1) name_close(coid_);
    }
    Client(const Client&) = delete;
    Client& operator=(const Client&) = delete;

    // timeout_ns = 0이면 만들 때 준 값
    bool send(const void* smsg, size_t sn, void* rmsg = nullptr, size_t rn = 0, uint64_t timeout_ns = 0) {
        if (coid_ == -1 && !open()) return false;
        uint64_t t = timeout_ns ? timeout_ns : timeout_ns_;
        TimerTimeout(CLOCK_MONOTONIC, _NTO_TIMEOUT_SEND | _NTO_TIMEOUT_REPLY, nullptr, &t, nullptr);
        if (MsgSend(coid_, smsg, sn, rmsg, rn) == -1) {
            if (errno != ETIMEDOUT) {  // 상대가 사라짐: 닫고 나중에 다시 연결
                name_close(coid_);
                coid_ = -1;
            }
            return false;
        }
        return true;
    }
    // 처음 연결할 때: 상대가 뜰 때까지 기다린다
    bool wait_open(double seconds) {
        const uint64_t end = now_ns() + static_cast<uint64_t>(seconds * 1e9);
        while (now_ns() < end) {
            coid_ = name_open(name_.c_str(), 0);
            if (coid_ != -1) return true;
            timespec ts{0, 50 * 1000000};
            nanosleep(&ts, nullptr);
        }
        return false;
    }
    bool connected() const { return coid_ != -1; }

private:
    bool open() {
        const uint64_t t = now_ns();
        if (last_try_ && t - last_try_ < 1000000000ULL) return false;
        last_try_ = t;
        coid_ = name_open(name_.c_str(), 0);
        return coid_ != -1;
    }
    std::string name_;
    uint64_t timeout_ns_;
    int coid_ = -1;
    uint64_t last_try_ = 0;
};

}  // namespace mm::ipc
