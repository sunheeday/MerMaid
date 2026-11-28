// P1~P4 사이 메시지 (QNX MsgSend / MsgReceive). 모두 고정 크기 POD.
// 받는 쪽은 받자마자 복사하고 바로 회신한 뒤 처리한다: 보낸 쪽(특히 100 Hz의 P1)이 막히지 않게.
#pragma once
#include <cstdint>
#include <cstring>

#include "features.hpp"
#include "policy.hpp"

namespace mm::msg {

// 시스템 메시지(_IO_*: 0x100~0x1FF)와 겹치지 않는 값
enum Type : uint16_t {
    SAMPLE = 0x8001,     // P1 -> P2, 100 Hz
    RIDE_START = 0x8002, // P1 -> P2, P4
    RIDE_END = 0x8003,   // P1 -> P2, P4
    VEH = 0x8004,        // P2 -> P3, 10초마다 차량 피처 46개
    PROFILES = 0x8005,   // P3 -> P4, 등록된 폰과 프로필 요청 (회신: ProfilesReply)
    RISK = 0x8006,       // P3 -> P4, 폰마다 확률 5칸 (Hdr.subtype = RiskKind)
    TOKENS = 0x8007,     // P3 -> P4, 주행 끝에 문맥 토큰으로 쓸 주기와 라벨 요청 (회신: TokensReply, V3 문맥 모델)
};

// RiskMsg.h.subtype: 확률 5칸의 뜻. P4는 정책 설정(thresholds.json kind)과 다르면 그 주기를 쓰지 않는다
enum RiskKind : uint16_t {
    RISK_MISC5 = 0,      // V1: P(MISC>=2..6), 단조 보정
    RISK_RESPONSE3 = 1,  // V3(문맥 모델): [P(fan), P(ambient), P(dots), NaN, NaN], 앞으로 5분 안에 켤 확률
};

constexpr int kMaxPhones = 9;  // PRD Q9 기준
constexpr int kIdLen = 64;

// 서비스 이름 (/dev/name/local/<이름>)
constexpr const char* kNameP2 = "mermaid_p2";
constexpr const char* kNameP3 = "mermaid_p3";
constexpr const char* kNameP4 = "mermaid_p4";

struct Hdr {
    uint16_t type;
    uint16_t subtype;
    uint32_t pad;
};

struct Sample {
    Hdr h{SAMPLE, 0, 0};
    uint64_t seq = 0;  // 주행 안의 샘플 번호 (0부터). P2가 빠진 샘플을 센다
    Row row{};
};

struct RideEdge {
    Hdr h{RIDE_START, 0, 0};
    uint64_t samples = 0;  // RIDE_END: P1이 보낸 샘플 수
};

struct Veh {
    Hdr h{VEH, 0, 0};
    uint64_t seq = 0;        // 주행 안의 피처 주기 번호
    double t = 0;            // 주행 시각 (s)
    uint64_t t_emit_ns = 0;  // P2가 벡터를 낸 단조 시계 (지연 측정용)
    VehicleVec x{};
};

struct ProfileEntry {
    char id[kIdLen] = {};
    double mssq = 0, sens = 1, ev = 0, tol = 0;
    double cal_a = 1, cal_b = 0;  // 개인 보정 (state/calib, DATA_LOGGING §12). 없으면 1, 0
};

struct ProfilesReq {
    Hdr h{PROFILES, 0, 0};
};

struct ProfilesReply {
    uint32_t n = 0;  // 0이면 P3는 기본 프로필로 한 번 추론
    double tol_display = 0;
    ProfileEntry e[kMaxPhones];
};

struct TokensReq {
    Hdr h{TOKENS, 0, 0};
};

// 주행 하나에서 승객·장치마다 고른 토큰 (ctx_label.hpp). P3는 seq 주기의 요약 e로 [e ‖ 장치 원핫 ‖ 라벨]을 만들어 저장한다
constexpr int kMaxTokens = kMaxPhones * 9;  // 승객마다 장치 3 × (양성 1 + 음성 2)
struct TokensReply {
    uint32_t n = 0;
    struct Entry {
        char id[kIdLen];
        uint64_t seq;
        uint8_t device, label, pad[6];  // device 0 fan, 1 ambient, 2 dots
    } e[kMaxTokens] = {};
};

struct RiskMsg {
    Hdr h{RISK, 0, 0};
    uint64_t seq = 0;
    double t = 0;
    uint64_t t_emit_ns = 0;   // P2가 벡터를 낸 시각
    uint64_t t_infer_ns = 0;  // P3 추론에 걸린 시간
    uint32_t n = 0;
    char id[kMaxPhones][kIdLen] = {};
    Risk p[kMaxPhones] = {};
};

inline void copy_id(char (&dst)[kIdLen], const std::string& s) {
    std::strncpy(dst, s.c_str(), kIdLen - 1);
    dst[kIdLen - 1] = 0;
}

}  // namespace mm::msg
