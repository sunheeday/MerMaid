"""합성 학습 데이터 만들기 (S3)

    python3 ml/simulator/build_dataset.py [--trips 480] [--passengers 120] [--seed 0] [--keep-trips 8] [--jobs 8]

경로마다 승객 4명이 다른 좌석, 회생 설정, 공조로 탄다(도메인 랜덤화). 탑승마다:
trip.bin 작성 → C++ featurize(차량 피처 71개) → G8(v1 NaN), G10(승객) 붙이기 → Oman 라벨.
출력: ml/out/dataset.npz, ml/out/dataset_report.json, data/trips/ride_XXXX.bin (재생용, 앞 --keep-trips개)
"""
import argparse
import json
import multiprocessing as mp
import pathlib
import sys
import tempfile
import time

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import sim  # noqa: E402
import trip as tripio  # noqa: E402

FEAT = tripio.FEATURES["features"]
NV = 71
OUT = tripio.REPO / "ml" / "out"
TRIPS = tripio.REPO / "data" / "trips"


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
    trip_id, seed, riders, keep = args
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
            path = (TRIPS / f"ride_{ride_id:04d}.bin") if ride_id < keep else pathlib.Path(td) / "trip.bin"
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
    ap.add_argument("--out", default=str(OUT / "dataset.npz"))
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    TRIPS.mkdir(parents=True, exist_ok=True)

    rng = np.random.default_rng(a.seed)
    pax = [sim.gen_passenger(rng, i) for i in range(a.passengers)]
    k = sc["riders_per_trip"]
    tasks = [(tid, a.seed, [pax[i] for i in rng.choice(a.passengers, k, replace=False)], a.keep_trips)
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
        "seed": a.seed, "trips": a.trips, "rides": len(rides), "passengers": a.passengers,
        "rows": int(len(y)), "seconds": round(time.time() - t0, 1),
        "ride_minutes_mean": float(np.mean([m["minutes"] for m in meta])),
        "label_rate": {f"P(>={k})": float(y[:, i].mean()) for i, k in enumerate(sim.CFG["label"]["k"])},
        "misc_hist": {int(v): int((data["misc"] == v).sum()) for v in np.unique(data["misc"])},
        "ride_max_misc_hist": {int(v): int(sum(m["max_misc"] == v for m in meta)) for v in sorted({m["max_misc"] for m in meta})},
        "ev_rides": int(sum(m["vehicle_type"] for m in meta)),
        "K_median": float(np.median([m["K"] for m in meta])),
    }
    (OUT / "dataset_report.json").write_text(json.dumps(report, indent=1))
    (OUT / "rides.json").write_text(json.dumps(meta, indent=0))
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
