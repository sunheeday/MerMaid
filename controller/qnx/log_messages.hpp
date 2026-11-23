// P3·P4·P5·P6 → P7 로그 메시지 (docs/controller/DATA_LOGGING.md §6-2). 모두 고정 크기 POD.
// 보내는 쪽 규칙: ipc::Client(kNameP7, kLogTimeoutNs). 실패하면 자기 통계 log_fail만 올리고 계속한다.
// P7은 받자마자 복사하고 바로 회신하며, 아무에게도 MsgSend 하지 않는다 (교착 고리 없음).
#pragma once
#include <cstdint>
#include <cstring>
#include <string>

#include "messages.hpp"

namespace mm::msg {

enum LogType : uint16_t {
    LOG_INFER = 0x8060,     // P3, 10초마다 RISK를 P4에 보낸 뒤
    LOG_DECISION = 0x8061,  // P4, 10초마다 명령을 다 보낸 뒤
    LOG_CTX = 0x8062,       // P6·P5, 기기 사건 (CtxKind 참고: 폰과 오간 것 전부. HB·MOT는 빼고)
    LOG_SYM = 0x8063,       // P6, 앱의 멀미 점수 (M08 SYM, 0~10 정수)
    LOG_GPS = 0x8064,       // P6, 앱의 GPS (M09, 규칙 통과한 것) → gps 표 (#18)
};

constexpr const char* kNameP7 = "mermaid_p7";
constexpr uint64_t kLogTimeoutNs = 2 * 1000000ULL;  // 2 ms
constexpr int kMaxDevices = kMaxPhones + 3;          // 폰 9 + airflow-01 + display-01 + 여유 1

struct LogInfer {
    Hdr h{LOG_INFER, 0, 0};
    uint64_t seq = 0;      // RISK와 같은 주기 번호
    double t = 0;          // 주행 시각 (s)
    uint64_t mono_ns = 0;  // 보낸 시각
    char model[16] = {};   // 모델 버전 (ml/runs 폴더 이름, 예: "v007")
    uint32_t n = 0;        // 행 수 (폰 수, 없으면 "default" 1행)
    char id[kMaxPhones][kIdLen] = {};
    double x[kMaxPhones][proto::kNumFeatures] = {};  // P3에 들어간 52칸 그대로 (NaN 포함)
    Risk p[kMaxPhones] = {};                         // p2..p6, 개인 보정 전
    double cal_a[kMaxPhones] = {};                   // 적용한 개인 보정 (§12, 없으면 1, 0)
    double cal_b[kMaxPhones] = {};
};

struct LogDecision {
    Hdr h{LOG_DECISION, 0, 0};
    uint64_t seq = 0;
    double t = 0;
    uint64_t mono_ns = 0;
    uint32_t n = 0;
    struct Dev {
        char device[kIdLen];
        uint8_t on, tier, changed, pad;
    } d[kMaxDevices] = {};
};

// ctx 표의 kind 칸에 이름 그대로 들어간다. 0~4는 기기가 보낸 것(P6이 규칙을 통과시킨 뒤), 5~10은 P6이 본 링크 사건과
// Pi → 기기로 실제 나간 것, 11~13은 P5의 안전 OFF. 번호는 바꾸지 않고 뒤에만 붙인다 (docs/controller/DATA_LOGGING.md §6-2)
enum class CtxKind : uint8_t {
    HELLO = 0,       // 기기 등록 (P5에 전달된 뒤 한 번)
    BYE = 1,         // P6이 등록을 지움 (P5에 DEV_BYE)
    SCR = 2,
    OVR = 3,
    PROF = 4,        // 앱 → C
    CONNECT = 5,     // 브리지: BLE 연결됨 (HELLO 전)
    DISCONNECT = 6,  // 브리지: BLE 끊김
    INT = 7,         // C → 기기로 나간 INT (연결마다 한 줄)
    PROF_OUT = 8,    // C → 앱으로 나간 PROF 회신
    DROP = 9,        // P6이 브리지에 끊으라고 함 + 이유
    REJECT = 10,     // 기기가 보냈지만 버린 메시지 + 이유와 원문 앞부분
    SAFE_OFF = 11,   // P5: DEV_SYNC가 끊겨 전부 OFF
    SAFE_CLEAR = 12, // P5: DEV_SYNC가 돌아와 안전 OFF 해제
    HELD_ON = 13,    // P5: 안전 OFF 중에 잡아 둔 ON을 해제 뒤 적용
    RISK_OUT = 14,   // C → 앱으로 나간 M10 RISK (#19)
};

struct LogCtx {
    Hdr h{LOG_CTX, 0, 0};
    uint64_t mono_ns = 0;
    char sender[kIdLen] = {};  // 기기 ID (Pi → 기기는 받는 기기). 모르면 "conn<N>", 전체는 "all", P5 사건은 "p5", 줄이 깨졌으면 "uart"
    CtxKind kind = CtxKind::HELLO;
    char value[192] = {};      // 사건 내용 JSON (잘리면 끝이 "...")
};

struct LogSym {
    Hdr h{LOG_SYM, 0, 0};
    uint64_t mono_ns = 0;
    char phone[kIdLen] = {};
    int32_t misc = 0;  // 0~10
};

struct LogGps {
    Hdr h{LOG_GPS, 0, 0};
    uint64_t mono_ns = 0;  // P6이 줄을 받은 때
    char phone[kIdLen] = {};
    double spd = 0;        // m/s
    double brg = 0;        // 도, 없으면 NaN
    double acc = 0;        // m
    double alt = 0;        // m, 없으면 NaN
};

inline const char* ctx_kind_name(CtxKind k) {
    switch (k) {
        case CtxKind::HELLO: return "HELLO";
        case CtxKind::BYE: return "BYE";
        case CtxKind::SCR: return "SCR";
        case CtxKind::OVR: return "OVR";
        case CtxKind::PROF: return "PROF";
        case CtxKind::CONNECT: return "CONNECT";
        case CtxKind::DISCONNECT: return "DISCONNECT";
        case CtxKind::INT: return "INT";
        case CtxKind::PROF_OUT: return "PROF_OUT";
        case CtxKind::DROP: return "DROP";
        case CtxKind::REJECT: return "REJECT";
        case CtxKind::SAFE_OFF: return "SAFE_OFF";
        case CtxKind::SAFE_CLEAR: return "SAFE_CLEAR";
        case CtxKind::HELD_ON: return "HELD_ON";
        case CtxKind::RISK_OUT: return "RISK_OUT";
    }
    return "?";
}

// 고정 길이 칸에 문자열 넣기. 넘치면 잘라서 끝을 "..."로
template <size_t N>
inline void copy_text(char (&dst)[N], const std::string& s) {
    if (s.size() < N) {
        std::memcpy(dst, s.c_str(), s.size() + 1);
        return;
    }
    std::memcpy(dst, s.data(), N - 4);
    std::memcpy(dst + N - 4, "...", 4);
}

// --models 경로에서 모델 버전: ".../runs/v007/models" → "v007", 아니면 마지막 폴더 이름
inline std::string model_version_from_path(std::string p) {
    while (p.size() > 1 && p.back() == '/') p.pop_back();
    auto base = [](const std::string& s) {
        const size_t k = s.rfind('/');
        return k == std::string::npos ? s : s.substr(k + 1);
    };
    std::string b = base(p);
    if (b == "models") {
        const size_t k = p.rfind('/');
        if (k != std::string::npos && k > 0) b = base(p.substr(0, k));
    }
    return b;
}

constexpr size_t kMaxLogBytes = sizeof(LogInfer);  // 가장 큰 메시지 (P7 큐 칸 크기)
static_assert(sizeof(LogDecision) <= kMaxLogBytes && sizeof(LogCtx) <= kMaxLogBytes && sizeof(LogSym) <= kMaxLogBytes &&
                  sizeof(LogGps) <= kMaxLogBytes,
              "LogInfer must be the largest log message");

}  // namespace mm::msg
