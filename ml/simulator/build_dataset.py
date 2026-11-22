"""합성 학습 데이터 만들기 (S3)

    python3 ml/simulator/build_dataset.py [--note TEXT] [--trips 480] [--passengers 120] [--seed 0] [--keep-trips 8] [--jobs 8]

경로마다 승객 4명이 다른 좌석, 회생 설정, 공조로 탄다(도메인 랜덤화). 탑승마다:
trip.bin 작성 → C++ featurize(차량 피처 71개) → G8(v1 NaN), G10(승객) 붙이기 → Oman 라벨.
출력: 새 버전 폴더 ml/out/runs/vNNN/ (ml/runs.py): dataset.npz, dataset_report.json, rides.json,
      sim_config.json + sim_effects.json (이번에 쓴 설정 사본), trips/ride_XXXX.bin (재생용, 앞 --keep-trips개).
      --out을 주면 그 경로 (이미 있으면 거부, 덮어쓰지 않음)
설정은 ml/simulator/의 두 JSON. 환경 변수 MERMAID_SIM_DIR로 다른 폴더를 줄 수 있다 (sim.py).
"""
import argparse
import json
import multiprocessing as mp
import pathlib
import sys
import shutil
import tempfile
import time

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import runs  # noqa: E402
import sim  # noqa: E402
import trip as tripio  # noqa: E402

FEAT = tripio.FEATURES["features"]
NV = 71


def ride_record(ride_id, trip_id, pax, vtype, ride, motion, rows, mult, t, Xv, rng):
    """차량 피처(Xv) + 승객 피처 → 77개 X, Oman 라벨. 실차 변환(ml/realdata)도 같은 함수를 쓴다."""
    emit = np.arange(999, len(rows), 1000)
    assert len(emit) == len(t)
    K = sim.oman.OMAN["K0"] * pax["true_sens"] * mult
    score, misc, fut, y, valid = sim.label(motion, K, rng, emit)
    X = np.full((len(t), 77), np.nan)
    X[:, :NV] = Xv
    X[:, 73], X[:, 74], X[:, 75], X[:, 76] = pax["mssq"], pax["sens"], pax["ev_experience"], vtype
    for i, f in enumerate(FEAT):  # enabled=false (G8, SVC)는 NaN
        if not f["enabled"]:
            X[:, i] = np.nan
    v = valid
    return {
        "ride_id": ride_id, "trip_id": trip_id, "person_id": pax["pid"],
        "t": t[v], "X": X[v], "score": score[v], "misc": misc[v], "future_max": fut[v],
        "y": np.column_stack([y[k][v] for k in sim.CFG["label"]["k"]]),
        "meta": {"ride_id": ride_id, "trip_id": trip_id, "person_id": pax["pid"], "vehicle_type": vtype,
                 "regen_level": ride["regen_level"], "seat_row": ride["seat_row"], "m": mult, "K": K,
                 "true_sens": pax["true_sens"], "sens": pax["sens"], "mssq": pax["mssq"],
                 "minutes": len(rows) / sim.FS / 60, "max_misc": float(misc.max())},
    }


def one_trip(args):
    trip_id, seed, riders, keep, *rest = args  # rest: 재생용 trip.bin 폴더
    keep_dir = pathlib.Path(rest[0]) if rest else None
    rng = np.random.default_rng([seed, trip_id])
    route = sim.gen_route(rng)
    driver = sim.gen_driver(rng)
    vtype = int(rng.random() < sim.CFG["ev_share"]["value"])
    out = []
    with tempfile.TemporaryDirectory() as td:
        for r_i, pax in enumerate(riders):
            ride_id = trip_id * len(riders) + r_i
            ride = sim.gen_ride(rng, vtype)
            motion = sim.drive(route, driver, ride["regen_level"], rng)
            rows, mult = sim.ride_rows(route, motion, ride, rng)
            path = (keep_dir / f"ride_{ride_id:04d}.bin") if keep_dir and ride_id < keep else pathlib.Path(td) / "trip.bin"
            tripio.write_trip(path, rows)
            t, Xv = tripio.featurize(path, pathlib.Path(td) / "features.bin")
            out.append(ride_record(ride_id, trip_id, pax, vtype, ride, motion, rows, mult, t, Xv, rng))
    return out


def main():
    ap = argparse.ArgumentParser()
    sc = sim.CFG["scale"]
    ap.add_argument("--trips", type=int, default=sc["trips"])
    ap.add_argument("--passengers", type=int, default=sc["passengers"])
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--keep-trips", type=int, default=8)
    ap.add_argument("--jobs", type=int, default=mp.cpu_count())
    ap.add_argument("--out", help="버전 폴더 대신 이 경로에 (보정용 빠른 실행). 이미 있으면 거부")
    ap.add_argument("--note", default="", help="run.json에 남길 설명")
    a = ap.parse_args()
    if a.out:
        if pathlib.Path(a.out).exists():
            sys.exit(f"{a.out}가 이미 있음 (덮어쓰지 않음)")
        od = pathlib.Path(a.out).parent
        od.mkdir(parents=True, exist_ok=True)
    else:
        params = {k: v for k, v in vars(a).items() if k not in ("out", "note", "jobs")}
        od = runs.new("dataset", a.note, params={**params, "sim_dir": runs.rel(sim.SIM_DIR)})
        a.out = str(od / "dataset.npz")
        for f in ("sim_config.json", "sim_effects.json"):
            shutil.copy(sim.SIM_DIR / f, od / f)
    keep_dir = od / "trips"
    keep_dir.mkdir(exist_ok=True)

    rng = np.random.default_rng(a.seed)
    pax = [sim.gen_passenger(rng, i) for i in range(a.passengers)]
    k = sc["riders_per_trip"]
    tasks = [(tid, a.seed, [pax[i] for i in rng.choice(a.passengers, k, replace=False)], a.keep_trips, str(keep_dir))
             for tid in range(a.trips)]
    t0 = time.time()
    with mp.Pool(a.jobs) as pool:
        rides = [r for chunk in pool.imap(one_trip, tasks, chunksize=2) for r in chunk]
    rides.sort(key=lambda r: r["ride_id"])

    cat = lambda key: np.concatenate([r[key] for r in rides])
    rep = lambda key: np.concatenate([np.full(len(r["t"]), r[key]) for r in rides])
    data = {"X": cat("X"), "y": cat("y"), "t": cat("t"), "misc": cat("misc"), "score": cat("score"),
            "future_max": cat("future_max"), "ride_id": rep("ride_id"), "trip_id": rep("trip_id"),
            "person_id": rep("person_id"), "k": np.array(sim.CFG["label"]["k"]),
            "feature_names": np.array([f["name"] for f in FEAT]),
            "feature_version": np.array(tripio.FEATURES["version"])}
    np.savez_compressed(a.out, **data)

    meta = [r["meta"] for r in rides]
    y = data["y"]
    report = {
        "sim_dir": runs.rel(sim.SIM_DIR), "seed": a.seed, "trips": a.trips, "rides": len(rides), "passengers": a.passengers,
        "rows": int(len(y)), "seconds": round(time.time() - t0, 1),
        "ride_minutes_mean": float(np.mean([m["minutes"] for m in meta])),
        "label_rate": {f"P(>={k})": float(y[:, i].mean()) for i, k in enumerate(sim.CFG["label"]["k"])},
        "misc_hist": {int(v): int((data["misc"] == v).sum()) for v in np.unique(data["misc"])},
        "ride_max_misc_hist": {int(v): int(sum(m["max_misc"] == v for m in meta)) for v in sorted({m["max_misc"] for m in meta})},
        "ev_rides": int(sum(m["vehicle_type"] for m in meta)),
        "K_median": float(np.median([m["K"] for m in meta])),
    }
    stem = pathlib.Path(a.out).stem
    (od / ("dataset_report.json" if stem == "dataset" else f"{stem}_report.json")).write_text(json.dumps(report, indent=1))
    (od / ("rides.json" if stem == "dataset" else f"{stem}_rides.json")).write_text(json.dumps(meta, indent=0))
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
