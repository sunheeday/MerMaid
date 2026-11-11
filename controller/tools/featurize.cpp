// trip.bin → features.bin. 학습(Mac)과 P2(Pi)가 같은 FeatureExtractor를 쓰게 하는 명령행 도구.
//   featurize <trip.bin> <features.bin> [--oman-k K]
// 출력: float64 [feature_version, n_columns] 뒤에 10초마다 한 행 [t, 차량 피처 71개]
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <vector>

#include "features.hpp"

using namespace mm;

int main(int argc, char** argv) {
    if (argc < 3) {
        std::fprintf(stderr, "usage: %s trip.bin features.bin [--oman-k K]\n", argv[0]);
        return 2;
    }
    double k = proto::kOmanK0;
    for (int i = 3; i + 1 < argc; i++)
        if (!std::strcmp(argv[i], "--oman-k")) k = std::atof(argv[i + 1]);

    std::ifstream in(argv[1], std::ios::binary);
    double hdr[3];
    if (!in.read(reinterpret_cast<char*>(hdr), sizeof hdr)) { std::fprintf(stderr, "cannot read %s\n", argv[1]); return 2; }
    if (int(hdr[0]) != proto::kTripVersion || int(hdr[1]) != proto::kTripCols || hdr[2] != proto::kSampleRateHz) {
        std::fprintf(stderr, "trip header mismatch: version %g cols %g rate %g (want %d %d %g)\n",
                     hdr[0], hdr[1], hdr[2], proto::kTripVersion, proto::kTripCols, proto::kSampleRateHz);
        return 2;
    }

    std::ofstream out(argv[2], std::ios::binary);
    const double ohdr[2] = {double(proto::kFeatureVersion), double(1 + proto::kNumVehicle)};
    out.write(reinterpret_cast<const char*>(ohdr), sizeof ohdr);

    FeatureExtractor fx(k);
    Row r;
    long rows = 0, emitted = 0;
    while (in.read(reinterpret_cast<char*>(r.data()), sizeof(double) * r.size())) {
        rows++;
        if (fx.push(r)) {
            const double t = fx.time();
            out.write(reinterpret_cast<const char*>(&t), sizeof t);
            out.write(reinterpret_cast<const char*>(fx.vehicle().data()), sizeof(double) * proto::kNumVehicle);
            emitted++;
        }
    }
    std::fprintf(stderr, "%ld samples -> %ld feature rows\n", rows, emitted);
    return 0;
}
