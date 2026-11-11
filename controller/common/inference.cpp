#include "inference.hpp"

#include <LightGBM/c_api.h>

#include <chrono>
#include <stdexcept>

#include "protocol_gen.hpp"

namespace mm {

static void check(int rc, const std::string& what) {
    if (rc != 0) throw std::runtime_error(what + ": " + LGBM_GetLastError());
}

Inference::Inference(const std::string& model_dir) {
    const auto t0 = std::chrono::steady_clock::now();
    for (int i = 0; i < 5; i++) {
        const std::string path = model_dir + "/model_" + std::to_string(i + 2) + ".txt";
        check(LGBM_BoosterCreateFromModelfile(path.c_str(), &iters_[i], &h_[i]), "load " + path);
        int nf = 0;
        check(LGBM_BoosterGetNumFeature(h_[i], &nf), "num features");
        if (nf != proto::kNumFeatures)
            throw std::runtime_error(path + ": model has " + std::to_string(nf) + " features, controller expects " +
                                     std::to_string(proto::kNumFeatures));
    }
    load_ms_ = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count();
}

Inference::~Inference() {
    for (void* h : h_)
        if (h) LGBM_BoosterFree(h);
}

std::vector<Risk> Inference::predict(const double* X, int n) const {
    std::vector<Risk> out(static_cast<size_t>(n));
    std::vector<double> buf(static_cast<size_t>(n));
    for (int i = 0; i < 5; i++) {
        int64_t len = 0;
        check(LGBM_BoosterPredictForMat(h_[i], X, C_API_DTYPE_FLOAT64, n, proto::kNumFeatures, 1,
                                        C_API_PREDICT_NORMAL, 0, -1, "", &len, buf.data()),
              "predict");
        if (len != n) throw std::runtime_error("predict: unexpected output length");
        for (int r = 0; r < n; r++) out[static_cast<size_t>(r)][static_cast<size_t>(i)] = buf[static_cast<size_t>(r)];
    }
    for (auto& p : out) monotone_fix(p);
    return out;
}

}  // namespace mm
