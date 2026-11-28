"""5차 실험 세계 — v6 피처(52 − G8 2 + 새 16 = 66)를 만드는 탑승 시뮬레이터 (기존 ml/simulator 위에 얹음).

기존 시뮬레이터(sim.py)는 그대로 쓰고(경로·운전·trip.bin·C++ featurize), 여기서 더하는 것:
  1-1 승객의 시각 과제 5상태 마르코프(안 씀/음악/영상/글/게임) → Oman 이득 K(t)에 사람별 배율 (영상 1.2~1.4, 글 1.4~1.7, 게임 1.6~2.0)
  1-2 개입 효과: 승객이 직접 켠 장치부터 누적 속도 배율 (선풍기 0.6~0.9, 주변 신호 0.7~0.95, 도트 0.6~0.9 화면 볼 때만), 1~2분 램프
      → MISC 를 장치 순서대로 다시 계산 (선풍기 → 주변 신호 → 도트), 사건 시각은 바뀌지 않는다
  1-3 상황 의존 습관: 더위·고속·화면·게임 계수(사람마다), 익숙해짐(이 차 탑승 횟수), 기분(탑승마다), 인지(한 번 쓰면 계속)
  1-4 일정: 출퇴근형/불규칙형 탑승 시각, 마지막 탑승 뒤 시간, 2시간 안 재탑승이면 느린 상태 S 잔류 30~60%
  1-5 운전: 가속→감속 직행 횟수(100 Hz 종가속도에서 셈), 펌프질 운전자 10%
라벨: 기존 y_k(MISC≥k, 최종 MISC 기준)과 반응 라벨 Y/W(장치 3개, 사건 전 5분 = 1, 자격 없음·사건 뒤 = W 0) 를 세계가 직접 만든다.
모든 크기는 WORLD / HABIT 에 있고 사람·탑승마다 범위에서 뽑는다.
"""
import json
import math
import pathlib
import sys
import tempfile

import numpy as np
from scipy.signal import lfilter

HERE = pathlib.Path(__file__).resolve().parent
ML = HERE.parents[1]
sys.path.insert(0, str(ML)); sys.path.insert(0, str(ML / "simulator"))
import oman  # noqa: E402
import sim  # noqa: E402
import trip as tripio  # noqa: E402

FS, DT = sim.FS, sim.DT
STEP = 10.0                      # 피처·라벨 방출 간격 (s)
HORIZON = 300.0                  # 반응 라벨 지평 (s)
HEADS = ["fan", "ambient", "dots"]
STATES = ["none", "music", "video", "text", "game"]
NONE, MUSIC, VIDEO, TEXT, GAME = range(5)

# ---------------------------------------------------------------- v6 피처 순서: v5 차량 46 + G10 4 + 새 16
V5 = tripio.FEATURES["features"]
assert tripio.FEATURES["version"] == 5, "v5 저장소(protocol/feature_order.json version 5)에서 돌려야 한다"
KEEP5 = [f["index"] for f in V5 if f["group"] != "G8"]                      # 50개 (G8 허용도 2개 제거)
NEW = [  # (이름, 그룹, 설명)
    ("screen_on_share_5m", "G11", "최근 5분 중 폰 화면 켜짐 비율"),
    ("app_fg_share_5m", "G11", "최근 5분 중 우리 앱 전면 비율"),
    ("rx_rate_log_5m", "G11", "최근 5분 평균 log10(1 + 수신 kB/s) (보류 피처)"),
    ("media_share_5m", "G11", "최근 5분 중 미디어 재생 비율 (보류)"),
    ("headphones_on", "G11", "이어폰 연결 여부 (보류)"),
    ("landscape_share_5m", "G11", "최근 5분 중 가로 방향 비율 (보류)"),
    ("fan_minutes_on", "G12", "선풍기 켜진 지 몇 분 (꺼짐 0)"),
    ("ambient_minutes_on", "G12", "주변 신호 켜진 지 몇 분"),
    ("dots_minutes_on", "G12", "모션 도트 켜진 지 몇 분"),
    ("ops_count_ride", "G13", "이번 탑승 수동 조작 횟수 (지금까지)"),
    ("min_since_last_op", "G13", "마지막 수동 조작 뒤 분 (없으면 NaN)"),
    ("tod_sin", "G14", "하루 중 시각 sin"),
    ("tod_cos", "G14", "하루 중 시각 cos"),
    ("hours_since_last_ride", "G14", "마지막 탑승 뒤 시간 (첫 탑승 NaN)"),
    ("rides_with_vehicle", "G14", "이 차를 탄 누적 횟수 (이번 포함 전)"),
    ("accel_decel_switch_5m", "G15", "최근 5분 가속(+0.5)→1초 안 감속(−0.5) 직행 횟수"),
]
NAMES = [V5[i]["name"] for i in KEEP5] + [n for n, _, _ in NEW]
GROUPS = [V5[i]["group"] for i in KEEP5] + [g for _, g, _ in NEW]
MONOTONE = [V5[i]["monotone"] for i in KEEP5] + [0] * len(NEW)
ENABLED = [V5[i]["enabled"] for i in KEEP5] + [True] * len(NEW)         # svc_conflict 만 꺼짐
DROPPABLE = [V5[i].get("droppable", True) for i in KEEP5] + [True] * len(NEW)
GROUP_DROPOUT = {g: v["dropout"] for g, v in tripio.FEATURES["groups"].items() if g != "G8"}
GROUP_DROPOUT.update({"G11": 0.10, "G12": 0.0, "G13": 0.0, "G14": 0.05, "G15": 0.0})
GROUP_DESC = {"G11": "phone: visual task signals", "G12": "device state (minutes on)", "G13": "manual operation history",
              "G14": "schedule / exposure", "G15": "driving pattern"}
NF = len(NAMES)                                                              # 66
assert NF == 66, NF
COL = {n: i for i, n in enumerate(NAMES)}
FIDX_SPEED_1M = tripio.FIDX["speed_mean_1m"]
FIDX_TEMP = tripio.FIDX["cabin_temp"]

# ---------------------------------------------------------------- 세계 상수 (크기는 전부 범위, 사람·탑승마다 뽑음)
WORLD = {
    "visual_types": {"nonuser": 0.30, "text": 0.30, "video": 0.25, "gamer": 0.15},
    "visual_pi": {  # 상태에 머무는 비율의 중심 (none, music, video, text, game)
        "nonuser": [0.70, 0.20, 0.04, 0.06, 0.00], "text": [0.25, 0.10, 0.15, 0.45, 0.05],
        "video": [0.20, 0.10, 0.50, 0.15, 0.05], "gamer": [0.20, 0.05, 0.15, 0.20, 0.40]},
    "visual_pi_conc": 25.0,                     # 사람별 디리클레 집중도 (성향 흔들림)
    "visual_ride_conc": 60.0,                   # 탑승별 집중도
    "dwell_s": {"none": [120, 600], "music": [180, 600], "video": [180, 600], "text": [120, 480], "game": [180, 600]},
    "mult": {"video": [1.2, 1.4], "text": [1.4, 1.7], "game": [1.6, 2.0]},
    "mult_tau_s": [60, 120],                    # 상태 바뀔 때 배율이 따라오는 시간
    "effect": {"fan": [0.6, 0.9], "ambient": [0.7, 0.95], "dots": [0.6, 0.9]},
    "effect_tau_s": [60, 120],
    "theta": {"fan": (3.4, 0.8, 2.6), "ambient": (4.3, 1.4, 2.5), "dots": (4.6, 1.6, 2.5)},   # (중심, sd, 하한)
    "theta_sens_coef": -0.8,                    # 문턱 = 중심 + coef·log(보이는 민감도) + 잡음
    "mood_sd": 0.5,
    "aware_p": 0.65, "discover_p": 0.05,        # 처음 인지 확률, 모르는 사람이 탑승마다 알게 될 확률
    "display_on_p": 0.7, "phone_off_p": 0.10,
    "headphone_p": [0.1, 0.9],
    "commuter_p": 0.55, "pumping_p": 0.10,
    "carry_gap_h": 2.0, "carry_frac": [0.3, 0.6],
    "phone": {  # 상태별 폰 신호 (10초 스텝마다 뽑음)
        "screen_p": [0.05, 0.10, 1.00, 0.95, 1.00],
        "rx_kBs_median": [0.5, 30.0, 600.0, 6.0, 20.0], "rx_log_sd": [0.6, 0.3, 0.25, 0.8, 0.5],
        "text_burst_p": 0.2, "text_burst_kBs": 120.0,
        "media_p": [0.0, 1.0, 1.0, 0.05, 0.8],
        "landscape_p": [0.0, 0.0, 0.70, 0.10, 0.85],
        "app_fg_screen_p": 0.10, "app_fg_dots_p": 0.60,
    },
    "switch": {"hi": 0.5, "lo": -0.5, "within_s": 1.0},
}
HABIT = {  # 상황 의존 습관의 세기 — 계수 상한 (사람마다 U(0, 상한))
    "off":    {"heat": 0.15, "highway": 0.0, "screen": 0.0, "game": 0.0, "habit_rate": 0.0},   # 4차 세계와 비슷
    "weak":   {"heat": 0.25, "highway": 0.5, "screen": 0.5, "game": 0.25, "habit_rate": 0.03},
    "medium": {"heat": 0.375, "highway": 0.75, "screen": 0.75, "game": 0.375, "habit_rate": 0.03},  # weak와 strong의 중간
    "strong": {"heat": 0.50, "highway": 1.0, "screen": 1.0, "game": 0.50, "habit_rate": 0.03},
}
HABIT_CAP = 0.5            # 익숙해짐 문턱 상승 상한
HIGHWAY_KMH = 80.0
PUMP_CFG = {"speed_wobble": 0.12, "wobble_length_m": 40.0}


def U(rng, lo_hi):
    return float(rng.uniform(lo_hi[0], lo_hi[1]))


# ---------------------------------------------------------------- 사람
def gen_person(rng, pid, habit="weak", vehicle_type=None):
    """숨은 성질 전부. 피처에는 mssq·sens·ev_experience·vehicle_type 만 나간다."""
    h = HABIT[habit]; W = WORLD
    base = sim.gen_passenger(rng, pid)
    s_ = float(np.clip(base["sens"], 0.5, 2.0)); shift = W["theta_sens_coef"] * math.log(s_)
    theta = {d: max(W["theta"][d][2], W["theta"][d][0] + shift + rng.normal(0, W["theta"][d][1])) for d in HEADS}
    names = list(W["visual_types"]); vt = names[rng.choice(len(names), p=[W["visual_types"][n] for n in names])]
    pi = rng.dirichlet(np.maximum(np.array(W["visual_pi"][vt]) * W["visual_pi_conc"], 0.05))
    return {**base, "habit": habit,
            "vehicle_type": int(rng.random() < sim.CFG["ev_share"]["value"]) if vehicle_type is None else int(vehicle_type),
            "theta": theta,
            "heat": rng.uniform(0, h["heat"]), "highway": rng.uniform(0, h["highway"]),
            "screen": rng.uniform(0, h["screen"]), "game": rng.uniform(0, h["game"]), "habit_rate": rng.uniform(0, h["habit_rate"]),
            "aware": {d: bool(rng.random() < W["aware_p"]) for d in HEADS},
            "visual_type": vt, "pi": pi,
            "mult": {k: U(rng, v) for k, v in W["mult"].items()}, "mult_tau": U(rng, W["mult_tau_s"]),
            "effect": {d: U(rng, W["effect"][d]) for d in HEADS}, "effect_tau": U(rng, W["effect_tau_s"]),
            "headphone_p": U(rng, W["headphone_p"]),
            "commuter": bool(rng.random() < W["commuter_p"]), "pumping": bool(rng.random() < W["pumping_p"]),
            "rides_done": 0, "last_end_h": None, "S_end": 0.0}


# ---------------------------------------------------------------- 일정 (시각은 0일 월요일 0시부터의 시간, h)
def schedule(person, n, rng, start_day=0):
    """탑승 시작 시각 n개 (h). 출퇴근형: 평일 아침·저녁, 가끔 점심 심부름(왕복). 불규칙형: 1~5일 간격 10~22시, 30%는 0.5~2h 뒤 복귀."""
    out = []; day = start_day
    while len(out) < n:
        if person["commuter"]:
            wd = day % 7 < 5
            if wd:
                if rng.random() < 0.85: out.append(day * 24 + rng.normal(8.0, 0.5))
                if rng.random() < 0.15:
                    a = day * 24 + rng.uniform(11, 14); out += [a, a + rng.uniform(0.5, 2.0)]
                if rng.random() < 0.85: out.append(day * 24 + rng.normal(18.5, 0.7))
            elif rng.random() < 0.3:
                a = day * 24 + rng.uniform(10, 20); out.append(a)
                if rng.random() < 0.5: out.append(a + rng.uniform(1, 4))
            day += 1
        else:
            a = day * 24 + rng.uniform(10, 22); out.append(a)
            if rng.random() < 0.3: out.append(a + rng.uniform(0.5, 2.0))
            day += int(rng.integers(1, 6))
    return sorted(out)[:n]


# ---------------------------------------------------------------- Oman, 초기 S 지원
def lp2_init(x, beta, dt, y0):
    al = math.exp(-dt / beta)
    f = lambda u: lfilter([1.0 - al], [1.0, -al], u, zi=[al * y0])[0]
    return f(f(x))


def oman_run(c, K, dt, S0=0.0):
    """oman.run 과 같되 K 가 배열이어도 되고(시간 가변 이득) 느린 상태를 S0 에서 시작할 수 있다."""
    S = lp2_init(c, oman.OMAN["beta_slow_s"], dt, S0)
    F = oman.lp2(K * S * c, oman.OMAN["beta_fast_s"], dt)
    return np.maximum(F + S, 0.0) ** oman.OMAN["exponent"], S


# ---------------------------------------------------------------- 승객 상태 (10초 스텝)
def visual_states(pi, m, rng):
    W = WORLD; dwell = [U(rng, W["dwell_s"][s]) for s in STATES]
    st = np.empty(m, np.int8); s = int(rng.choice(5, p=pi)); left = rng.exponential(dwell[s])
    for i in range(m):
        st[i] = s; left -= STEP
        if left <= 0:
            q = pi.copy(); q[s] = 0.0
            s = int(rng.choice(5, p=q / q.sum())) if q.sum() > 1e-9 else s
            left = rng.exponential(dwell[s])
    return st


def phone_signals(st, screen, dots_on, rng):
    """상태(와 실제 화면 상태) → (app_fg, rx_kBs, media, landscape) 스텝마다."""
    P = WORLD["phone"]; m = len(st); r = rng.random((m, 5))
    media = r[:, 1] < np.array(P["media_p"])[st]
    land = screen & (r[:, 2] < np.array(P["landscape_p"])[st])
    rx = np.array(P["rx_kBs_median"])[st] * np.exp(rng.normal(0, 1, m) * np.array(P["rx_log_sd"])[st])
    burst = (st == TEXT) & (r[:, 3] < P["text_burst_p"]); rx = np.where(burst, rx + P["text_burst_kBs"] * rng.random(m), rx)
    app = screen & (r[:, 4] < np.where(dots_on, P["app_fg_dots_p"], P["app_fg_screen_p"]))
    return app, rx, media, land


def ema(x, alpha):
    y = np.empty_like(x, dtype=float); y[0] = x[0]
    for i in range(1, len(x)):
        y[i] = y[i - 1] + alpha * (x[i] - y[i - 1])
    return y


def roll_mean(x, w):
    """뒤쪽 w개(자기 포함) 평균, 앞부분은 있는 만큼."""
    c = np.cumsum(np.concatenate([[0.0], x.astype(float)])); n = len(x); i = np.arange(1, n + 1); j = np.maximum(i - w, 0)
    return (c[i] - c[j]) / (i - j)


def switch_count_5m(a_long, emit):
    """가속(> +0.5) 뒤 1초 안에 감속(< −0.5)으로 직행한 횟수 — 최근 5분, 방출 시점마다."""
    S = WORLD["switch"]; hi = a_long > S["hi"]; lo = a_long < S["lo"]
    on = np.flatnonzero(lo & ~np.concatenate([[False], lo[:-1]]))
    chi = np.cumsum(hi); win = int(S["within_s"] * FS)
    ev = [i for i in on if i > 0 and chi[i - 1] - (chi[i - win - 1] if i - win - 1 >= 0 else 0) > 0]
    cnt = np.zeros(len(a_long), int); cnt[ev] = 1; cc = np.cumsum(cnt); w5 = int(300 * FS)
    return np.array([cc[e] - (cc[e - w5] if e - w5 >= 0 else 0) for e in emit], float)


# ---------------------------------------------------------------- 운동학: 펌프질 운전자, EV 회생 lift-off
LIFTOFF = {"p": {1: 0.25, 2: 0.6, 3: 0.6}, "accel_mps2": {1: 0.6, 2: 1.0, 3: 1.3}, "dur_s": [0.8, 1.5], "recover_s": [2.0, 4.0], "gap_s": 4.0}


def ev_liftoff(route, motion, level, rng):
    """EV 회생(단계 1~3): 가속이 끝나는 순간 액셀을 떼면 바로 회생 감속이 온다 → 가속→감속 직행. 속도 프로파일을 고쳐 운동학 전부를 다시 계산."""
    if level is None or level not in LIFTOFF["p"]:
        return motion
    v0 = motion["v"]; a = motion["a_long"]; n = len(v0)
    ends = np.flatnonzero((a[:-1] >= 0.5) & (a[1:] < 0.5)); pulse = np.zeros(n); last = -10 ** 9
    for i in ends:
        if i - last < LIFTOFF["gap_s"] * FS or rng.random() >= LIFTOFF["p"][level]:
            continue
        last = i; ar = LIFTOFF["accel_mps2"][level] * rng.uniform(0.8, 1.2); d1 = int(U(rng, LIFTOFF["dur_s"]) * FS); d2 = int(U(rng, LIFTOFF["recover_s"]) * FS)
        pulse[i:i + d1] -= ar; pulse[i + d1:i + d1 + d2] += ar * d1 / d2
    if not pulse.any():
        return motion
    v = np.maximum(v0 + np.cumsum(pulse) * DT, 0.0)
    s_grid = np.arange(len(route["kappa"])) * sim.DS; th_s = np.arctan(route["grade"])
    kap0 = motion["kappa"]; sway = motion["a_lat"] - v0 ** 2 * kap0
    st = np.cumsum(v) * DT; kap = np.interp(st, s_grid, route["kappa"]); theta = np.interp(st, s_grid, th_s)
    sway = sway * np.where(v0 > 0.5, v / np.maximum(v0, 0.5), 1.0)
    return {"t": motion["t"], "v": v, "s": st, "a_long": np.gradient(v, DT), "a_lat": v ** 2 * kap + sway, "kappa": kap,
            "yaw_rate": v * kap + sway / np.maximum(v, 1.0), "theta": theta, "a_vert": v ** 2 * np.interp(st, s_grid, np.gradient(th_s, sim.DS))}


def make_motion(route, driver, regen_level, rng, pumping=False):
    """sim.drive + 펌프질 운전(짧고 큰 속도 흔들림) + EV lift-off."""
    if pumping:
        keep = {k: sim.CFG["driver"][k] for k in PUMP_CFG}; sim.CFG["driver"].update(PUMP_CFG)
        try:
            motion = sim.drive(route, driver, regen_level, rng)
        finally:
            sim.CFG["driver"].update(keep)
    else:
        motion = sim.drive(route, driver, regen_level, rng)
    return ev_liftoff(route, motion, regen_level, rng)


# ---------------------------------------------------------------- 탑승 하나
def simulate_ride(person, route, driver, ride, rng, start_h, rides_with_vehicle, tmpdir, motion=None, habit=None):
    """→ 레코드 dict (행 = 10초 스텝). person 의 rides_done/last_end_h/S_end/aware 를 갱신한다 (호출자가 사람을 순서대로 돌려야 함)."""
    W = WORLD; H = HABIT[habit or person["habit"]]; C = tripio.COL
    if motion is None:
        motion = make_motion(route, driver, ride["regen_level"], rng, person["pumping"])
    rows, mult = sim.ride_rows(route, motion, ride, rng)
    td = pathlib.Path(tmpdir); tp = td / "trip.bin"; tripio.write_trip(tp, rows)
    t, Xv = tripio.featurize(tp, td / "features.bin")
    emit = np.arange(999, len(rows), 1000); assert len(emit) == len(t); m = len(t); n100 = len(rows)
    t100 = motion["t"]

    # ---- 탑승 맥락
    gap = None if person["last_end_h"] is None else max(start_h - person["last_end_h"], 0.0)
    S0 = person["S_end"] * U(rng, W["carry_frac"]) if (gap is not None and gap <= W["carry_gap_h"]) else 0.0
    mood = rng.normal(0, W["mood_sd"]); display_on = rng.random() < W["display_on_p"]; phone_off = rng.random() < W["phone_off_p"]
    for d in HEADS:                                                   # 모르던 장치를 이번에 알게 됨
        if not person["aware"][d] and rng.random() < W["discover_p"]:
            person["aware"][d] = True
    pi_ride = rng.dirichlet(np.maximum(person["pi"] * W["visual_ride_conc"], 0.05))
    st = visual_states(pi_ride, m, rng)
    screen_true = rng.random(m) < np.array(W["phone"]["screen_p"])[st]      # 실제 화면 상태 (도트 자격·효과용)

    # ---- K(t): 체질 × 좌석/온도/공조 × 시각 과제 배율 (1~2분 램프)
    K0 = sim.oman.OMAN["K0"] * person["true_sens"] * mult
    mv = np.ones(m); mv[st == VIDEO] = person["mult"]["video"]; mv[st == TEXT] = person["mult"]["text"]; mv[st == GAME] = person["mult"]["game"]
    mv = ema(mv, min(1.0, STEP / person["mult_tau"]))
    K100 = K0 * np.interp(t100, t, mv, left=mv[0], right=mv[-1])
    c = oman.input_signal(motion["a_long"], motion["a_lat"], DT)
    L = sim.CFG["label"]; block = (t // L["noise_block_s"]).astype(int); noise = rng.normal(0, L["noise_sd"], block.max() + 1)[block]

    def misc_of(Kt):
        score, S = oman_run(c, Kt, DT, S0)
        return score, S, np.clip(np.round(score[emit] + noise), 0, 10)
    score, S, misc = misc_of(K100)

    # ---- 습관 → 유효 문턱 (스텝마다)
    temp = np.nan_to_num(rows[emit, C["cabin_temp"]], nan=24.0)
    sp1 = np.nan_to_num(Xv[:, FIDX_SPEED_1M], nan=0.0) / sim.KMH
    habituation = min(HABIT_CAP, person["habit_rate"] * rides_with_vehicle)
    th = {"fan": person["theta"]["fan"] + mood + habituation - person["heat"] * np.maximum(temp - 24.0, 0),
          "ambient": person["theta"]["ambient"] + mood + habituation - person["highway"] * (sp1 >= HIGHWAY_KMH),
          "dots": person["theta"]["dots"] + mood + habituation - person["screen"] * screen_true - person["game"] * (st == GAME)}
    gate = {"fan": np.ones(m, bool), "ambient": np.full(m, bool(display_on)), "dots": screen_true & (not phone_off)}

    # ---- 사건 → 개입 효과 → MISC 다시 (선풍기 → 주변 신호 → 도트)
    screen100 = np.interp(t100, t, screen_true.astype(float), left=float(screen_true[0]), right=float(screen_true[-1])) >= 0.5
    eff100 = np.ones(n100); t_on = {}
    for d in HEADS:
        if not person["aware"][d]:
            continue
        hit = np.flatnonzero(gate[d] & (misc >= th[d]))
        if not len(hit):
            continue
        t_on[d] = float(t[hit[0]])
        ramp = np.clip((t100 - t_on[d]) / person["effect_tau"], 0, 1); e = 1.0 - (1.0 - person["effect"][d]) * ramp
        if d == "dots":
            e = np.where(screen100, e, 1.0)
        eff100 = eff100 * e
        score, S, misc = misc_of(K100 * eff100)

    # ---- 라벨: y_k (최종 MISC) 와 반응 라벨 Y/W
    h = int(HORIZON / STEP)
    fut = np.array([misc[j + 1:j + 1 + h].max() if j + h < m else np.nan for j in range(m)])
    valid = np.arange(m) + h < m
    yk = np.column_stack([(fut >= k).astype(np.float32) for k in L["k"]])
    Y = np.zeros((m, 3), np.int8); Wt = np.zeros((m, 3), np.float32)
    for j, d in enumerate(HEADS):
        w = gate[d].astype(np.float32); y = np.zeros(m, np.int8)
        if d in t_on:
            y[(t < t_on[d]) & (t + HORIZON >= t_on[d])] = 1; w[t >= t_on[d]] = 0.0
        Y[:, j] = y; Wt[:, j] = w

    # ---- 새 피처 16개
    dots_on = (t >= t_on["dots"]) if "dots" in t_on else np.zeros(m, bool)
    screen = screen_true; app, rx, media, land = phone_signals(st, screen, dots_on, rng)
    hp = (np.isin(st, [MUSIC, VIDEO, GAME]) & (rng.random(m) < person["headphone_p"])) | (rng.random(m) < 0.1 * person["headphone_p"])
    w5 = int(300 / STEP)
    newX = np.full((m, len(NEW)), np.nan, np.float32)
    if not phone_off:
        newX[:, 0] = roll_mean(screen, w5); newX[:, 1] = roll_mean(app, w5); newX[:, 2] = roll_mean(np.log10(1 + rx), w5)
        newX[:, 3] = roll_mean(media, w5); newX[:, 4] = hp; newX[:, 5] = roll_mean(land, w5)
    for j, d in enumerate(HEADS):
        newX[:, 6 + j] = np.maximum(t - t_on[d], 0) / 60.0 if d in t_on else 0.0
    ons = np.sort([v for v in t_on.values()])
    newX[:, 9] = np.searchsorted(ons, t, side="right")
    last = np.full(m, np.nan)
    for v in ons:
        last[t >= v] = v
    newX[:, 10] = (t - last) / 60.0
    tod = (start_h + t / 3600.0) % 24.0; newX[:, 11] = np.sin(2 * np.pi * tod / 24); newX[:, 12] = np.cos(2 * np.pi * tod / 24)
    newX[:, 13] = np.nan if gap is None else gap
    newX[:, 14] = rides_with_vehicle
    newX[:, 15] = switch_count_5m(motion["a_long"], emit)

    X5 = tripio.full_vector(Xv, person["mssq"], person["sens"], person["ev_experience"], person["vehicle_type"])
    X = np.hstack([X5[:, KEEP5], newX]).astype(np.float32)

    # ---- 사람 상태 갱신
    minutes = n100 / FS / 60
    person["rides_done"] += 1; person["last_end_h"] = start_h + minutes / 60; person["S_end"] = float(S[-1])
    v = valid
    return {"t": t[v], "X": X[v], "score": score[emit][v], "misc": misc[v], "future_max": fut[v], "y": yk[v], "Y": Y[v], "W": Wt[v],
            "state": st[v], "n": int(v.sum()),
            "meta": {"person_id": person["pid"], "vehicle_type": person["vehicle_type"], "regen_level": ride["regen_level"], "seat_row": ride["seat_row"],
                     "m": mult, "K0": K0, "true_sens": person["true_sens"], "sens": person["sens"], "mssq": person["mssq"], "minutes": minutes,
                     "max_misc": float(misc.max()), "start_h": start_h, "gap_h": gap, "S0": S0, "mood": mood, "display_on": bool(display_on),
                     "phone_off": bool(phone_off), "rides_with_vehicle": int(rides_with_vehicle), "t_on": t_on, "visual_type": person["visual_type"],
                     "state_share": [float((st == s).mean()) for s in range(5)], "commuter": person["commuter"], "pumping": person["pumping"]}}


def person_public(p):
    """rides.json 에 남길 사람 정보 (숨은 성질 포함 — 분석용, 모델엔 안 들어감)."""
    return {k: (v.tolist() if isinstance(v, np.ndarray) else v) for k, v in p.items() if k not in ("rides_done", "last_end_h", "S_end")}


def pack(records, extra_keys=()):
    """레코드 목록 → npz 용 dict."""
    cat = lambda k: np.concatenate([r[k] for r in records])
    rep = lambda k: np.concatenate([np.full(r["n"], r[k]) for r in records])
    data = {"X": cat("X"), "y": cat("y"), "Y": cat("Y"), "W": cat("W"), "t": cat("t"), "misc": cat("misc"), "score": cat("score"),
            "future_max": cat("future_max"), "state": cat("state"),
            "ride_id": rep("ride_id"), "trip_id": rep("trip_id"), "person_id": rep("person_id"), "ride_order": rep("ride_order"),
            "k": np.array(sim.CFG["label"]["k"]), "feature_names": np.array(NAMES), "feature_groups": np.array(GROUPS),
            "feature_version": np.array("6exp"), "heads": np.array(HEADS)}
    for k in extra_keys:
        data[k] = rep(k)
    return data


def group_dropout(X, ride_id, rng, cell=None):
    """v6 판 그룹 드롭아웃 (탑승 단위 그룹 + 칸 2%). 원본은 두고 복사본을 돌려준다."""
    X = X.copy(); rides, inv = np.unique(ride_id, return_inverse=True)
    for g, p in GROUP_DROPOUT.items():
        if p <= 0:
            continue
        cols = [i for i in range(NF) if GROUPS[i] == g and DROPPABLE[i]]
        drop = rng.random(len(rides)) < p; X[np.ix_(drop[inv], cols)] = np.nan
    cell = tripio.FEATURES["cell_dropout"] if cell is None else cell
    cols = [i for i in range(NF) if DROPPABLE[i] and GROUPS[i] not in ("G1", "G9")]
    mask = rng.random((len(X), len(cols))) < cell; sub = X[:, cols]; sub[mask] = np.nan; X[:, cols] = sub
    return X


def feature_doc():
    return {"version": "6exp", "n_features": NF, "base": "feature_order v5 minus G8", "groups_new": GROUP_DESC,
            "features": [{"index": i, "name": NAMES[i], "group": GROUPS[i], "monotone": MONOTONE[i], "enabled": ENABLED[i]} for i in range(NF)]}


if __name__ == "__main__":
    print(json.dumps(feature_doc(), indent=1, ensure_ascii=False))
