// AC-01 모델 동등성 + Q9 측정: Python과 같은 벡터를 lib_lightgbm으로 예측해 파일로 쓴다.
//   verify_model <model_dir> <vectors.bin> <actual_p.bin> [mem]
// vectors.bin: float64 LE, 머리 [1, n, 77] 뒤에 행. actual_p.bin: 머리 [1, n, 5] 뒤에 단조 보정된 P(>=2..6).
// 비교는 Mac에서 ml/verify/model_parity.py check. mem을 주면 측정 뒤 자기 프로세스의 메모리를 출력한다
// (QNX는 getrusage의 ru_maxrss가 0이라 pidin을 부르고, 그 밖에서는 ru_maxrss만).
#include <sys/resource.h>
#include <unistd.h>

#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <stdexcept>
#include <string>
#include <vector>

#include "inference.hpp"
#include "protocol_gen.hpp"

using Clock = std::chrono::steady_clock;

static double us_since(Clock::time_point t0) {
    return std::chrono::duration<double, std::micro>(Clock::now() - t0).count();
}

static double median(std::vector<double> v) {
    std::sort(v.begin(), v.end());
    return v[v.size() / 2];
}

int main(int argc, char** argv) {
    if (argc < 4) {
        std::fprintf(stderr, "usage: verify_model <model_dir> <vectors.bin> <actual_p.bin> [mem]\n");
        return 2;
    }
    try {
        std::ifstream in(argv[2], std::ios::binary);
        if (!in) throw std::runtime_error(std::string("cannot open ") + argv[2]);
        double head[3];
        in.read(reinterpret_cast<char*>(head), sizeof head);
        const int n = static_cast<int>(head[1]);
        if (head[0] != 1 || head[2] != mm::proto::kNumFeatures || n <= 0) throw std::runtime_error("bad vectors.bin header");
        std::vector<double> X(static_cast<size_t>(n) * mm::proto::kNumFeatures);
        in.read(reinterpret_cast<char*>(X.data()), static_cast<std::streamsize>(X.size() * sizeof(double)));
        if (!in) throw std::runtime_error("vectors.bin is shorter than its header says");

        mm::Inference inf(argv[1]);
        rusage ru{};
        getrusage(RUSAGE_SELF, &ru);
        const long rss_after_load = ru.ru_maxrss;

        // 전체를 한 번에 (결과 파일용)
        auto t0 = Clock::now();
        std::vector<mm::Risk> P = inf.predict(X.data(), n);
        const double batch_us = us_since(t0);

        // 한 행씩 (P3가 폰 한 대를 따로 부를 때)
        std::vector<double> one;
        for (int i = 0; i < std::min(n, 500); i++) {
            t0 = Clock::now();
            inf.predict_one(&X[static_cast<size_t>(i) * mm::proto::kNumFeatures]);
            one.push_back(us_since(t0));
        }
        // 한 주기: 폰 9대 = 9행 (PRD Q9 기준)
        std::vector<double> cyc;
        for (int rep = 0; rep < 200; rep++) {
            const int start = (rep * 9) % std::max(1, n - 9);
            t0 = Clock::now();
            inf.predict(&X[static_cast<size_t>(start) * mm::proto::kNumFeatures], 9);
            cyc.push_back(us_since(t0));
        }
        getrusage(RUSAGE_SELF, &ru);

        std::ofstream out(argv[3], std::ios::binary);
        const double oh[3] = {1, static_cast<double>(n), 5};
        out.write(reinterpret_cast<const char*>(oh), sizeof oh);
        for (const auto& p : P) out.write(reinterpret_cast<const char*>(p.data()), sizeof(double) * 5);
        if (!out) throw std::runtime_error(std::string("cannot write ") + argv[3]);

        const auto& it = inf.iterations();
        std::printf("{\"rows\": %d, \"trees\": [%d, %d, %d, %d, %d], \"load_ms\": %.2f, \"batch_us_per_row\": %.2f, "
                    "\"single_row_us_median\": %.1f, \"cycle_9_phones_ms_median\": %.3f, \"cycle_9_phones_ms_max\": %.3f, "
                    "\"ru_maxrss_after_load\": %ld, \"ru_maxrss_end\": %ld, \"pid\": %d}\n",
                    n, it[0], it[1], it[2], it[3], it[4], inf.load_ms(), batch_us / n, median(one),
                    median(cyc) / 1000, *std::max_element(cyc.begin(), cyc.end()) / 1000, rss_after_load, ru.ru_maxrss,
                    static_cast<int>(getpid()));
        std::fflush(stdout);
#ifdef __QNX__
        if (argc > 4 && std::string(argv[4]) == "mem") {
            const std::string cmd = "pidin -p " + std::to_string(getpid()) + " mem";
            if (std::system(cmd.c_str()) != 0) std::fprintf(stderr, "pidin failed\n");
        }
#endif
    } catch (const std::exception& e) {
        std::fprintf(stderr, "verify_model: %s\n", e.what());
        return 1;
    }
    return 0;
}
