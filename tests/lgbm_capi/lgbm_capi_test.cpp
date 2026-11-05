// Pi에서 실행: lib_lightgbm으로 예측해 Mac의 Python 결과와 비교
// 입력과 정답은 CSV가 아니라 원본 비트(.bin)로 읽는다. QNX strtod는 값을 1~2 ULP 다르게 읽기 때문이다.
#include <LightGBM/c_api.h>
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <fstream>
#include <stdexcept>
#include <string>
#include <vector>

static std::vector<double> read_bin(const std::string& path) {
    std::ifstream in(path, std::ios::binary | std::ios::ate);
    if (!in) throw std::runtime_error("파일을 열 수 없음: " + path);
    const std::streamsize bytes = in.tellg();
    std::vector<double> v((size_t)bytes / sizeof(double));
    in.seekg(0);
    in.read(reinterpret_cast<char*>(v.data()), bytes);
    return v;
}

static void ok(int rc) {
    if (rc != 0) throw std::runtime_error(LGBM_GetLastError());
}

static double predict(BoosterHandle h, const double* x, int ncol, int type) {
    int64_t len = 0;
    double out = 0.0;
    ok(LGBM_BoosterPredictForMatSingleRow(h, x, C_API_DTYPE_FLOAT64, ncol, 1,
                                          type, 0, -1, "", &len, &out));
    return out;
}

int main(int argc, char** argv) {
    if (argc < 2) { std::printf("사용법: %s fixtures/model_nan [dump.csv]\n", argv[0]); return 2; }
    const std::string base = argv[1];
    try {
        BoosterHandle h = nullptr;
        int iterations = 0, ncol = 0;
        auto t0 = std::chrono::steady_clock::now();
        ok(LGBM_BoosterCreateFromModelfile((base + ".txt").c_str(), &iterations, &h));
        auto t1 = std::chrono::steady_clock::now();
        ok(LGBM_BoosterGetNumFeature(h, &ncol));

        const auto inputs = read_bin(base + "_inputs.bin");
        const auto expected = read_bin(base + "_expected.bin");
        const size_t rows = inputs.size() / ncol;
        if (expected.size() != rows * 2) throw std::runtime_error("행 수가 다름");

        // 두 번째 인자를 주면 행별 예측값을 CSV로 저장 (원인 분석용)
        std::FILE* dump = argc > 2 ? std::fopen(argv[2], "w") : nullptr;
        double max_raw = 0.0, max_prob = 0.0;
        size_t bad = 0;
        for (size_t i = 0; i < rows; i++) {
            const double* x = &inputs[i * ncol];
            const double r = predict(h, x, ncol, C_API_PREDICT_RAW_SCORE);
            const double p = predict(h, x, ncol, C_API_PREDICT_NORMAL);
            max_raw = std::max(max_raw, std::fabs(r - expected[i * 2]));
            max_prob = std::max(max_prob, std::fabs(p - expected[i * 2 + 1]));
            if (std::fabs(p - expected[i * 2 + 1]) >= 1e-6) bad++;
            if (dump) std::fprintf(dump, "%.17g,%.17g\n", r, p);
        }
        if (dump) std::fclose(dump);

        auto t2 = std::chrono::steady_clock::now();
        volatile double sink = 0.0;
        for (size_t i = 0; i < rows; i++) sink = sink + predict(h, &inputs[i * ncol], ncol, C_API_PREDICT_NORMAL);
        auto t3 = std::chrono::steady_clock::now();

        const bool pass = max_prob < 1e-6;
        std::printf("%s: trees=%d rows=%zu\n", base.c_str(), iterations, rows);
        std::printf("  모델 로드: %.1f ms\n", std::chrono::duration<double, std::milli>(t1 - t0).count());
        std::printf("  max|raw 차이|=%.3e  max|p 차이|=%.3e  어긋난 행=%zu\n", max_raw, max_prob, bad);
        std::printf("  행당 예측 시간: %.2f us\n",
                    std::chrono::duration<double, std::micro>(t3 - t2).count() / rows);
        std::printf("RESULT: %s\n", pass ? "PASS" : "FAIL");
        LGBM_BoosterFree(h);
        return pass ? 0 : 1;
    } catch (const std::exception& e) {
        std::printf("ERROR: %s\n", e.what());
        return 2;
    }
}
