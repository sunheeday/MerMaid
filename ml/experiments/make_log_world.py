"""로그 세계 만들기 — 베이스 세계(기본 sim_config)와 다른 설정으로 합성 로그를 한 벌 더 (노트북 15~25분)

    python3 ml/experiments/make_log_world.py [--trips 240] [--passengers 60] [--seed 1] [--jobs 8]

베이스 세계 = repo 기본 시뮬레이터(dataset.npz). 로그 세계 = 우리 차·우리 승객. 다른 점:
  운전이 더 거칠고(가감속·횡가속 범위 ↑), 센서 잡음·진동 ↑, EV 비율 ↑, 실내 더 더움, 탑승 짧음, 승객 풀이 다름(새 사람).
같은 시뮬레이터 코드를 쓰되 sim.CFG 만 덮어쓴다(피처는 같은 featurize). 사람(person_id)은 여러 탑승에 반복해서 탄다.
출력: ml/out/bulk_world/log_dataset.npz  (dataset.npz 와 같은 키 + world="log")
"""
import argparse
import multiprocessing as mp
import pathlib
import sys
import time

import numpy as np

ML = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ML)); sys.path.insert(0, str(ML / "simulator"))
import sim  # noqa: E402
import build_dataset as bd  # noqa: E402
import trip as tripio  # noqa: E402

# 로그 세계의 차이 — 전부 "그럴 법한" 방향으로, 세기는 설계값
OVERRIDE = {
    "driver": {"accel_mps2": [0.9, 2.6], "decel_mps2": [0.7, 2.2], "lat_accel_mps2": [1.8, 4.2], "speed_factor": [0.95, 1.25], "speed_wobble": 0.07},
    "imu": {"accel_noise": 0.09, "vib_base": 0.03, "vib_per_mps": 0.006},
    "ev_share": {"value": 0.85},
    "cabin": {"set_c": [22, 28], "start_c": [20, 38]},
    "trip_minutes": {"range": [10, 30]},
}


def deep_update(d, u):
    for k, v in u.items():
        if isinstance(v, dict) and isinstance(d.get(k), dict):
            deep_update(d[k], v)
        else:
            d[k] = v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trips", type=int, default=240); ap.add_argument("--passengers", type=int, default=60)
    ap.add_argument("--seed", type=int, default=1); ap.add_argument("--jobs", type=int, default=min(8, mp.cpu_count()))
    ap.add_argument("--out", default=str(ML / "out" / "bulk_world" / "log_dataset.npz"))
    a = ap.parse_args()
    out = pathlib.Path(a.out); out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        sys.exit(f"{out} 이미 있음 (덮어쓰지 않음)")
    deep_update(sim.CFG, OVERRIDE)                      # fork 로 띄우는 워커가 이 상태를 물려받는다
    try:
        mp.set_start_method("fork")
    except RuntimeError:
        pass
    rng = np.random.default_rng(a.seed)
    pax = [sim.gen_passenger(rng, 10_000 + i) for i in range(a.passengers)]   # 베이스 세계와 겹치지 않는 사람 ID
    k = sim.CFG["scale"]["riders_per_trip"]
    tasks = [(tid, a.seed, [pax[i] for i in rng.choice(a.passengers, k, replace=False)], 0) for tid in range(a.trips)]
    t0 = time.time()
    with mp.Pool(a.jobs) as pool:
        rides = [r for chunk in pool.imap(bd.one_trip, tasks, chunksize=2) for r in chunk]
    rides.sort(key=lambda r: r["ride_id"])
    cat = lambda key: np.concatenate([r[key] for r in rides])
    rep = lambda key: np.concatenate([np.full(len(r["t"]), r[key]) for r in rides])
    data = {"X": cat("X"), "y": cat("y"), "t": cat("t"), "misc": cat("misc"), "score": cat("score"),
            "future_max": cat("future_max"), "ride_id": rep("ride_id"), "trip_id": rep("trip_id"), "person_id": rep("person_id"),
            "k": np.array(sim.CFG["label"]["k"]), "feature_names": np.array([f["name"] for f in bd.FEAT]),
            "feature_version": np.array(tripio.FEATURES["version"]), "world": np.array("log")}
    np.savez_compressed(out, **data)
    pid, cnt = np.unique([r["person_id"] for r in rides], return_counts=True)
    print(f"로그 세계: 탑승 {len(rides)}, 행 {len(data['t'])}, 사람 {len(pid)} (사람당 탑승 평균 {cnt.mean():.1f}), {time.time() - t0:.0f}s → {out}")


if __name__ == "__main__":
    main()
