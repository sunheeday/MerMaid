// V3 문맥 모델 패리티 + 시간: export_ctx.py가 쓴 check.bin을 C++ CtxModel로 다시 계산해 PyTorch 값과 비교한다.
//   ctx_check <model_dir> [check.bin]       (기본 <model_dir>/check.bin)
// 통과 기준: 요약 e, 공통 확률 p0, 문맥 확률 p 모두 최대 차이 < 1e-5. 실패면 종료 코드 1.
// 시간: 10초 주기 한 번(summarize + predict)과 탑승 시작 한 번(history, 토큰 k개)의 중앙값·최대.
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <fstream>
#include <stdexcept>
#include <string>
#include <vector>

#include "ctx_model.hpp"

using Clock = std::chrono::steady_clock;

namespace {

template <typename T>
std::vector<T> rd(std::ifstream& in, size_t n) {
    std::vector<T> v(n);
    in.read(reinterpret_cast<char*>(v.data()), static_cast<std::streamsize>(n * sizeof(T)));
    if (static_cast<size_t>(in.gcount()) != n * sizeof(T)) throw std::runtime_error("check.bin too short");
    return v;
}

double us(Clock::time_point t0) { return std::chrono::duration<double, std::micro>(Clock::now() - t0).count(); }

struct Stat {
    std::vector<double> v;
    double med() { std::sort(v.begin(), v.end()); return v.empty() ? 0 : v[v.size() / 2]; }
    double max() const { return v.empty() ? 0 : *std::max_element(v.begin(), v.end()); }
};

}  // namespace

int main(int argc, char** argv) {
    if (argc < 2) {
        std::fprintf(stderr, "usage: ctx_check <model_dir> [check.bin]\n");
        return 2;
    }
    try {
        const std::string dir = argv[1];
        const auto t_load = Clock::now();
        mm::CtxModel m(dir);
        const double load_ms = us(t_load) / 1000;
        std::ifstream in(argc > 2 ? argv[2] : dir + "/check.bin", std::ios::binary);
        if (!in) throw std::runtime_error("cannot open check.bin");
        const auto head = rd<int32_t>(in, 5);
        const int n = head[0], nf = head[1], de = head[2], k = head[3], ncase = head[4];
        const auto cases = rd<int32_t>(in, static_cast<size_t>(ncase));
        if (nf != m.n_inputs() || de != m.summary_dim()) throw std::runtime_error("check.bin does not match the model");
        const int td = m.token_dim(), H = m.hidden_dim(), NH = mm::CtxModel::kHeads;
        const auto X = rd<double>(in, static_cast<size_t>(n) * nf);
        const auto E = rd<float>(in, static_cast<size_t>(n) * de);
        const auto P0 = rd<float>(in, static_cast<size_t>(n) * NH);

        double de_max = 0, p0_max = 0, p_max = 0;
        std::vector<std::vector<float>> e(n, std::vector<float>(de));
        for (int i = 0; i < n; i++) {
            m.summarize_inputs(&X[static_cast<size_t>(i) * nf], e[i].data());
            float p0[3];
            m.common(e[i].data(), p0);
            for (int j = 0; j < de; j++) de_max = std::max(de_max, (double)std::fabs(e[i][j] - E[static_cast<size_t>(i) * de + j]));
            for (int j = 0; j < NH; j++) p0_max = std::max(p0_max, (double)std::fabs(p0[j] - P0[static_cast<size_t>(i) * NH + j]));
        }
        std::vector<float> last_ctx;
        for (int c = 0; c < ncase; c++) {
            const auto C = rd<float>(in, static_cast<size_t>(n) * k * td);
            const auto pad = rd<float>(in, static_cast<size_t>(n) * k);
            const auto P = rd<float>(in, static_cast<size_t>(n) * NH);
            double cmax = 0;
            for (int i = 0; i < n; i++) {
                int cnt = 0;  // 유효 토큰은 앞쪽에 몰려 있다 (context6.make_context)
                while (cnt < k && pad[static_cast<size_t>(i) * k + cnt] == 0.f) cnt++;
                std::vector<float> h(H), p(NH);
                // PyTorch는 저장된 E로 질의한다. 요약 차이를 따로 재려고 여기서도 같은 E를 쓴다
                m.history(&C[static_cast<size_t>(i) * k * td], cnt, h.data());
                m.predict(&E[static_cast<size_t>(i) * de], h.data(), p.data());
                for (int j = 0; j < NH; j++) cmax = std::max(cmax, (double)std::fabs(p[j] - P[static_cast<size_t>(i) * NH + j]));
            }
            std::printf("case %3d tokens: max |p - torch| = %.2e\n", cases[c], cmax);
            p_max = std::max(p_max, cmax);
            if (c == ncase - 1) last_ctx.assign(C.begin(), C.begin() + static_cast<size_t>(k) * td);
        }

        // 시간
        Stat cyc, hist;
        std::vector<float> h(H, 0.f), p(NH), ee(de);
        for (int r = 0; r < 2000; r++) {
            const auto t0 = Clock::now();
            m.summarize_inputs(&X[static_cast<size_t>(r % n) * nf], ee.data());
            m.predict(ee.data(), h.data(), p.data());
            cyc.v.push_back(us(t0));
        }
        for (int r = 0; r < 200; r++) {
            const auto t0 = Clock::now();
            m.history(last_ctx.data(), k, h.data());
            hist.v.push_back(us(t0));
        }
        const bool ok = de_max < 1e-5 && p0_max < 1e-5 && p_max < 1e-5;
        std::printf("summary e  max diff %.2e\ncommon p0  max diff %.2e\ncontext p  max diff %.2e\n", de_max, p0_max, p_max);
        std::printf("load %.1f ms | cycle (summarize+predict) median %.1f us max %.1f us | ride start (history, %d tokens) median %.1f us max %.1f us\n",
                    load_ms, cyc.med(), cyc.max(), k, hist.med(), hist.max());
        std::printf("%s\n", ok ? "PASS" : "FAIL");
        return ok ? 0 : 1;
    } catch (const std::exception& ex) {
        std::fprintf(stderr, "ctx_check: %s\n", ex.what());
        return 1;
    }
}
