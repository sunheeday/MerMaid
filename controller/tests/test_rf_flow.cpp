// P6 코어(comm)와 P5 코어(devmgr)를 이어 붙인 흐름 시험. QNX 메시지 전달 자리는 함수 호출로 대신한다.
// docs/RF_MESSAGES.md와 인수인계 §6-0-1의 예시: 폰 연결 → 등록 → P4 명령 → 폰 화면 → 수동 끔 → 끊김
#include <cmath>
#include <cstdio>
#include <string>
#include <vector>

#include "comm.hpp"
#include "devmgr.hpp"

using namespace mm;

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

// Pi 안을 흉내: P6 → P5 는 바로 전달, P6 → P4 는 받은 것으로 모아 둠, UART로 나간 줄도 모아 둠
struct Pi {
    comm::Comm p6{nullptr, 0};
    devmgr::DevMgr p5{nullptr};
    std::vector<std::string> uart;            // ESP32로 나간 줄
    std::vector<msg::CtxOvr> to_p4_ovr;       // P4가 받은 OVR
    std::vector<msg::CtxScreen> to_p4_scr;

    void run_p5(const devmgr::Outs& outs) {
        for (const auto& o : outs) {
            comm::Actions a;
            if (const auto* i = std::get_if<msg::OutInt>(&o)) a = p6.on_out_int(*i);
            else if (const auto* s = std::get_if<msg::OutStream>(&o)) a = p6.on_out_stream(*s);
            else if (const auto* p = std::get_if<msg::OutProf>(&o)) a = p6.on_out_prof(*p);
            else if (const auto* d = std::get_if<msg::OutDrop>(&o)) a = p6.on_out_drop(*d);
            run_p6(a);
        }
    }
    void run_p6(const comm::Actions& acts) {
        for (const auto& a : acts) {
            if (const auto* u = std::get_if<comm::UartLine>(&a)) uart.push_back(u->text);
            else if (const auto* h = std::get_if<msg::DevHello>(&a)) {
                p6.hello_delivered(h->conn, h->dev);
                run_p5(p5.on_hello(*h));
            } else if (const auto* b = std::get_if<msg::DevBye>(&a)) run_p5(p5.on_bye(*b));
            else if (const auto* o = std::get_if<msg::CtxOvr>(&a)) to_p4_ovr.push_back(*o);
            else if (const auto* s = std::get_if<msg::CtxScreen>(&a)) to_p4_scr.push_back(*s);
        }
    }
    void from_bridge(const std::string& line, int64_t t) { run_p6(p6.on_uart_line(line, t)); }
    void from_p4(const std::string& device, int tier, bool on) {
        msg::TierCmd m;
        msg::copy_id(m.device, device);
        m.tier = static_cast<uint8_t>(tier);
        m.on = on;
        run_p5(p5.on_tier_cmd(m));
    }
    std::vector<std::string> take() {
        std::vector<std::string> v;
        v.swap(uart);
        return v;
    }
};

static std::string join(const std::vector<std::string>& v) {
    std::string s;
    for (const auto& x : v) s += (s.empty() ? "" : " / ") + x;
    return s;
}
#define EXPECT_UART(pi, ...)                                                                   \
    do {                                                                                       \
        const std::vector<std::string> want_ = {__VA_ARGS__};                                  \
        const std::vector<std::string> got_ = (pi).take();                                     \
        CHECK(got_ == want_, "\n  got  %s\n  want %s", join(got_).c_str(), join(want_).c_str()); \
    } while (0)

int main() {
    Pi pi;
    const std::string A = "5f3a9c1e";

    // 1-2. 폰 연결, HELLO → P5 명부, 현재 상태(모두 꺼짐)가 폰으로
    pi.from_bridge("0|CONNECT", 0);
    pi.from_bridge("0|{\"t\":\"HELLO\",\"role\":\"app\",\"dev\":\"" + A + "\",\"v\":1}", 100);
    EXPECT_UART(pi, "0|{\"t\":\"INT\",\"tier\":1,\"on\":false}", "0|{\"t\":\"INT\",\"tier\":2,\"on\":false}",
                "0|{\"t\":\"INT\",\"tier\":3,\"on\":false}");

    // 3. P4가 DEV_SYNC로 명부를 받는다
    msg::DevSyncReply r = pi.p5.on_sync(msg::DevSync{}, 1000);
    CHECK(r.n == 1 && A == r.e[0].dev, "P4 sees the phone");

    // 4. 위험 상승 → Tier 3 ON → 폰에 INT, 그다음 MOT가 이 폰으로만
    pi.from_p4(A, 3, true);
    EXPECT_UART(pi, "0|{\"t\":\"INT\",\"tier\":3,\"on\":true}");
    msg::Motion m;
    m.ax = 0.5;
    m.ay = -0.2;
    m.pax = m.pay = std::nan("");
    pi.run_p6(pi.p6.on_motion(m));
    EXPECT_UART(pi, "0|{\"t\":\"MOT\",\"ax\":0.500,\"ay\":-0.200}");

    // 폰 화면 상태 → P4
    pi.from_bridge("0|{\"t\":\"SCR\",\"on\":true,\"att\":true}", 2000);
    CHECK(pi.to_p4_scr.size() == 1 && A == pi.to_p4_scr[0].sender && pi.to_p4_scr[0].att == 1, "SCR reaches P4");

    // 5. 사용자가 점을 끔 → OVR가 P4로 → P4가 OFF 명령 → 폰 점 사라지고 MOT 멈춤
    pi.from_bridge("0|{\"t\":\"OVR\",\"dev\":\"" + A + "\",\"on\":false}", 3000);
    CHECK(pi.to_p4_ovr.size() == 1 && A == pi.to_p4_ovr[0].device && pi.to_p4_ovr[0].on == 0, "OVR reaches P4");
    pi.from_p4(A, 3, false);
    EXPECT_UART(pi, "0|{\"t\":\"INT\",\"tier\":3,\"on\":false}");
    pi.run_p6(pi.p6.on_motion(m));
    EXPECT_UART(pi);

    // HUD 합류 → Tier 2 ON: HUD에 INT + MOT, 폰에는 INT(표시)만
    pi.from_bridge("1|CONNECT", 4000);
    pi.from_bridge("1|{\"t\":\"HELLO\",\"role\":\"hud\",\"dev\":\"hud-01\",\"v\":1}", 4100);
    pi.take();
    pi.from_p4("display-01", 2, true);
    EXPECT_UART(pi, "0|{\"t\":\"INT\",\"tier\":2,\"on\":true}", "1|{\"t\":\"INT\",\"tier\":2,\"on\":true}");
    pi.run_p6(pi.p6.on_motion(m));
    EXPECT_UART(pi, "1|{\"t\":\"MOT\",\"ax\":0.500,\"ay\":-0.200}");

    // 6. 폰이 나감 → 명부에서 빠짐. 다시 붙으면 지금 상태(Tier 2 ON)를 받는다
    pi.from_bridge("0|DISCONNECT", 5000);
    r = pi.p5.on_sync(msg::DevSync{}, 6000);
    CHECK(r.n == 1 && std::string(r.e[0].dev) == "hud-01", "phone removed");
    pi.from_bridge("2|CONNECT", 7000);
    pi.from_bridge("2|{\"t\":\"HELLO\",\"role\":\"app\",\"dev\":\"" + A + "\",\"v\":1}", 7100);
    EXPECT_UART(pi, "2|{\"t\":\"INT\",\"tier\":1,\"on\":false}", "2|{\"t\":\"INT\",\"tier\":2,\"on\":true}",
                "2|{\"t\":\"INT\",\"tier\":3,\"on\":false}");

    if (failures) {
        std::printf("%d failure(s)\n", failures);
        return 1;
    }
    std::printf("rf_flow: all passed\n");
    return 0;
}
