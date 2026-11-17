// P5(기기 관리)·P6(통신) 메시지 (QNX MsgSend / MsgReceive). messages.hpp(0x8001~0x8006)에 이어지는 번호.
// 모두 고정 크기 POD. 받는 쪽은 받자마자 복사하고 바로 회신한 뒤 처리한다 (messages.hpp와 같은 규칙).
// 문서: docs/RF_MESSAGES.md. 기기 메시지(JSON)는 protocol/PROTOCOL.md §3.
//
// 보내는 방향 (교착 방지: MsgSend는 회신까지 멈추므로 서로에게 보내는 짝이 없게 한다)
//   P4 → P5 → P6(받기 스레드)     명령이 내려가는 방향
//   P2 → P6(받기 스레드)          MOT 10 Hz
//   P6(UART 스레드) → P5, P4      기기에서 들어온 것
//   P5는 P4에게 먼저 보내지 않는다. P4가 10초마다 보내는 DEV_SYNC의 회신으로 등록 목록을 준다.
//   P6의 받기 스레드는 MsgSend 하지 않는다.
#pragma once
#include <cstdint>

#include "messages.hpp"

namespace mm::msg {

enum RfType : uint16_t {
    DEV_HELLO = 0x8010,   // P6 -> P5, 기기가 HELLO를 보냄
    DEV_BYE = 0x8011,     // P6 -> P5, 브리지가 DISCONNECT를 알림
    DEV_SYNC = 0x8012,    // P4 -> P5, 10초마다 (하트비트 겸). 회신: DevSyncReply
    CTX_SCREEN = 0x8020,  // P6 -> P4, M03 SCR
    CTX_OVR = 0x8021,     // P6 -> P4, M05 OVR
    CTX_PROF = 0x8022,    // P6 -> P4, M06 PROF
    // 0x8023: 비워 둠 (M04 MISC 제거, PROTOCOL.md §3). 번호를 재사용하지 않음
    TIER_CMD = 0x8030,    // P4 -> P5, 기기 하나 ON/OFF (policy.hpp의 Command)
    OUT_INT = 0x8040,     // P5 -> P6, M01 INT를 보낼 것
    OUT_STREAM = 0x8041,  // P5 -> P6, 이 연결에 MOT를 보낼지
    OUT_PROF = 0x8042,    // P5 -> P6, M06 PROF(저장된 프로필)를 보낼 것
    OUT_DROP = 0x8043,    // P5 -> P6, 연결을 끊을 것
    MOTION = 0x8050,      // P2 -> P6, 100 ms마다 (constants.json mot_period_ms)
};

constexpr int kMaxConn = 9;          // 브리지 연결 번호 0~8 (PROTOCOL.md §1)
constexpr uint8_t kConnAll = 0xFF;   // UART "ALL"

constexpr const char* kNameP5 = "mermaid_p5";
constexpr const char* kNameP6 = "mermaid_p6";

enum class Role : uint8_t { App = 0, Hud = 1 };

// ---- P6 -> P5 ----
struct DevHello {
    Hdr h{DEV_HELLO, 0, 0};
    uint8_t conn = 0;
    Role role = Role::App;
    uint16_t ver = 0;
    char dev[kIdLen] = {};  // 폰: 앱 UUID / HUD: 항상 hud-01
};

struct DevBye {
    Hdr h{DEV_BYE, 0, 0};
    uint8_t conn = 0;
};

// ---- P4 -> P5 (회신 있음) ----
struct DevSync {
    Hdr h{DEV_SYNC, 0, 0};
    uint64_t seq = 0;
};

struct DevEntry {
    char dev[kIdLen] = {};
    Role role = Role::App;
    uint8_t screen = 0;  // 마지막 M03 (등록 직후는 0)
    uint8_t att = 0;
};

struct DevSyncReply {
    uint32_t n = 0;
    DevEntry e[kMaxConn];
};

// ---- P6 -> P4 ----
// sender: 보낸 기기의 ID (폰 UUID 또는 hud-01). policy.hpp의 on_screen / on_override / on_profile 인자와 같다
struct CtxScreen {
    Hdr h{CTX_SCREEN, 0, 0};
    char sender[kIdLen] = {};
    uint8_t on = 0;
    uint8_t att = 0;  // HUD는 항상 0
};

struct CtxOvr {
    Hdr h{CTX_OVR, 0, 0};
    char sender[kIdLen] = {};
    char device[kIdLen] = {};  // 대상: airflow-01, display-01, 또는 보낸 폰 자신의 ID
    uint8_t on = 0;
};

struct CtxProf {
    Hdr h{CTX_PROF, 0, 0};
    char phone[kIdLen] = {};
    double mssq = 0;  // MSSQ-Short 원점수 0~54
    double sens = 1;  // 설문 민감도 0.5~2.0 (Policy::on_profile의 sens0)
};

// ---- P4 -> P5 ----
struct TierCmd {
    Hdr h{TIER_CMD, 0, 0};
    char device[kIdLen] = {};  // airflow-01, display-01, 폰 ID
    uint8_t tier = 0;          // 1 송풍, 2 앰비언트, 3 점 찍기
    uint8_t on = 0;
};

// ---- P5 -> P6 ----
struct OutInt {
    Hdr h{OUT_INT, 0, 0};
    uint8_t conn = 0;  // 0~8 또는 kConnAll
    uint8_t tier = 0;
    uint8_t on = 0;
};

struct OutStream {
    Hdr h{OUT_STREAM, 0, 0};
    uint8_t conn = 0;  // 0~8 또는 kConnAll
    uint8_t on = 0;    // 폰: Tier 3 ON 동안 / HUD: Tier 2 ON 동안
};

struct OutProf {
    Hdr h{OUT_PROF, 0, 0};
    uint8_t conn = 0;
    double mssq = 0;
    double sens = 1;
};

struct OutDrop {
    Hdr h{OUT_DROP, 0, 0};
    uint8_t conn = 0;
};

// ---- P2 -> P6 ----
struct Motion {
    Hdr h{MOTION, 0, 0};
    double t = 0;    // 주행 시각 (s)
    double ax = 0;   // Wf 가중 가속도 m/s², x 전후(앞 +)
    double ay = 0;   // y 좌우 (부호는 docs/RF_MESSAGES.md §5, 확정 필요)
    double pax = 0;  // 경로 예측값. 없으면 NaN → JSON에서 생략
    double pay = 0;
};

}  // namespace mm::msg
