// 생성 파일: python3 protocol/gen_cpp.py. 직접 고치지 말 것.
#pragma once

namespace mm::proto {

// ---- feature_order.json ----
constexpr int kFeatureVersion = 3;
constexpr int kNumFeatures = 77;
constexpr int kNumVehicle = 71;
enum Feat : int {
    F_RMS_X_10S = 0,
    F_RMS_X_1M = 1,
    F_RMS_X_5M = 2,
    F_RMS_Y_10S = 3,
    F_RMS_Y_1M = 4,
    F_RMS_Y_5M = 5,
    F_RMS_Z_10S = 6,
    F_RMS_Z_1M = 7,
    F_RMS_Z_5M = 8,
    F_DECEL_RMS_X_1M = 9,
    F_DECEL_RMS_X_5M = 10,
    F_ACCEL_RMS_X_1M = 11,
    F_ACCEL_RMS_X_5M = 12,
    F_JERK_RMS_X_10S = 13,
    F_JERK_RMS_X_1M = 14,
    F_JERK_RMS_Y_10S = 15,
    F_JERK_RMS_Y_1M = 16,
    F_YAW_RMS_10S = 17,
    F_YAW_RMS_1M = 18,
    F_HARD_ACCEL_COUNT_1M = 19,
    F_HARD_ACCEL_COUNT_5M = 20,
    F_HARD_BRAKE_COUNT_1M = 21,
    F_HARD_BRAKE_COUNT_5M = 22,
    F_HARD_TURN_COUNT_1M = 23,
    F_HARD_TURN_COUNT_5M = 24,
    F_EVENT_IRREGULARITY_5M = 25,
    F_PITCH_MEAN_1M = 26,
    F_MSDV = 27,
    F_MSDV_INC_5M = 28,
    F_OMAN_SCORE = 29,
    F_OMAN_DELTA_2M = 30,
    F_SENSITIZATION = 31,
    F_SVC_CONFLICT = 32,
    F_SPEED_MEAN_10S = 33,
    F_SPEED_MEAN_1M = 34,
    F_SPEED_MEAN_5M = 35,
    F_SPEED_STD_1M = 36,
    F_SPEED_STD_5M = 37,
    F_STOP_RATIO_5M = 38,
    F_SPEED_MAX_5M = 39,
    F_ACCEL_PEDAL_RMS_1M = 40,
    F_BRAKE_PEDAL_RMS_1M = 41,
    F_PEDAL_RATE_RMS_1M = 42,
    F_DRIVE_MODE = 43,
    F_CRUISE_ON = 44,
    F_REGEN_DECEL_RMS_1M = 45,
    F_REGEN_DECEL_RMS_5M = 46,
    F_REGEN_LEVEL = 47,
    F_ONE_PEDAL = 48,
    F_REGEN_SHARE_5M = 49,
    F_SOC = 50,
    F_BATT_POWER_MEAN_1M = 51,
    F_BATT_POWER_STD_1M = 52,
    F_MOTOR_TORQUE_RATE_RMS_1M = 53,
    F_DIST_NEXT_TURN = 54,
    F_ANGLE_NEXT_TURN = 55,
    F_PRED_AX_10S = 56,
    F_PRED_AY_10S = 57,
    F_CURVATURE_AHEAD = 58,
    F_CABIN_TEMP = 59,
    F_SET_TEMP = 60,
    F_FAN_LEVEL = 61,
    F_AC_ON = 62,
    F_SEAT_ROW = 63,
    F_SEATBACK_ANGLE = 64,
    F_WINDOW_OPEN = 65,
    F_NOISE_LEVEL = 66,
    F_NOISE_LOWFREQ_LEVEL = 67,
    F_RIDE_TIME = 68,
    F_REEXPOSURE = 69,
    F_TIME_SINCE_REST = 70,
    F_TOL_DISPLAY = 71,
    F_TOL_PHONE = 72,
    F_MSSQ = 73,
    F_SENSITIVITY = 74,
    F_EV_EXPERIENCE = 75,
    F_VEHICLE_TYPE = 76,
};
constexpr const char* kFeatureNames[kNumFeatures] = {
    "rms_x_10s",
    "rms_x_1m",
    "rms_x_5m",
    "rms_y_10s",
    "rms_y_1m",
    "rms_y_5m",
    "rms_z_10s",
    "rms_z_1m",
    "rms_z_5m",
    "decel_rms_x_1m",
    "decel_rms_x_5m",
    "accel_rms_x_1m",
    "accel_rms_x_5m",
    "jerk_rms_x_10s",
    "jerk_rms_x_1m",
    "jerk_rms_y_10s",
    "jerk_rms_y_1m",
    "yaw_rms_10s",
    "yaw_rms_1m",
    "hard_accel_count_1m",
    "hard_accel_count_5m",
    "hard_brake_count_1m",
    "hard_brake_count_5m",
    "hard_turn_count_1m",
    "hard_turn_count_5m",
    "event_irregularity_5m",
    "pitch_mean_1m",
    "msdv",
    "msdv_inc_5m",
    "oman_score",
    "oman_delta_2m",
    "sensitization",
    "svc_conflict",
    "speed_mean_10s",
    "speed_mean_1m",
    "speed_mean_5m",
    "speed_std_1m",
    "speed_std_5m",
    "stop_ratio_5m",
    "speed_max_5m",
    "accel_pedal_rms_1m",
    "brake_pedal_rms_1m",
    "pedal_rate_rms_1m",
    "drive_mode",
    "cruise_on",
    "regen_decel_rms_1m",
    "regen_decel_rms_5m",
    "regen_level",
    "one_pedal",
    "regen_share_5m",
    "soc",
    "batt_power_mean_1m",
    "batt_power_std_1m",
    "motor_torque_rate_rms_1m",
    "dist_next_turn",
    "angle_next_turn",
    "pred_ax_10s",
    "pred_ay_10s",
    "curvature_ahead",
    "cabin_temp",
    "set_temp",
    "fan_level",
    "ac_on",
    "seat_row",
    "seatback_angle",
    "window_open",
    "noise_level",
    "noise_lowfreq_level",
    "ride_time",
    "reexposure",
    "time_since_rest",
    "tol_display",
    "tol_phone",
    "mssq",
    "sensitivity",
    "ev_experience",
    "vehicle_type",
};
constexpr bool kFeatureEnabled[kNumFeatures] = {
    true, true, true, true, true, true, true, true, true, true, true, true, true, true, true, true, true, true, true, true, true, true, true, true, true, true, true, true, true, true, true, true, false, true, true, true, true, true, true, true, true, true, true, true, true, true, true, true, true, true, true, true, true, true, true, true, true, true, true, true, true, true, true, true, true, true, true, true, true, true, true, false, false, true, true, true, true
};

// ---- trip_format.json ----
constexpr int kTripVersion = 1;
constexpr int kTripCols = 32;
enum TripCol : int {
    C_T = 0,
    C_AX = 1,
    C_AY = 2,
    C_AZ = 3,
    C_GX = 4,
    C_GY = 5,
    C_GZ = 6,
    C_SPEED = 7,
    C_ACCEL_PEDAL = 8,
    C_BRAKE_PEDAL = 9,
    C_DRIVE_MODE = 10,
    C_CRUISE_ON = 11,
    C_REGEN_DECEL = 12,
    C_REGEN_LEVEL = 13,
    C_ONE_PEDAL = 14,
    C_SOC = 15,
    C_BATT_POWER = 16,
    C_MOTOR_TORQUE = 17,
    C_DIST_NEXT_TURN = 18,
    C_ANGLE_NEXT_TURN = 19,
    C_PRED_AX_10S = 20,
    C_PRED_AY_10S = 21,
    C_CURVATURE_AHEAD = 22,
    C_CABIN_TEMP = 23,
    C_SET_TEMP = 24,
    C_FAN_LEVEL = 25,
    C_AC_ON = 26,
    C_SEAT_ROW = 27,
    C_SEATBACK_ANGLE = 28,
    C_WINDOW_OPEN = 29,
    C_NOISE_LEVEL = 30,
    C_NOISE_LOWFREQ_LEVEL = 31,
};

// ---- constants.json ----
constexpr double kSampleRateHz = 100.0;
constexpr double kFeaturePeriodS = 10.0;
constexpr double kOmanK0 = 51.4;
constexpr double kOmanBetaFast = 66.2;
constexpr double kOmanBetaSlow = 502.4;
constexpr double kOmanExponent = 0.4;
constexpr double kOmanInputLowpassHz = 0.63;
constexpr double kWf_f1 = 0.08;
constexpr double kWf_f2 = 0.63;
constexpr double kWf_q1 = 0.7071067811865476;
constexpr double kWf_f4 = 0.25;
constexpr double kWf_q4 = 0.86;
constexpr double kWf_f5 = 0.0625;
constexpr double kWf_q5 = 0.8;
constexpr double kWf_f6 = 0.1;
constexpr double kWf_q6 = 0.8;
constexpr double kHardAccelX = 1.5;
constexpr double kHardBrakeX = -0.75;
constexpr double kHardTurnAbsY = 2.0;
constexpr double kEventRearmMargin = 0.25;
constexpr double kRestStillTimeS = 180.0;
constexpr double kRestStillAccel = 0.05;
constexpr double kMssqMean = 12.9;
constexpr double kMssqSd = 9.9;
constexpr double kSensCoef = 0.3;
constexpr double kSensMin = 0.5;
constexpr double kSensMax = 2.0;
constexpr double kDefaultMssq = 12.9;
constexpr double kDefaultSens = 1.0;
constexpr const char* kDeviceAirflow = "airflow-01";
constexpr const char* kDeviceDisplay = "display-01";
constexpr const char* kDeviceHud = "hud-01";

}  // namespace mm::proto
