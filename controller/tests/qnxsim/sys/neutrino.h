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
