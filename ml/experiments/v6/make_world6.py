"""5차 실험 세계 생성 — 베이스(학습) 세계와 플릿(채점) 세계, v6 피처 66개 + 반응 라벨.

    # 베이스: 사람 400명, 사람마다 자기 차(EV 60%)로 20탑승을 일정대로 (k=256 문맥이 차도록) (사람 단위 병렬)
    python3 ml/experiments/v6/make_world6.py base  --persons 400 --rides 20 --habit weak   --seed 0 --jobs 8 \
        --out ml/out/v6/base_weak.npz
    # 플릿: 차 20대 × 48탑승, 차마다 가족 3~5명·설정·센서 결측 (차 단위 병렬)
    python3 ml/experiments/v6/make_world6.py fleet --vehicles 20 --trips 48 --habit weak --seed 5 --jobs 8 \
        --out ml/out/v6/fleet_weak.npz

--habit off|weak|medium|strong : 상황 의존 습관의 세기 (world6.HABIT). 같은 --seed 면 습관 세기만 다른 쌍둥이 세계가 된다
  (사람·경로·운전은 같은 난수에서 나오지만, 사건이 달라지면 그 뒤 난수 소비가 달라져 완전히 같지는 않다).
출력: npz (키: X, y, Y, W, t, misc, score, future_max, state, ride_id, trip_id, person_id, ride_order[, vehicle_id, trip_order],
      feature_names, feature_groups, feature_version="6exp", heads, world, habit, veh_cfg) + 옆에 *_rides.json, *_persons.json, *_report.json
"""
import argparse
import copy
import json
import multiprocessing as mp
import pathlib
import sys
import tempfile
import time

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import world6 as W6  # noqa: E402
sim, tripio = W6.sim, W6.tripio

BASE_CFG = copy.deepcopy(sim.CFG)
FLEET_MISS = (("G5", 0.4), ("G6", 0.3), ("G2", 0.15))
RIDE_P = 0.7                      # 가족 구성원이 그 탑승에 타는 확률
_G = {"habit": "weak", "seed": 0, "rides": 12, "trips": 40}


def deep_update(d, u):
    for k, v in u.items():
        if isinstance(v, dict) and isinstance(d.get(k), dict):
            deep_update(d[k], v)
        else:
            d[k] = v


# ---------------------------------------------------------------- 베이스: 사람 하나 = 자기 차로 R 탑승
def one_person(pid):
    rng = np.random.default_rng([_G["seed"], pid])
    sim.CFG.clear(); sim.CFG.update(copy.deepcopy(BASE_CFG))
    p = W6.gen_person(rng, pid, _G["habit"])
    starts = W6.schedule(p, _G["rides"], rng)
    recs = []
    with tempfile.TemporaryDirectory() as td:
        for i, sh in enumerate(starts):
            if p["last_end_h"] is not None and sh < p["last_end_h"] + 0.25:      # 앞 탑승이 안 끝났으면 15분 뒤 출발
                sh = p["last_end_h"] + 0.25
            route = sim.gen_route(rng); driver = sim.gen_driver(rng); ride = sim.gen_ride(rng, p["vehicle_type"])
            r = W6.simulate_ride(p, route, driver, ride, rng, sh, rides_with_vehicle=i, tmpdir=td)
            r.update(ride_id=pid * 1000 + i, trip_id=pid * 1000 + i, person_id=pid, ride_order=i); r["meta"].update(ride_id=r["ride_id"], trip_id=r["trip_id"], ride_order=i)
            recs.append(r)
    return recs, W6.person_public(p)


# ---------------------------------------------------------------- 플릿: 차 하나 = 가족이 T 탑승을 시간순으로
def draw_vehicle(rng, v):
    d = BASE_CFG["driver"]; s = rng.uniform(0.8, 1.4); dc = float(rng.uniform(-2, 3)); tm = float(rng.uniform(0.6, 1.2))
    cfg = {"driver": {"accel_mps2": [d["accel_mps2"][0] * s, d["accel_mps2"][1] * s], "decel_mps2": [d["decel_mps2"][0] * s, d["decel_mps2"][1] * s],
                      "lat_accel_mps2": [d["lat_accel_mps2"][0] * s, d["lat_accel_mps2"][1] * s], "speed_wobble": float(rng.uniform(0.04, 0.08))},
           "imu": {"accel_noise": float(rng.uniform(0.04, 0.10)), "vib_base": float(rng.uniform(0.015, 0.035)), "vib_per_mps": float(rng.uniform(0.003, 0.007))},
           "cabin": {"set_c": [BASE_CFG["cabin"]["set_c"][0] + dc, BASE_CFG["cabin"]["set_c"][1] + dc],
                     "start_c": [BASE_CFG["cabin"]["start_c"][0] + dc, BASE_CFG["cabin"]["start_c"][1] + dc]},
           "trip_minutes": {"range": [BASE_CFG["trip_minutes"]["range"][0] * tm, BASE_CFG["trip_minutes"]["range"][1] * tm]}}
    return {"vehicle": v, "drive_scale": s, "cfg": cfg, "ev": int(rng.random() < 0.6), "missing": [g for g, p in FLEET_MISS if rng.random() < p],
            "family": int(rng.integers(3, 6)), "commuter": bool(rng.random() < W6.WORLD["commuter_p"]), "pumping": bool(rng.random() < W6.WORLD["pumping_p"])}


def one_vehicle(v):
    rng = np.random.default_rng([_G["seed"], 7, v])
    vc = draw_vehicle(rng, v)
    sim.CFG.clear(); sim.CFG.update(copy.deepcopy(BASE_CFG)); deep_update(sim.CFG, vc["cfg"])
    fam = [W6.gen_person(rng, 20000 + v * 10 + i, _G["habit"], vehicle_type=vc["ev"]) for i in range(vc["family"])]
    for p in fam:
        p["commuter"] = vc["commuter"]; p["pumping"] = vc["pumping"]
    starts = W6.schedule(fam[0], _G["trips"], rng)
    recs = []; count = {p["pid"]: 0 for p in fam}; last_end = None
    with tempfile.TemporaryDirectory() as td:
        for o, sh in enumerate(starts):
            if last_end is not None and sh < last_end + 0.25:
                sh = last_end + 0.25
            route = sim.gen_route(rng); driver = sim.gen_driver(rng)
            present = [p for p in fam if rng.random() < RIDE_P] or [fam[int(rng.integers(len(fam)))]]
            present = present[:4]
            regen = int(rng.integers(0, 4)) if vc["ev"] else None
            motion = None
            for p in present:
                ride = sim.gen_ride(rng, vc["ev"]); ride["regen_level"] = regen
                if motion is None:                                             # 같은 차·같은 운전: 운동학은 탑승 공유
                    motion = W6.make_motion(route, driver, regen, rng, vc["pumping"])
                r = W6.simulate_ride(p, route, driver, ride, rng, sh, rides_with_vehicle=count[p["pid"]], tmpdir=td, motion=motion)
                count[p["pid"]] += 1
                r.update(ride_id=0, trip_id=v * 1000 + o, person_id=p["pid"], ride_order=count[p["pid"]] - 1, vehicle_id=v, trip_order=o)
                r["meta"].update(trip_id=r["trip_id"], vehicle_id=v, trip_order=o, ride_order=r["ride_order"])
                recs.append(r)
            last_end = max(p["last_end_h"] for p in present)
    return recs, vc, [W6.person_public(p) for p in fam]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("kind", choices=["base", "fleet"])
    ap.add_argument("--persons", type=int, default=400); ap.add_argument("--rides", type=int, default=20)
    ap.add_argument("--vehicles", type=int, default=20); ap.add_argument("--trips", type=int, default=48)
    ap.add_argument("--habit", choices=list(W6.HABIT), default="weak"); ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--jobs", type=int, default=min(8, mp.cpu_count())); ap.add_argument("--out", required=True)
    ap.add_argument("--pid-offset", type=int, default=0, help="베이스 세계 사람 번호 시작 (추가 세계용)")
    a = ap.parse_args()
    out = pathlib.Path(a.out); out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        sys.exit(f"{out} 이미 있음 (덮어쓰지 않음)")
    _G.update(habit=a.habit, seed=a.seed, rides=a.rides, trips=a.trips)
    try:
        mp.set_start_method("fork")
    except RuntimeError:
        pass
    t0 = time.time(); stem = out.with_suffix("")
    if a.kind == "base":
        with mp.Pool(a.jobs) as pool:
            res = []
            for i, r in enumerate(pool.imap_unordered(one_person, range(a.pid_offset, a.pid_offset + a.persons)), 1):
                res.append(r)
                if i % 20 == 0 or i == a.persons:
                    print(f"  사람 {i}/{a.persons} ({time.time() - t0:.0f}s)", flush=True)
        recs = [x for rr, _ in res for x in rr]; persons = [p for _, p in res]; veh_cfg = []
        recs.sort(key=lambda r: r["ride_id"]); extra = ()
    else:
        with mp.Pool(a.jobs) as pool:
            res = []
            for i, r in enumerate(pool.imap_unordered(one_vehicle, range(a.vehicles)), 1):
                res.append(r); vc = r[1]
                print(f"  차 {vc['vehicle']:2d}: 가족 {vc['family']}, 탑승 {len(r[0])}, 운전 ×{vc['drive_scale']:.2f}, EV {vc['ev']}, 결측 {vc['missing'] or '-'}, "
                      f"{'출퇴근' if vc['commuter'] else '불규칙'}{', 펌프질' if vc['pumping'] else ''} ({i}/{a.vehicles}, {time.time() - t0:.0f}s)", flush=True)
        res.sort(key=lambda r: r[1]["vehicle"])
        recs = [x for rr, _, _ in res for x in rr]; veh_cfg = [vc for _, vc, _ in res]; persons = [p for _, _, pp in res for p in pp]
        recs.sort(key=lambda r: (r["vehicle_id"], r["trip_order"], r["person_id"]))
        for i, r in enumerate(recs):
            r["ride_id"] = i; r["meta"]["ride_id"] = i
        extra = ("vehicle_id", "trip_order")
    data = W6.pack(recs, extra)
    if a.kind == "fleet":                                                      # 차 단위 센서 결측
        for vc in veh_cfg:
            for g in vc["missing"]:
                cols = [i for i in range(W6.NF) if W6.GROUPS[i] == g]
                data["X"][np.ix_(data["vehicle_id"] == vc["vehicle"], cols)] = np.nan
    data.update(world=np.array(a.kind), habit=np.array(a.habit), veh_cfg=np.array(json.dumps(veh_cfg)))
    np.savez_compressed(out, **data)
    meta = [r["meta"] for r in recs]; Y, Wt = data["Y"], data["W"]
    report = {"kind": a.kind, "habit": a.habit, "seed": a.seed, "rides": len(recs), "rows": int(len(Y)), "persons": len(persons),
              "seconds": round(time.time() - t0, 1), "ride_minutes_mean": float(np.mean([m["minutes"] for m in meta])),
              "label_rate": {f"P(>={k})": float(data["y"][:, i].mean()) for i, k in enumerate(data["k"])},
              "event_rate_per_ride": {d: float(np.mean([d in m["t_on"] for m in meta])) for d in W6.HEADS},
              "Y_rate_eligible": {d: float(Y[Wt[:, j] > 0, j].mean()) for j, d in enumerate(W6.HEADS)},
              "eligible_share": {d: float((Wt[:, j] > 0).mean()) for j, d in enumerate(W6.HEADS)},
              "state_share": dict(zip(W6.STATES, np.mean([m["state_share"] for m in meta], 0).round(3).tolist())),
              "phone_off_rides": float(np.mean([m["phone_off"] for m in meta])),
              "max_misc_hist": {int(v): int(sum(m["max_misc"] == v for m in meta)) for v in sorted({m["max_misc"] for m in meta})}}
    pathlib.Path(f"{stem}_report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False))
    pathlib.Path(f"{stem}_rides.json").write_text(json.dumps(meta, indent=0, ensure_ascii=False, default=float))
    pathlib.Path(f"{stem}_persons.json").write_text(json.dumps(persons, indent=0, ensure_ascii=False, default=float))
    pathlib.Path(out.parent / "feature_order_v6exp.json").write_text(json.dumps(W6.feature_doc(), indent=1, ensure_ascii=False))
    print(json.dumps(report, indent=1, ensure_ascii=False)); print("→", out)


if __name__ == "__main__":
    main()
