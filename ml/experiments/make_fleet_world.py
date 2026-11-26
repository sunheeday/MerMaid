"""플릿 세계 만들기 — 차 K대, 차마다 설정·가족(고정 승객)·센서 구성이 다르고, 탑승은 시간순.

    python3 ml/experiments/make_fleet_world.py [--vehicles 20] [--trips-per-vehicle 30] [--seed 5] [--jobs 8]
        → ml/out/bulk_world/fleet.npz

차마다 베이스 설정(sim_config) 주위에서 뽑는 것:
  운전 거칠기 배율 U(0.8, 1.4), 속도 흔들림 U(0.04, 0.08), IMU 잡음 U(0.04, 0.10), 진동 U(0.015, 0.035)/U(0.003, 0.007),
  EV 여부(차 단위 고정, p=0.6), 실내 설정 온도 이동 U(-2, +3) ℃, 탑승 길이 배율 U(0.6, 1.2),
  센서 결측(차 단위 고정): G4 없음 p=0.5, G7 없음 p=0.4, G3a 없음 p=0.3.
가족 3~5명(차 단위 고정, person_id 20000 + 차번호*10 + i), 탑승마다 그중 최대 4명이 탄다.
출력 키: dataset.npz 와 같은 키 + vehicle_id, trip_order(차 안에서 0부터 시간순), veh_cfg(json), world="fleet".
"""
import argparse
import copy
import json
import multiprocessing as mp
import pathlib
import sys
import time

import numpy as np

ML = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ML)); sys.path.insert(0, str(ML / "simulator")); sys.path.insert(0, str(ML / "train"))
import sim  # noqa: E402
import build_dataset as bd  # noqa: E402
import trip as tripio  # noqa: E402
import common as C  # noqa: E402

BASE_CFG = copy.deepcopy(sim.CFG)
MISS_P = (("G4", 0.5), ("G7", 0.4), ("G3a", 0.3))


def draw_vehicle(rng, v):
    d = BASE_CFG["driver"]; s = rng.uniform(0.8, 1.4)
    cfg = {"driver": {"accel_mps2": [d["accel_mps2"][0] * s, d["accel_mps2"][1] * s], "decel_mps2": [d["decel_mps2"][0] * s, d["decel_mps2"][1] * s],
                      "lat_accel_mps2": [d["lat_accel_mps2"][0] * s, d["lat_accel_mps2"][1] * s], "speed_wobble": float(rng.uniform(0.04, 0.08))},
           "imu": {"accel_noise": float(rng.uniform(0.04, 0.10)), "vib_base": float(rng.uniform(0.015, 0.035)), "vib_per_mps": float(rng.uniform(0.003, 0.007))},
           "ev_share": {"value": float(rng.random() < 0.6)},
           "cabin": {"set_c": [BASE_CFG["cabin"]["set_c"][0] + (dc := float(rng.uniform(-2, 3))), BASE_CFG["cabin"]["set_c"][1] + dc],
                     "start_c": [BASE_CFG["cabin"]["start_c"][0] + dc, BASE_CFG["cabin"]["start_c"][1] + dc]},
           "trip_minutes": {"range": [BASE_CFG["trip_minutes"]["range"][0] * (tm := float(rng.uniform(0.6, 1.2))), BASE_CFG["trip_minutes"]["range"][1] * tm]}}
    missing = [g for g, p in MISS_P if rng.random() < p]
    return {"vehicle": v, "drive_scale": s, "cfg": cfg, "missing": missing, "family": int(rng.integers(3, 6))}


def deep_update(d, u):
    for k, val in u.items():
        if isinstance(val, dict) and isinstance(d.get(k), dict):
            deep_update(d[k], val)
        else:
            d[k] = val


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vehicles", type=int, default=20); ap.add_argument("--trips-per-vehicle", type=int, default=30)
    ap.add_argument("--seed", type=int, default=5); ap.add_argument("--jobs", type=int, default=min(8, mp.cpu_count()))
    ap.add_argument("--out", default=str(ML / "out" / "bulk_world" / "fleet.npz"))
    a = ap.parse_args()
    out = pathlib.Path(a.out); out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        sys.exit(f"{out} 이미 있음 (덮어쓰지 않음)")
    try:
        mp.set_start_method("fork")
    except RuntimeError:
        pass
    rng = np.random.default_rng(a.seed); t0 = time.time(); rides_all = []; veh_cfg = []
    for v in range(a.vehicles):
        vc = draw_vehicle(rng, v); veh_cfg.append(vc)
        sim.CFG.clear(); sim.CFG.update(copy.deepcopy(BASE_CFG)); deep_update(sim.CFG, vc["cfg"])   # 워커는 fork 로 이 상태를 물려받는다
        fam = [sim.gen_passenger(rng, 20000 + v * 10 + i) for i in range(vc["family"])]
        k = min(4, len(fam))
        tasks = [(v * 1000 + i, a.seed, [fam[j] for j in rng.choice(len(fam), k, replace=False)], 0) for i in range(a.trips_per_vehicle)]
        with mp.Pool(a.jobs) as pool:
            rides = [r for chunk in pool.imap(bd.one_trip, tasks, chunksize=2) for r in chunk]
        for r in rides:
            r["vehicle_id"] = v; r["trip_order"] = r["trip_id"] - v * 1000
        rides_all += rides
        print(f"차 {v:2d}: 가족 {len(fam)}명, 탑승 {len(rides)}, 운전 배율 {vc['drive_scale']:.2f}, EV {int(vc['cfg']['ev_share']['value'])}, 결측 {vc['missing'] or '-'} ({time.time() - t0:.0f}s)", flush=True)
    # ride_id 를 전역에서 유일하게 다시 매김 (trip 당 탑승 수가 달라 one_trip 의 번호가 겹칠 수 있음)
    rides_all.sort(key=lambda r: (r["vehicle_id"], r["trip_order"], r["person_id"]))
    for i, r in enumerate(rides_all):
        r["ride_id"] = i
    cat = lambda key: np.concatenate([r[key] for r in rides_all])
    rep = lambda key: np.concatenate([np.full(len(r["t"]), r[key]) for r in rides_all])
    X = cat("X")
    # 차 단위 센서 결측
    veh = rep("vehicle_id")
    for vc in veh_cfg:
        for g in vc["missing"]:
            X[np.ix_(veh == vc["vehicle"], C.GROUPS[g])] = np.nan
    data = {"X": X, "y": cat("y"), "t": cat("t"), "misc": cat("misc"), "score": cat("score"), "future_max": cat("future_max"),
            "ride_id": rep("ride_id"), "trip_id": rep("trip_id"), "person_id": rep("person_id"), "vehicle_id": veh, "trip_order": rep("trip_order"),
            "k": np.array(sim.CFG["label"]["k"]), "feature_names": np.array([f["name"] for f in bd.FEAT]),
            "feature_version": np.array(tripio.FEATURES["version"]), "world": np.array("fleet"), "veh_cfg": np.array(json.dumps(veh_cfg))}
    np.savez_compressed(out, **data)
    print(f"플릿 세계: 차 {a.vehicles}, 탑승 {len(rides_all)}, 행 {len(data['t'])}, 사람 {len(np.unique(data['person_id']))}, {time.time() - t0:.0f}s → {out}")


if __name__ == "__main__":
    main()
