// P4 상태 파일 테스트: 저장 → 다시 읽기, 원자적 교체, 파일 이름 검사, Policy의 바뀐 값만 저장
#include <unistd.h>

#include <cstdio>
#include <cstdlib>
#include <string>

#include "store.hpp"

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

int main() {
    char tmpl[] = "/tmp/mm_store_XXXXXX";
    const char* base = mkdtemp(tmpl);
    CHECK(base, "mkdtemp");
    const std::string dir = std::string(base) + "/state";

    store::Store st(dir);
    int n = 99;
    Profile p;
    CHECK(!st.load_tolerance("airflow-01", n) && n == 99, "missing file reads as absent");
    CHECK(!st.load_profile("phone-a", p), "missing profile");

    st.save_tolerance("airflow-01", -3);
    CHECK(st.load_tolerance("airflow-01", n) && n == -3, "tolerance round trip: %d", n);
    st.save_tolerance("airflow-01", 2);  // 덮어쓰기
    CHECK(st.load_tolerance("airflow-01", n) && n == 2, "tolerance overwrite: %d", n);
    std::string junk;
    CHECK(!store::read_file(dir + "/tolerance/airflow-01.json.tmp", junk), "no tmp file left behind");

    const Profile want{12.5, 1.1, 1.265};
    st.save_profile("phone-a", want);
    CHECK(st.load_profile("phone-a", p) && p.mssq == 12.5 && p.sens_mssq == 1.1 && p.sens == 1.265,
          "profile round trip: %.17g %.17g %.17g", p.mssq, p.sens_mssq, p.sens);

    CHECK(store::safe_name("phone-a_1.x") && !store::safe_name("../x") && !store::safe_name("a/b") &&
              !store::safe_name("") && !store::safe_name(".hidden"),
          "safe_name");
    bool threw = false;
    try {
        st.save_tolerance("../evil", 1);
    } catch (const std::exception&) {
        threw = true;
    }
    CHECK(threw, "unsafe device name is refused");

    // Policy: 처음 본 프로필과 수동 켜기로 바뀐 허용도만 저장된다
    PolicyConfig cfg = load_policy_config(MM_CONFIG_DIR "/policy.json", MM_CONFIG_DIR "/thresholds.json");
    Policy pol(cfg);
    pol.set_hud_connected(true);
    pol.add_phone("phone-b");
    pol.on_profile("phone-b", 20, 1.3);
    CHECK(st.save_dirty(pol) == 1, "new profile saved");
    CHECK(st.save_dirty(pol) == 0, "nothing dirty the second time");
    pol.on_override(100, "phone-b", "airflow-01", true);  // 꺼진 상태에서 켬: n - 1
    CHECK(st.save_dirty(pol) == 1, "tolerance saved after manual on");
    CHECK(st.load_tolerance("airflow-01", n) && n == -1, "policy started at n = 0, manual on gives -1, got %d", n);

    std::system(("rm -rf " + std::string(base)).c_str());
    if (failures) std::printf("%d failure(s)\n", failures);
    else std::printf("store: all passed\n");
    return failures ? 1 : 0;
}
