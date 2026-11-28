"""6차 실험 세계 (v7) — "2단계 어텐션이 언제 필요해지는가"를 보려고 world6 위에 **모드**를 얹은 세계.

사람마다 문턱 집합이 m개 있고(모드), 이번 탑승에 어느 집합이 쓰이는지는 **탑승 단위의 관측 가능한 상황**이 정한다.
상황 열쇠(--keys)는 전부 v5 피처(저장소 protocol/feature_order.json, 52개)로 보이는 것만 쓴다:
    route  도심 / 고속도로        → G2 속도 (speed_mean_5m, speed_max_5m, stop_ratio_5m)
    rough  부드러운 / 거친 운전    → G1 운동 (hard_*_count, rms, jerk)
    hot    보통 / 더운 실내        → G5 cabin_temp
    rear   앞좌석 / 뒷좌석         → G6 seat_row
모드 번호 = 열쇠 비트 (keys=route,rough 면 m=4: 0 도심·부드러움, 1 고속·부드러움, 2 도심·거침, 3 고속·거침).
모드별 문턱 = 사람 기본 문턱 + δ(사람, 모드, 장치), δ ~ N(0, mode_sd).  δ 가 **사람마다 다르므로** 피처만으로는 알 수 없고,
그 사람의 **같은 모드** 과거를 읽어야 맞출 수 있다 — 이것이 "상황이 닮은 과거를 골라 읽는" 2단계 어텐션이 필요해지는 조건이다.

세계 변수 (실험 설계 A1~A7)
    --modes m            모드 수 1/2/4 (= 2^len(keys); --keys 를 안 주면 route, rough 순서로 자동)
    --arrange            모드 배열  blocked(모드마다 한 덩어리, --blocks 로 덩어리 수)  iid(탑승마다 독립)  run:L(평균 L탑승 지속하는 마르코프)
    --rides n            사람당 탑승 수 (이력 길이) 20/60/200/400
    --theta-shift s      문턱 전체 이동 (− 면 사건이 잦아져 증거 밀도 ↑)
    --mode-sd            모드 간 문턱 차 (0 이면 모드가 있어도 문턱은 같음 = 대조군)
    --drift              none / slow(탑승마다 N(0, 0.05) 랜덤워크) / abrupt(중간 한 번 ±1 점프) — GRU 에 유리한 대조 조건
가시성(A6)·잡음 토큰(A5)은 세계가 아니라 context7 쪽 옵션(--hide, --neg-per-ride)으로 건다.

피처는 world6 과 같은 66개(v5 50 + 새 16)를 만들고, 학습 때 --input v5 로 v5 49개만 쓴다 (추가 열은 npz 에 남아 있다).
출력: npz (world6 키 + mode) + *_rides.json, *_persons.json, *_report.json

    python3 ml/experiments/v7/world7.py --persons 160 --rides 200 --modes 4 --arrange iid --seed 0 --jobs 8 --out ml/out/v7/m4_iid/base.npz
"""
import argparse
import copy
import json
import math
import multiprocessing as mp
import pathlib
import sys
import tempfile
import time

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "v6"))
import world6 as W6  # noqa: E402
sim, tripio = W6.sim, W6.tripio

BASE_CFG = copy.deepcopy(sim.CFG)
W6.HABIT["none"] = {"heat": 0.0, "highway": 0.0, "screen": 0.0, "game": 0.0, "habit_rate": 0.0}   # 상황 의존은 모드로만
KEYS_DEFAULT = ["route", "rough", "hot", "rear"]
ROUTE_W = {0: {"straight": 0.30, "stop": 0.30, "turn": 0.20, "s_curve": 0.10, "roundabout": 0.10, "hill": 0.00, "highway": 0.00},   # 도심
           1: {"straight": 0.15, "stop": 0.00, "turn": 0.00, "s_curve": 0.10, "roundabout": 0.00, "hill": 0.05, "highway": 0.70}}   # 고속
ROUGH_SCALE = {0: 0.8, 1: 1.5}                       # 운전자 가감속·횡가속 범위 배율
HOT_C = {0: ((20, 24), (15, 30)), 1: ((27, 31), (28, 38))}   # (set_c 범위, start_c 범위)
_G = {}


def mode_bits(mode, keys):
    return {k: (mode >> j) & 1 for j, k in enumerate(keys)}


def mode_sequence(n, m, rng, arrange, blocks=None):
    if m == 1:
        return np.zeros(n, np.int64)
    if arrange == "iid":
        return rng.integers(0, m, n)
    if arrange == "blocked":
        b = blocks or m; seq = np.concatenate([rng.permutation(m) for _ in range(math.ceil(b / m))])[:b]
        edges = np.linspace(0, n, b + 1).round().astype(int)
        return np.concatenate([np.full(edges[i + 1] - edges[i], seq[i]) for i in range(b)])
    if arrange.startswith("run:"):
        L = float(arrange[4:]); out = np.empty(n, np.int64); s = int(rng.integers(m))
        for i in range(n):
            out[i] = s
            if rng.random() < 1.0 / L:
                s = int((s + rng.integers(1, m)) % m)
        return out
    raise ValueError(arrange)


def drift_path(n, kind, rng):
    """탑승마다 기본 문턱에 더해지는 이동 (장치별)."""
    D = np.zeros((n, len(W6.HEADS)))
    if kind == "slow":
        D = np.cumsum(rng.normal(0, 0.05, (n, len(W6.HEADS))), 0)
    elif kind == "abrupt":
        j = int(rng.integers(int(0.3 * n), int(0.7 * n) + 1)); D[j:] = rng.choice([-1.0, 1.0], len(W6.HEADS))
    elif kind != "none":
        raise ValueError(kind)
    return D


def gen_person7(rng, pid, m, keys, mode_sd, theta_shift):
    p = W6.gen_person(rng, pid, habit="none")
    base = {d: p["theta"][d] + theta_shift for d in W6.HEADS}
    delta = rng.normal(0, mode_sd, (m, len(W6.HEADS))) if m > 1 else np.zeros((1, len(W6.HEADS)))
    delta -= delta.mean(0, keepdims=True)                                        # 사람 평균 문턱은 그대로, 모드 간 차이만
    p.update(theta_base=base, mode_delta=delta, n_modes=m, keys=keys)
    return p


def ride_setup(rng, p, mode, keys):
    """모드 비트에 따라 sim.CFG(경로·운전) 를 바꾸고 route/driver/ride 를 뽑는다."""
    bits = mode_bits(mode, keys); cfg = copy.deepcopy(BASE_CFG)
    if _G.get("minutes"):
        cfg["trip_minutes"]["range"] = list(_G["minutes"])
    if "route" in bits:
        cfg["segments"]["weights"] = dict(ROUTE_W[bits["route"]])
    if "rough" in bits:
        s = ROUGH_SCALE[bits["rough"]]
        for k_ in ("accel_mps2", "decel_mps2", "lat_accel_mps2"):
            cfg["driver"][k_] = [v * s for v in BASE_CFG["driver"][k_]]
    if "hot" in bits:
        cfg["cabin"]["set_c"], cfg["cabin"]["start_c"] = [list(v) for v in HOT_C[bits["hot"]]]
    sim.CFG.clear(); sim.CFG.update(cfg)
    route = sim.gen_route(rng); driver = sim.gen_driver(rng); ride = sim.gen_ride(rng, p["vehicle_type"])
    if "rear" in bits:
        ride["seat_row"] = float(bits["rear"])
    return route, driver, ride, bits


def one_person(pid):
    g = _G; rng = np.random.default_rng([g["seed"], pid]); m = g["modes"]; keys = g["keys"]
    sim.CFG.clear(); sim.CFG.update(copy.deepcopy(BASE_CFG))
    p = gen_person7(rng, pid, m, keys, g["mode_sd"], g["theta_shift"])
    starts = W6.schedule(p, g["rides"], rng)
    modes = mode_sequence(g["rides"], m, rng, g["arrange"], g["blocks"]); drift = drift_path(g["rides"], g["drift"], rng)
    recs = []
    with tempfile.TemporaryDirectory() as td:
        for i, sh in enumerate(starts):
            if p["last_end_h"] is not None and sh < p["last_end_h"] + 0.25:
                sh = p["last_end_h"] + 0.25
            mode = int(modes[i])
            p["theta"] = {d: max(W6.WORLD["theta"][d][2], p["theta_base"][d] + p["mode_delta"][mode, j] + drift[i, j]) for j, d in enumerate(W6.HEADS)}
            route, driver, ride, bits = ride_setup(rng, p, mode, keys)
            r = W6.simulate_ride(p, route, driver, ride, rng, sh, rides_with_vehicle=i, tmpdir=td, habit="none")
            r.update(ride_id=pid * 1000 + i, trip_id=pid * 1000 + i, person_id=pid, ride_order=i, mode=mode)
            r["meta"].update(ride_id=r["ride_id"], ride_order=i, mode=mode, bits=bits, theta=p["theta"], drift=drift[i].tolist())
            recs.append(r)
    pub = W6.person_public(p); pub["mode_delta"] = p["mode_delta"].tolist(); pub["modes"] = modes.tolist()
    return recs, pub


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--persons", type=int, default=160); ap.add_argument("--rides", type=int, default=200)
    ap.add_argument("--modes", type=int, default=4, choices=[1, 2, 4, 8, 16]); ap.add_argument("--keys", default=None, help="route,rough,hot,rear 중 (기본: 앞에서 log2(m)개)")
    ap.add_argument("--arrange", default="iid", help="blocked | iid | run:L"); ap.add_argument("--blocks", type=int, default=None)
    ap.add_argument("--mode-sd", type=float, default=1.0); ap.add_argument("--theta-shift", type=float, default=0.0)
    ap.add_argument("--drift", default="none", choices=["none", "slow", "abrupt"])
    ap.add_argument("--minutes", type=float, nargs=2, default=None, help="탑승 길이 범위 (분), 기본 sim_config 15~40")
    ap.add_argument("--seed", type=int, default=0); ap.add_argument("--jobs", type=int, default=min(8, mp.cpu_count()))
    ap.add_argument("--pid-offset", type=int, default=0); ap.add_argument("--out", required=True)
    a = ap.parse_args()
    nk = int(round(math.log2(a.modes))); keys = a.keys.split(",") if a.keys else KEYS_DEFAULT[:nk]
    assert len(keys) == nk and all(k in KEYS_DEFAULT for k in keys), (keys, a.modes)
    out = pathlib.Path(a.out); out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        sys.exit(f"{out} 이미 있음 (덮어쓰지 않음)")
    _G.update(seed=a.seed, rides=a.rides, modes=a.modes, keys=keys, arrange=a.arrange, blocks=a.blocks, mode_sd=a.mode_sd,
              theta_shift=a.theta_shift, drift=a.drift, minutes=a.minutes)
    try:
        mp.set_start_method("fork")
    except RuntimeError:
        pass
    t0 = time.time(); stem = out.with_suffix("")
    with mp.Pool(a.jobs) as pool:
        res = []
        for i, r in enumerate(pool.imap_unordered(one_person, range(a.pid_offset, a.pid_offset + a.persons)), 1):
            res.append(r)
            if i % 10 == 0 or i == a.persons:
                print(f"  사람 {i}/{a.persons} ({time.time() - t0:.0f}s)", flush=True)
    recs = [x for rr, _ in res for x in rr]; persons = [p for _, p in res]; recs.sort(key=lambda r: r["ride_id"])
    data = W6.pack(recs, ("mode",))
    data.update(world=np.array("v7"), habit=np.array("none"), veh_cfg=np.array("[]"),
                v7=np.array(json.dumps({k: (list(v) if isinstance(v, tuple) else v) for k, v in _G.items()})))
    np.savez_compressed(out, **data)
    meta = [r["meta"] for r in recs]; Y, Wt = data["Y"], data["W"]; md = data["mode"]
    per_mode = {}
    for mo in range(a.modes):
        ms = [m_ for m_ in meta if m_["mode"] == mo]; rows = md == mo
        per_mode[mo] = {"bits": mode_bits(mo, keys), "rides": len(ms), "minutes": round(float(np.mean([m_["minutes"] for m_ in ms])), 1) if ms else None,
                        "event_rate": {d: round(float(np.mean([d in m_["t_on"] for m_ in ms])), 3) for d in W6.HEADS} if ms else None,
                        "Y_rate": {d: round(float(Y[rows & (Wt[:, j] > 0), j].mean()), 4) for j, d in enumerate(W6.HEADS)} if rows.any() else None}
    report = {"kind": "v7", "args": {k: (list(v) if isinstance(v, tuple) else v) for k, v in _G.items()}, "persons": len(persons), "rides": len(recs), "rows": int(len(Y)),
              "seconds": round(time.time() - t0, 1), "ride_minutes_mean": float(np.mean([m_["minutes"] for m_ in meta])),
              "tokens_per_ride_est": float(np.mean([len(m_["t_on"]) for m_ in meta])) + 2 * len(W6.HEADS),
              "event_rate_per_ride": {d: float(np.mean([d in m_["t_on"] for m_ in meta])) for d in W6.HEADS},
              "Y_rate_eligible": {d: float(Y[Wt[:, j] > 0, j].mean()) for j, d in enumerate(W6.HEADS)},
              "eligible_share": {d: float((Wt[:, j] > 0).mean()) for j, d in enumerate(W6.HEADS)},
              "per_mode": per_mode,
              "mode_switch_share": float(np.mean([np.mean(np.diff(p["modes"]) != 0) for p in persons])) if a.rides > 1 else 0.0,
              "max_misc_hist": {int(v): int(sum(m_["max_misc"] == v for m_ in meta)) for v in sorted({m_["max_misc"] for m_ in meta})}}
    pathlib.Path(f"{stem}_report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False))
    pathlib.Path(f"{stem}_rides.json").write_text(json.dumps(meta, indent=0, ensure_ascii=False, default=float))
    pathlib.Path(f"{stem}_persons.json").write_text(json.dumps(persons, indent=0, ensure_ascii=False, default=float))
    print(json.dumps(report, indent=1, ensure_ascii=False)); print("→", out)


if __name__ == "__main__":
    main()
