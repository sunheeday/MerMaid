// P1 LIVE용 MPU-6050: 레지스터 설정, FIFO 읽기, 단위 변환, 장착 회전, 자이로 바이어스 보정, 빠진 샘플 처리.
// 표준 C++17만 사용. I2C는 Bus 뒤에 숨겨서 Mac 시험(fake_mpu.hpp)과 QNX(qnx/mpu_i2c.hpp)가 같은 코드를 쓴다.
// 설계: docs/controller/DATA_LOGGING.md §4
//
// 한 박자(10 ms)마다 Acquirer::step(now)을 부르면 그 사이 FIFO에 쌓인 샘플이 Row로 나온다.
// 샘플 시각은 센서 클럭 기준 t = seq / 100 (Pi가 늦게 깨어나도 FIFO에 쌓여 있어서 간격은 센서가 정한다).
#pragma once
#include <array>
#include <cstdint>
#include <functional>
#include <string>
#include <vector>

#include "features.hpp"

namespace mm::imu {

// ---- 레지스터 (MPU-6000/6050 Register Map rev 4.2) ----
namespace reg {
constexpr uint8_t SMPLRT_DIV = 0x19;
constexpr uint8_t CONFIG = 0x1A;
constexpr uint8_t GYRO_CONFIG = 0x1B;
constexpr uint8_t ACCEL_CONFIG = 0x1C;
constexpr uint8_t FIFO_EN = 0x23;
constexpr uint8_t INT_ENABLE = 0x38;
constexpr uint8_t INT_STATUS = 0x3A;
constexpr uint8_t USER_CTRL = 0x6A;
constexpr uint8_t PWR_MGMT_1 = 0x6B;
constexpr uint8_t FIFO_COUNTH = 0x72;  // FIFO_COUNTL = 0x73 (한 번에 2바이트 읽기)
constexpr uint8_t FIFO_R_W = 0x74;
constexpr uint8_t WHO_AM_I = 0x75;
}  // namespace reg

constexpr uint8_t kWhoAmI = 0x68;
constexpr uint8_t kWhoAmIClone = 0x72;  // 보드에 달린 호환 칩. 레지스터 맵과 FIFO 동작이 같음 (bringup/README.md, 2026-11-21 LIVE 주행)
constexpr uint8_t kPwrReset = 0x80;         // PWR_MGMT_1: 전체 리셋
constexpr uint8_t kPwrClkGyroX = 0x01;      // PWR_MGMT_1: 슬립 해제, 클럭 = 자이로 X PLL
constexpr uint8_t kSmplrtDiv100Hz = 9;      // 1 kHz / (1 + 9) = 100 Hz (DLPF를 켜면 내부 1 kHz)
constexpr uint8_t kDlpf44Hz = 0x03;         // CONFIG: DLPF 약 44 Hz
constexpr uint8_t kGyro250dps = 0x00;       // ±250 °/s, 131 LSB/(°/s)
constexpr uint8_t kAccel4g = 0x08;          // ±4 g, 8192 LSB/g (±2 g면 z축이 방지턱에서 잘림)
constexpr uint8_t kFifoAccelGyro = 0x78;    // FIFO_EN: 자이로 XYZ + 가속도 (온도 제외) = 샘플당 12바이트
constexpr uint8_t kUserFifoEnable = 0x40;   // USER_CTRL
constexpr uint8_t kUserFifoReset = 0x04;    // USER_CTRL
constexpr uint8_t kIntFifoOverflow = 0x10;  // INT_STATUS (읽으면 지워짐)

constexpr double kAccelLsbPerG = 8192.0;
constexpr double kGyroLsbPerDps = 131.0;
constexpr double kG = 9.80665;
constexpr double kPi = 3.14159265358979323846;
constexpr int kFifoBytes = 1024;
constexpr int kSampleBytes = 12;      // ax ay az gx gy gz, big-endian int16
constexpr int kMaxChunkSamples = 8;   // I2C 한 번에 최대 96바이트
constexpr double kRateHz = 100.0;

// 보정·복구 규칙 (설계 §4-4, §4-5)
constexpr int kCalibSamples = 200;                     // 정지 판정 창 2초
constexpr double kStillAccSd = 0.05;                   // m/s², 가속도 크기의 표준편차
constexpr double kStillGyroSdDps = 0.5;                // °/s, 자이로 각 축 표준편차
constexpr uint64_t kCalibTimeoutNs = 30000000000ULL;   // 30초 안에 정지를 못 얻으면 저장된 바이어스
constexpr double kAutoLevelMaxDeg = 10.0;               // 자동 수평 맞춤: 이보다 기울어 있으면 적용하지 않음 (장착 이상이나 경사로)
constexpr int kHoldMax = 50;                           // 이만큼까지 빠진 샘플은 마지막 값을 반복해 채움 (0.5초)
constexpr uint64_t kErrorRestartNs = 1000000000ULL;    // I2C 오류가 1초 계속되면 재초기화
constexpr uint64_t kInitRetryNs = 1000000000ULL;       // 초기화 실패 뒤 다시 시도 간격
constexpr int kMaxInitFails = 10;                      // 연속 실패하면 fatal

using Vec3 = std::array<double, 3>;
using Mat3 = std::array<Vec3, 3>;

// I2C 레지스터 접근. 실패하면 false (QNX: devctl 오류)
class Bus {
public:
    virtual ~Bus() = default;
    virtual bool write(uint8_t reg, uint8_t val) = 0;
    virtual bool read(uint8_t reg, uint8_t* buf, size_t n) = 0;
};

// FIFO 한 샘플 (센서 축, 단위 없는 정수)
struct Raw {
    int16_t a[3];
    int16_t g[3];
};
Raw decode(const uint8_t* p);  // 12바이트

// 장착 방향: 차량 축(x 앞, y 왼쪽, z 위) = R · 센서 축
struct Mount {
    Mat3 R{{{1, 0, 0}, {0, 1, 0}, {0, 0, 1}}};
};
Mount parse_mount(const std::string& json_text);
// 자동 수평 맞춤: 정지 때 차량 축 평균 가속도 m을 +z로 보내는 최단 회전 (로드리게스, 회전축이 수평이라 요는 그대로).
// m이 +z에서 max_deg보다 벗어나 있으면(또는 크기가 0이면) false
bool level_rotation(const Vec3& m_car, double max_deg, Mat3& L, double& tilt_deg);
Mat3 matmul(const Mat3& A, const Mat3& B);  // {"version":1,"R":[[..],[..],[..]]}. 회전 행렬이 아니면 예외
Mount load_mount(const std::string& path);

// 차량 축, a: m/s² (중력 포함, 수평 정지 시 z = +9.81), g: rad/s
struct Sample {
    Vec3 a;
    Vec3 g;
};
Sample convert(const Raw& r, const Mount& m, const Vec3& gyro_bias_dps);

// trip_format.json 한 행: t = seq / 100, ax..gz, 나머지 25칸은 NaN (주행 전체 NaN 규칙)
void fill_row(Row& row, uint64_t seq, const Sample& s);

// 정지 판정 (센서 축 그대로, 바이어스 보정 전)
struct Still {
    bool still = false;
    double acc_mag_mean = 0, acc_mag_sd = 0;  // m/s²
    Vec3 gyro_mean_dps{}, gyro_sd_dps{};
    Vec3 acc_mean{};  // m/s², 센서 축 (자동 수평 맞춤용)
};
Still check_still(const Raw* w, size_t n);

// 리셋 → 설정 → FIFO 켜기. WHO_AM_I가 다르거나 쓰기가 실패하면 false와 이유
bool init(Bus& bus, const std::function<void(int ms)>& sleep_ms, std::string& err);
bool fifo_reset(Bus& bus);

// 한 박자 FIFO 읽기
struct Tick {
    bool ok = true;          // I2C 오류 없음
    bool overflow = false;   // FIFO 넘침: 샘플을 잃었고 몇 개인지 모름
    bool misaligned = false; // 바이트 수가 12의 배수가 아님: FIFO를 비워야 함
    size_t discarded = 0;    // misaligned일 때 버리는 샘플 수 (올림)
    std::vector<Raw> samples;
};
Tick read_fifo(Bus& bus);

// P1이 내보낼 것 (순서대로 링과 P2에)
struct Out {
    enum Kind : uint32_t { SAMPLE = 0, RIDE_START = 1, RIDE_END = 2 } kind = SAMPLE;
    uint32_t flags = 0;
    uint64_t seq = 0;  // SAMPLE: 주행 안 번호. RIDE_END: 보낸 샘플 수
    Row row{};
};
constexpr uint32_t FLAG_HOLD = 1;  // 빠진 샘플을 마지막 값으로 채움

struct Stats {
    uint64_t samples = 0, hold = 0;
    uint64_t overflow = 0, misaligned = 0, i2c_error_ticks = 0;
    uint64_t restarts = 0, init_fails = 0, rides = 0;
    bool bias_uncalibrated = false;  // 정지 판정도 저장된 값도 없어 바이어스 0으로 주행
    // 자동 수평 맞춤 (Config::auto_level): -1 꺼짐, 0 시도했지만 적용 안 함(정지 못 얻음·너무 기움), 1 적용
    int level = -1;
    double level_tilt_deg = 0;       // 정지 평균이 +z에서 벗어난 각도 (적용 여부와 상관없이, 판정했으면)
    double level_pitch_deg = 0, level_roll_deg = 0;  // 적용 전 차량 축 기울기: atan2(ax, az), atan2(ay, az)
    double rate_hz = 0;              // 현재 주행의 실제 샘플률 (샘플 수 / 단조 시계)
};

// 수집 상태 기계: 초기화 → 보정 → 주행 → (오류) → 초기화 …
class Acquirer {
public:
    struct Config {
        Mount mount;
        bool has_saved_bias = false;
        Vec3 saved_bias_dps{};
        // 첫 시작 보정의 정지 2초로 장착 기울기를 재서 mount.R 앞에 곱한다 (P1 프로세스마다 한 번).
        // 정지를 못 얻거나 max_deg보다 기울어 있으면 mount.R 그대로. 기본 꺼짐 (docs/controller/DATA_LOGGING.md §4-4)
        bool auto_level = false;
        double auto_level_max_deg = kAutoLevelMaxDeg;
    };
    Acquirer(Bus& bus, Config cfg, std::function<void(int ms)> sleep_ms);

    // 10 ms마다 부른다 (now_ns = CLOCK_MONOTONIC)
    std::vector<Out> step(uint64_t now_ns);
    // 끝낼 때 (SIGINT/SIGTERM). 주행 중이면 RIDE_END
    std::vector<Out> stop();

    bool fatal() const { return state_ == FATAL; }
    bool riding() const { return state_ == RUN; }
    const std::string& last_error() const { return err_; }
    const Stats& stats() const { return st_; }
    // 보정으로 새 바이어스를 얻었으면 한 번 true (P1이 state/imu_bias.json에 저장)
    bool take_new_bias(Vec3& dps);
    // 지금 쓰는 장착 행렬 (자동 수평 맞춤을 적용했으면 L · mount.R)
    const Mat3& mount_R() const { return mount_.R; }

private:
    enum State { INIT, CALIB, RUN, FATAL };
    void begin_calib(uint64_t now);
    void start_ride(uint64_t now, std::vector<Out>& out);
    void restart(std::vector<Out>& out);
    void emit(const Raw& r, uint32_t flags, std::vector<Out>& out);
    void try_level(const Still* s);  // 첫 보정 때 한 번. s = nullptr이면 정지를 못 얻음

    Bus& bus_;
    Config cfg_;
    std::function<void(int)> sleep_ms_;
    State state_ = INIT;
    std::string err_;
    Stats st_;
    int init_fails_ = 0;
    uint64_t next_init_ = 0;
    uint64_t calib_start_ = 0, last_ok_ = 0, ride_start_ = 0;
    std::vector<Raw> window_;
    Vec3 bias_{};
    bool new_bias_ = false;
    Mount mount_;               // 실제로 쓰는 장착 행렬 (cfg_.mount 또는 자동 수평 맞춤 적용)
    bool level_tried_ = false;
    uint64_t seq_ = 0;
    Raw last_{};
    bool has_last_ = false;
};

}  // namespace mm::imu
