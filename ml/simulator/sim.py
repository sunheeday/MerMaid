"""합성 주행 시뮬레이터 (PRD 4.1, S3)

경로(trip) → 운전(ride: 회생 설정마다 속도 프로파일을 다시 계산) → 100 Hz trip.bin 행 + 정답 MISC.
모든 수치는 sim_config.json과 sim_effects.json에서 읽는다. 함수는 난수 생성기만 받으면 재현된다.
"""
import json
import math
import pathlib
import sys

import numpy as np
from scipy.ndimage import gaussian_filter1d
from scipy.signal import butter, filtfilt, sosfilt

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import oman  # noqa: E402
import trip as tripio  # noqa: E402

CFG = json.loads((HERE / "sim_config.json").read_text())
EFF = json.loads((HERE / "sim_effects.json").read_text())
FS = tripio.FS
DT = 1.0 / FS
G = 9.81
DS = 0.5  # 경로 거리 격자 (m)
KMH = 1 / 3.6


def U(rng, lo_hi):
    lo, hi = lo_hi
    return float(rng.uniform(lo, hi))


# ---------------------------------------------------------------- 승객
def gen_passenger(rng, pid):
    p = CFG["passenger"]
    mssq = float(np.clip(rng.normal(p["mssq_mean"], p["mssq_sd"]), 0, 54))
    sens = float(oman.sensitivity(mssq))  # 앱이 아는 값 (G10)
    true = sens * math.exp(rng.normal(0, p["true_sens_log_sd"]))  # 실제 체질: MSSQ로 다 설명되지 않음
    return {"pid": pid, "mssq": mssq, "sens": sens,
            "true_sens": float(np.clip(true, *p["true_sens_clip"])),
            "ev_experience": float(rng.random() < p["ev_experience_p"])}


# ---------------------------------------------------------------- 경로
def gen_route(rng):
    """거리 격자 위의 곡률, 목표 속도, 경사, 정차 지점, 회전(내비)."""
    S = CFG["segments"]
    names = list(S["weights"])
    w = np.array([S["weights"][n] for n in names])
    target_s = U(rng, CFG["trip_minutes"]["range"]) * 60
    kappa, vt, grade, stops, turns = [], [], [], [], []
    est_t, s0 = 0.0, 0.0

    def add(length, k, v, g=0.0):
        n = max(1, int(round(length / DS)))
        kappa.append(np.full(n, k)); vt.append(np.full(n, v)); grade.append(np.full(n, g))
        return n * DS

    while est_t < target_s:
        kind = names[rng.choice(len(names), p=w / w.sum())]
        c = S[kind]
        if kind == "straight":
            v = U(rng, c["speed_kmh"]) * KMH
            L = add(U(rng, c["length_m"]), 0.0, v)
        elif kind == "stop":
            v = U(rng, c["speed_kmh"]) * KMH
            L = add(U(rng, c["length_m"]), 0.0, v)
            dwell = U(rng, c["dwell_s"])
            stops.append((s0 + L, dwell))
            est_t += dwell
        elif kind == "turn":
            R, sgn, v = U(rng, c["radius_m"]), rng.choice([-1, 1]), c["speed_kmh"] * KMH
            L = add(R * math.pi / 2, sgn / R, v)
            turns.append((s0 + L / 2, sgn * math.pi / 2))
        elif kind == "s_curve":
            R, v = U(rng, c["radius_m"]), U(rng, c["speed_kmh"]) * KMH
            sgn, a = rng.choice([-1, 1]), math.radians(U(rng, c["angle_deg"]))
            L = add(R * a, sgn / R, v) + add(R * a, -sgn / R, v)
        elif kind == "roundabout":
            R, a, v = U(rng, c["radius_m"]), math.radians(U(rng, c["angle_deg"])), c["speed_kmh"] * KMH
            L = add(R * a, 1.0 / R, v)  # 반시계 회전 (y 왼쪽 +)
            turns.append((s0 + L / 2, a))
        else:  # hill
            v = U(rng, c["speed_kmh"]) * KMH
            L = add(U(rng, c["length_m"]), 0.0, v, rng.choice([-1, 1]) * U(rng, c["grade"]))
        s0 += L
        est_t += L / v
    kappa = gaussian_filter1d(np.concatenate(kappa), S["curvature_smooth_m"] / DS)
    grade = gaussian_filter1d(np.concatenate(grade), S["grade_smooth_m"] / DS)
    return {"kappa": kappa, "vt": np.concatenate(vt), "grade": grade, "stops": stops, "turns": turns,
            "length": len(kappa) * DS}


def gen_driver(rng):
    d = CFG["driver"]
    return {k: U(rng, d[k]) for k in ("accel_mps2", "decel_mps2", "lat_accel_mps2", "speed_factor")}


# ---------------------------------------------------------------- 운전
def drive(route, driver, regen_level, rng):
    """속도 프로파일 → 100 Hz 운동학 (회생 단계가 높을수록 감속이 세짐)."""
    d, rg = CFG["driver"], CFG["regen"]
    n = len(route["kappa"])
    s = np.arange(n) * DS
    # 목표 속도의 느린 흔들림 (운전자 변동)
    rho = math.exp(-DS / d["wobble_length_m"])
    e = np.empty(n)
    e[0] = 0.0
    nz = rng.normal(0, d["speed_wobble"] * math.sqrt(1 - rho * rho), n)
    for i in range(1, n):
        e[i] = rho * e[i - 1] + nz[i]
    vlim = route["vt"] * driver["speed_factor"] * (1 + e)
    k = np.abs(route["kappa"])
    vlim = np.minimum(vlim, np.sqrt(driver["lat_accel_mps2"] / np.maximum(k, 1e-6)))
    stop_idx = [min(n - 1, int(round(ss / DS))) for ss, _ in route["stops"]]
    vlim[stop_idx] = 0.0
    vlim[0] = vlim[-1] = 0.0
    a_dec = driver["decel_mps2"] + (regen_level * rg["extra_driver_decel_per_level"] if regen_level is not None else 0)
    a_acc = driver["accel_mps2"]
    v2 = vlim ** 2
    for i in range(n - 2, -1, -1):
        v2[i] = min(v2[i], v2[i + 1] + 2 * a_dec * DS)
    for i in range(1, n):
        v2[i] = min(v2[i], v2[i - 1] + 2 * a_acc * DS)
    v = np.sqrt(v2)
    # 거리 → 시간 (정차 지점에서 대기 시간 추가)
    vm = 0.5 * (v[1:] + v[:-1])
    dt_seg = np.where(vm > 1e-6, DS / np.maximum(vm, 1e-6), 0.0)
    t = np.concatenate([[0.0], np.cumsum(dt_seg)])
    dwell = np.zeros(n)
    for (ss, dw), i in zip(route["stops"], stop_idx):
        dwell[i] += dw
    t = t + np.concatenate([[0.0], np.cumsum(dwell)[:-1]])
    # 정차 지점은 대기 시작과 끝 두 점으로
    ts, ss_, vs = [t[0]], [s[0]], [v[0]]
    for i in range(1, n):
        if dwell[i - 1] > 0:
            ts.append(t[i - 1] + dwell[i - 1]); ss_.append(s[i - 1]); vs.append(0.0)
        ts.append(t[i]); ss_.append(s[i]); vs.append(v[i])
    T = np.arange(0, ts[-1], DT)
    vt = np.interp(T, ts, vs)
    # 저크 제한: 영위상 저역통과
    b, a = butter(2, d["smoothing_hz"], fs=FS)
    vt = np.maximum(filtfilt(b, a, vt), 0.0)
    st = np.cumsum(vt) * DT
    along = np.gradient(vt, DT)
    kap = np.interp(st, s, route["kappa"])
    theta = np.arctan(np.interp(st, s, route["grade"]))
    return {"t": T, "v": vt, "s": st, "a_long": along, "a_lat": vt ** 2 * kap, "kappa": kap, "theta": theta}


# ---------------------------------------------------------------- 탑승 (trip.bin 행)
def ride_rows(route, motion, ride, rng):
    """운동학과 탑승 설정 → trip_format.json 행 (n x 32)과 Oman 배수 m."""
    C, V, I, CB = tripio.COL, CFG["vehicle"], CFG["imu"], CFG["cabin"]
    t, v, al, ay, th = motion["t"], motion["v"], motion["a_long"], motion["a_lat"], motion["theta"]
    n = len(t)
    rows = tripio.empty(n)
    rows[:, C["t"]] = t

    # IMU: 노면 진동(속도 비례, 2~15 Hz) + 센서 잡음
    sos = butter(2, I["vib_band_hz"], btype="band", fs=FS, output="sos")
    def vib(ratio):
        x = sosfilt(sos, rng.normal(0, 1, n))
        return x / x.std() * (I["vib_base"] + I["vib_per_mps"] * v) * ratio
    an = lambda: rng.normal(0, I["accel_noise"], n)
    gn = lambda: rng.normal(0, I["gyro_noise"], n)
    rows[:, C["ax"]] = al + G * np.sin(th) + vib(I["vib_xy_ratio"]) + an()
    rows[:, C["ay"]] = ay + vib(I["vib_xy_ratio"]) + an()
    rows[:, C["az"]] = G * np.cos(th) + vib(1.0) + an()
    rows[:, C["gx"]] = gn()
    rows[:, C["gy"]] = np.gradient(th, DT) + gn()
    rows[:, C["gz"]] = v * motion["kappa"] + gn()

    # 차량 동역학
    rows[:, C["speed"]] = v
    m = V["mass_kg"]
    a_res = V["rolling"] * G * (v > 0.1) + 0.5 * 1.2 * V["cda_m2"] * v ** 2 / m + G * np.sin(th)
    F = m * (al + a_res)
    ev, lvl = ride["vehicle_type"] == 1, ride["regen_level"]
    if ev:
        regen = np.minimum(np.maximum(-F, 0), m * CFG["regen"]["max_decel_mps2"][lvl])
    else:
        regen = np.minimum(np.maximum(-F, 0), m * V["ice_engine_brake_mps2"])
    brake = np.maximum(-F, 0) - regen
    rows[:, C["accel_pedal"]] = np.clip(np.maximum(F, 0) / (m * V["max_drive_mps2"]), 0, 1)
    rows[:, C["brake_pedal"]] = np.clip(brake / (m * V["max_brake_mps2"]), 0, 1)
    rows[:, C["drive_mode"]] = ride["drive_mode"]
    cruise = (v > 60 * KMH) & (np.abs(al) < 0.3) & (np.abs(motion["kappa"]) < 1e-3) & ride["cruise_user"]
    rows[:, C["cruise_on"]] = cruise.astype(float)
    if ev:
        rows[:, C["regen_decel"]] = regen / m
        rows[:, C["regen_level"]] = lvl
        rows[:, C["one_pedal"]] = float(lvl == CFG["regen"]["one_pedal_level"])
        drive_kw = np.where(F > 0, F * v / V["drive_eff"], -regen * v * V["regen_eff"]) / 1000
        p_kw = drive_kw + V["aux_kw"] + V["climate_kw"] * ride["ac_on"]
        rows[:, C["batt_power"]] = p_kw
        rows[:, C["soc"]] = np.clip(ride["soc0"] - np.cumsum(p_kw) * DT / 3600 / V["battery_kwh"], 0, 1)
        rows[:, C["motor_torque"]] = np.where(F > 0, F, -regen) * V["wheel_radius_m"] / V["gear_ratio"]

    # 내비: 다음 회전, 10초 뒤 가속도, 50 m 앞 곡률
    turns = route["turns"]
    if turns:
        ts_ = np.array([x for x, _ in turns])
        ang = np.array([y for _, y in turns])
        j = np.searchsorted(ts_, motion["s"])
        has = j < len(ts_)
        jj = np.minimum(j, len(ts_) - 1)
        rows[:, C["dist_next_turn"]] = np.where(has, ts_[jj] - motion["s"], np.nan)
        rows[:, C["angle_next_turn"]] = np.where(has, ang[jj], np.nan)
    sh = int(10 * FS)
    rows[:, C["pred_ax_10s"]] = np.concatenate([al[sh:], np.full(sh, al[-1])]) + rng.normal(0, 0.1, n)
    rows[:, C["pred_ay_10s"]] = np.concatenate([ay[sh:], np.full(sh, ay[-1])]) + rng.normal(0, 0.1, n)
    s_grid = np.arange(len(route["kappa"])) * DS
    rows[:, C["curvature_ahead"]] = np.interp(motion["s"] + 50, s_grid, route["kappa"])

    mult = cabin_rows(rows, t, v, ride, rng)
    return rows, float(mult)


def cabin_rows(rows, t, v, ride, rng):
    """공조·좌석·소음 칸을 rows에 채우고 Oman 이득 배수 m을 돌려준다 (실차 데이터 변환에도 쓴다)."""
    C, CB = tripio.COL, CFG["cabin"]
    n = len(t)
    # 공조, 좌석, 소음
    temp = ride["set_c"] + (ride["start_c"] - ride["set_c"]) * np.exp(-t / CB["tau_s"])
    rows[:, C["cabin_temp"]] = temp
    rows[:, C["set_temp"]] = ride["set_c"]
    rows[:, C["fan_level"]] = ride["fan_level"]
    rows[:, C["ac_on"]] = ride["ac_on"]
    rows[:, C["seat_row"]] = ride["seat_row"]
    rows[:, C["seatback_angle"]] = ride["seatback_deg"]
    rows[:, C["window_open"]] = ride["window_open"]
    noise = 45 + 0.35 * v / KMH + 3 * ride["window_open"] + rng.normal(0, 1, n)
    rows[:, C["noise_level"]] = noise
    rows[:, C["noise_lowfreq_level"]] = noise - 6 + 0.05 * v / KMH

    # Oman 이득 배수 m (sim_effects.json, 탑승마다 크기를 무작위로)
    mult = 1.0
    if ride["seat_row"] == 1:
        mult *= U(rng, EFF["rear_seat"]["range"])
    ct = EFF["cabin_temp"]
    frac = np.clip((temp.mean() - ct["start_c"]) / (ct["full_c"] - ct["start_c"]), 0, 1)
    mult *= 1 + (U(rng, ct["max_range"]) - 1) * frac
    if ride["fan_level"] >= EFF["fan"]["min_level"]:
        mult *= U(rng, EFF["fan"]["range"])
    mult *= EFF["noise"]["factor"]
    return float(mult)


def gen_ride(rng, vehicle_type):
    CB = CFG["cabin"]
    return {"vehicle_type": vehicle_type,
            "regen_level": int(rng.integers(0, 4)) if vehicle_type == 1 else None,
            "drive_mode": float(rng.integers(0, 3)),
            "cruise_user": bool(rng.random() < 0.5),
            "soc0": U(rng, (0.3, 0.95)),
            "set_c": U(rng, CB["set_c"]), "start_c": U(rng, CB["start_c"]),
            "fan_level": float(rng.integers(CB["fan_levels"][0], CB["fan_levels"][1] + 1)),
            "ac_on": float(rng.random() < 0.5),
            "seat_row": float(rng.random() < CB["rear_p"]),
            "seatback_deg": U(rng, CB["seatback_deg"]),
            "window_open": float(rng.random() < CB["window_open_p"])}


# ---------------------------------------------------------------- 라벨
def label(motion, K, rng, emit_idx):
    """10초 시점마다 MISC와 y_k. 미래 5분이 다 있는 시점만 쓸 수 있다(valid)."""
    L = CFG["label"]
    c = oman.input_signal(motion["a_long"], motion["a_lat"], DT)
    score = oman.run(c, K, DT)[0][emit_idx]
    t = motion["t"][emit_idx]
    block = (t // L["noise_block_s"]).astype(int)
    noise = rng.normal(0, L["noise_sd"], block.max() + 1)[block]
    misc = np.clip(np.round(score + noise), 0, 10)
    h = int(L["horizon_s"] / 10)
    n = len(misc)
    fut = np.array([misc[j + 1:j + 1 + h].max() if j + h < n else np.nan for j in range(n)])
    y = {k: (fut >= k).astype(float) for k in L["k"]}
    return score, misc, fut, y, np.arange(n) + h < n
