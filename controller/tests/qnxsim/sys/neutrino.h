// qnxsim: Pi 없이 P5·P6 프로세스를 시험하려고 QNX 메시지 전달을 Unix 소켓으로 흉내 낸 것 (Mac, Linux).
// 실제 QNX와 같은 점: 이름으로 찾기, MsgSend가 회신까지 멈춤, 시간 제한(ETIMEDOUT), MsgError(errno)
// 다른 점: 펄스 없음, _IO_CONNECT 없음, 우선순위 없음. QNX 보드 빌드에서는 쓰지 않는다
#pragma once
#include <errno.h>
#include <time.h>

#include <cstddef>
#include <cstdint>

#ifndef EOK
#define EOK 0
#endif
#define _NTO_TIMEOUT_SEND (1 << 6)
#define _NTO_TIMEOUT_REPLY (1 << 7)
#define _PULSE_CODE_DISCONNECT (-33)

struct _pulse {
    uint16_t type;
    uint16_t subtype;
    int8_t code;
    uint8_t zero[3];
    int value;
    int scoid;
};
struct sigevent;

extern "C" {
int MsgReceive(int chid, void* msg, size_t bytes, void* info);
int MsgReply(int rcvid, long status, const void* msg, size_t bytes);
int MsgError(int rcvid, int err);
long MsgSend(int coid, const void* smsg, size_t sbytes, void* rmsg, size_t rbytes);
int ConnectDetach(int coid);
int TimerTimeout(clockid_t id, int flags, const struct sigevent* notify, const uint64_t* ntime, uint64_t* otime);
}

// macOS에는 clock_nanosleep이 없다 (P1 재생용). CLOCK_MONOTONIC 절대 시각까지 nanosleep으로 기다린다
#if defined(__APPLE__) && !defined(TIMER_ABSTIME)
#define TIMER_ABSTIME 1
inline int clock_nanosleep(clockid_t id, int flags, const struct timespec* t, struct timespec* rem) {
    if (!(flags & TIMER_ABSTIME)) return nanosleep(t, rem) == 0 ? 0 : errno;
    timespec now;
    clock_gettime(id, &now);
    long long ns = (long long)(t->tv_sec - now.tv_sec) * 1000000000LL + (t->tv_nsec - now.tv_nsec);
    if (ns <= 0) return 0;
    timespec d{(time_t)(ns / 1000000000LL), (long)(ns % 1000000000LL)};
    return nanosleep(&d, nullptr) == 0 ? 0 : errno;
}
#endif
