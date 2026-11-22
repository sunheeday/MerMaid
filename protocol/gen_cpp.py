"""protocol/*.json, ride_schema.sql → controller/common/protocol_gen.hpp

    python3 protocol/gen_cpp.py          # 헤더를 다시 만든다
    python3 protocol/gen_cpp.py --check  # 헤더가 JSON과 같은지만 확인 (check.py가 호출)

QNX에 JSON 파서를 넣지 않으려고, C++는 이 생성 헤더로 같은 값을 읽는다. 헤더는 직접 고치지 않는다.
ride_schema.sql(P7 주행 파일 표)은 문자열 그대로 넣는다: P7이 이 SQL로 파일을 만들고, 서버는 같은 파일을 읽는다.
"""
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent
OUT = ROOT.parent / "controller" / "common" / "protocol_gen.hpp"


def ride_schema():
    """(schema_version, SQL 본문). 버전은 '-- schema_version: N' 줄"""
    sql = (ROOT / "ride_schema.sql").read_text()
    m = re.search(r"^-- schema_version: (\d+)$", sql, re.M)
    if not m:
        raise SystemExit("ride_schema.sql: '-- schema_version: N' 줄이 없음")
    if ')SQL"' in sql:
        raise SystemExit("ride_schema.sql에 )SQL\" 문자열이 있으면 C++ raw string이 깨짐")
    return int(m.group(1)), sql


def render():
    rv, rsql = ride_schema()
    c = json.loads((ROOT / "constants.json").read_text())
    fo = json.loads((ROOT / "feature_order.json").read_text())
    tf = json.loads((ROOT / "trip_format.json").read_text())
    f = fo["features"]
    num = lambda v: repr(float(v))
    L = [
        "// 생성 파일: python3 protocol/gen_cpp.py. 직접 고치지 말 것.",
        "#pragma once",
        "",
        "namespace mm::proto {",
        "",
        "// ---- feature_order.json ----",
        f"constexpr int kFeatureVersion = {fo['version']};",
        f"constexpr int kNumFeatures = {len(f)};",
        f"constexpr int kNumVehicle = {fo['vehicle_range'][1] + 1};",
        "enum Feat : int {",
        *[f"    F_{x['name'].upper()} = {x['index']}," for x in f],
        "};",
        "constexpr const char* kFeatureNames[kNumFeatures] = {",
        *[f'    "{x["name"]}",' for x in f],
        "};",
        "constexpr bool kFeatureEnabled[kNumFeatures] = {",
        "    " + ", ".join("true" if x["enabled"] else "false" for x in f),
        "};",
        "",
        "// ---- trip_format.json ----",
        f"constexpr int kTripVersion = {tf['version']};",
        f"constexpr int kTripCols = {len(tf['columns'])};",
        "enum TripCol : int {",
        *[f"    C_{x['name'].upper()} = {i}," for i, x in enumerate(tf["columns"])],
        "};",
        "constexpr const char* kTripNames[kTripCols] = {",
        *[f'    "{x["name"]}",' for x in tf["columns"]],
        "};",
        "",
        "// ---- constants.json ----",
        f"constexpr double kSampleRateHz = {num(c['sample_rate_hz'])};",
        f"constexpr double kFeaturePeriodS = {num(c['feature_period_s'])};",
        f"constexpr double kOmanK0 = {num(c['oman']['K0'])};",
        f"constexpr double kOmanBetaFast = {num(c['oman']['beta_fast_s'])};",
        f"constexpr double kOmanBetaSlow = {num(c['oman']['beta_slow_s'])};",
        f"constexpr double kOmanExponent = {num(c['oman']['exponent'])};",
        f"constexpr double kOmanInputLowpassHz = {num(c['oman']['input_lowpass_hz'])};",
        *[f"constexpr double kWf_{k[:-3] if k.endswith('_hz') else k} = {num(v)};"
          for k, v in c["wf"].items() if isinstance(v, (int, float))],
        f"constexpr double kHardAccelX = {num(c['events']['hard_accel_x_mps2'])};",
        f"constexpr double kHardBrakeX = {num(c['events']['hard_brake_x_mps2'])};",
        f"constexpr double kHardTurnAbsY = {num(c['events']['hard_turn_abs_y_mps2'])};",
        f"constexpr double kEventRearmMargin = {num(c['events']['rearm_margin_mps2'])};",
        f"constexpr double kRestStillTimeS = {num(c['rest']['still_time_s'])};",
        f"constexpr double kRestStillAccel = {num(c['rest']['still_weighted_accel_mps2'])};",
        f"constexpr double kMssqMean = {num(c['mssq']['norm_mean'])};",
        f"constexpr double kMssqSd = {num(c['mssq']['norm_sd'])};",
        f"constexpr double kSensCoef = {num(c['mssq']['sens_coef'])};",
        f"constexpr double kSensMin = {num(c['mssq']['sens_min'])};",
        f"constexpr double kSensMax = {num(c['mssq']['sens_max'])};",
        f"constexpr double kDefaultMssq = {num(c['mssq']['default_profile']['mssq'])};",
        f"constexpr double kDefaultSens = {num(c['mssq']['default_profile']['sens'])};",
        f"constexpr int kMotPeriodMs = {int(c['mot_period_ms'])};  // P2 -> P6 MOTION 주기",
        f"constexpr const char* kDeviceAirflow = \"{c['device_ids']['airflow']}\";",
        f"constexpr const char* kDeviceDisplay = \"{c['device_ids']['display']}\";",
        f"constexpr const char* kDeviceHud = \"{c['device_ids']['hud']}\";",
        "",
        "// ---- ride_schema.sql (P7 주행 파일) ----",
        f"constexpr int kRideSchemaVersion = {rv};",
        'constexpr const char* kRideSchemaSql = R"SQL(',
        rsql.rstrip("\n"),
        ')SQL";',
        "",
        "}  // namespace mm::proto",
        "",
    ]
    return "\n".join(L)


if __name__ == "__main__":
    text = render()
    if "--check" in sys.argv:
        ok = OUT.exists() and OUT.read_text() == text
        print("protocol_gen.hpp " + ("최신" if ok else "가 JSON·SQL과 다름: python3 protocol/gen_cpp.py"))
        sys.exit(0 if ok else 1)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text)
    print(f"wrote {OUT}")
