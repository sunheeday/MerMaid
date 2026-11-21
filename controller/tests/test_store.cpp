// P4 상태 파일 테스트: 저장 → 다시 읽기, 원자적 교체, 파일 이름 검사, Policy의 바뀐 값만 저장, 개인 보정 파일과 식
#include <unistd.h>

#include <cstdio>
#include <cstdlib>
#include <cstring>
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

    const Profile want{12.5, 1.1};
    st.save_profile("phone-a", want);
    CHECK(st.load_profile("phone-a", p) && p.mssq == 12.5 && p.sens == 1.1, "profile round trip: %.17g %.17g", p.mssq,
          p.sens);
    const Profile old = store::parse_profile(R"({"mssq": 20.0, "sens_mssq": 1.3, "sens": 1.5})");
    CHECK(old.mssq == 20.0 && old.sens == 1.3, "old profile file: survey sens_mssq kept, adjusted sens dropped (%.17g)",
          old.sens);

    calib::Calib c;
    CHECK(!st.load_calib("phone-a", c) && c.a == 1 && c.b == 0, "missing calib reads as absent (identity)");
    store::write_atomic(dir + "/calib/phone-a.json", R"({"a": 1.25, "b": -0.5, "run": "v014", "loo_gain": 0.01})");
    CHECK(st.load_calib("phone-a", c) && c.a == 1.25 && c.b == -0.5, "calib read, extra keys ignored (%.17g %.17g)", c.a, c.b);
    for (const char* bad : {R"({"a": 0.2, "b": 0})", R"({"a": 1.0, "b": 9})", R"({"a": 1.0})", R"({"a": "x", "b": 0})"}) {
        bool threw = false;
        try {
            store::parse_calib(bad);
        } catch (const std::exception&) {
            threw = true;
        }
        CHECK(threw, "bad calib refused: %s", bad);
    }
    {
        const Risk p{0.9, 0.7, 0.5, 0.3, 1e-9};
        const Risk same = calib::apply(p, calib::Calib{});
        CHECK(std::memcmp(&same, &p, sizeof p) == 0, "a=1, b=0 leaves P bit-identical");
        const Risk q = calib::apply(p, calib::Calib{1.5, -0.4});
        bool order = true;
        for (size_t k = 1; k < q.size(); k++) order = order && q[k] <= q[k - 1];
        CHECK(order && q[2] < p[2], "calibration keeps p2 >= ... >= p6 and lowers p4 here (%.6f)", q[2]);
        const Risk r = calib::apply(p, calib::Calib{9.0, 0.0});
        CHECK(std::memcmp(&r, &p, sizeof p) == 0, "out-of-range calib is not applied");
    }
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
