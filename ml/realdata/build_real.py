"""실차 주행 + 합성 승객 → dataset.npz와 같은 모양의 데이터셋

    python3 ml/realdata/build_real.py --source comma2k19 --root data/raw/comma2k19/chunk1
    python3 ml/realdata/build_real.py --source baton --root data/raw/baton

주행(운동학)만 실제이고 나머지는 시뮬레이터와 같다: 주행마다 승객 4명(시뮬레이터와 같은 승객 풀,
같은 seed)이 각자 공조·좌석 설정(sim.gen_ride, sim.cabin_rows)으로 탄다. 라벨은 sim.label을
그대로 쓴다. 입력은 CAN 속도 미분(a_long)과 v·ω_z(a_lat).
데이터셋에 없는 칸(comma2k19: 페달·EV·내비 전부)은 NaN → featurize가 그 피처를 NaN으로 낸다.
출력: 새 버전 폴더 ml/out/runs/vNNN/ (ml/runs.py): real_<source>.npz, real_<source>_report.json,
      trips/real/<source>/*.bin (--keep-trips)
"""
import argparse
import json
import multiprocessing as mp
import pathlib
import sys
import tempfile

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import convert  # noqa: E402
import build_dataset  # noqa: E402  (convert가 ml/simulator를 sys.path에 넣는다)
import sim  # noqa: E402
import runs  # noqa: E402
import trip as tripio  # noqa: E402

READERS = {"comma2k19": "comma2k19", "baton": "baton"}


def one(args):
    src, route_dir, segs, trip_id, riders, seed, keep_dir = args
    reader = __import__(READERS[src])
    raw = reader.read(pathlib.Path(route_dir), segs)
    if raw is None:  # 너무 짧은 경로
        return None, []
    rows0, motion, info = convert.to_trip(raw)
    rng = np.random.default_rng([seed, trip_id])
    vtype = 0  # 두 데이터셋 모두 EV 신호(G3b)가 없다
    out = []
    with tempfile.TemporaryDirectory() as td:
        for r_i, pax in enumerate(riders):
            ride_id = trip_id * len(riders) + r_i
            ride = sim.gen_ride(rng, vtype)
            rows = rows0.copy()
            mult = sim.cabin_rows(rows, rows[:, tripio.COL["t"]], motion["v"], ride, rng)
            path = pathlib.Path(td) / "trip.bin"
            if keep_dir and r_i == 0:
                path = pathlib.Path(keep_dir) / f"{info['name']}.bin"
            tripio.write_trip(path, rows)
            t, Xv = tripio.featurize(path, pathlib.Path(td) / "features.bin")
            rec = build_dataset.ride_record(ride_id, trip_id, pax, vtype, ride, motion, rows, mult, t, Xv, rng)
            rec["meta"]["route"] = info["name"]
            emit = np.arange(999, len(rows), 1000)[:len(rec["t"])]
            for k_, x in motion["tags"].items():  # 주기마다 지난 1분 평균 (피처 1m 창과 같은 범위)
                cs = np.concatenate([[0.0], np.cumsum(x)])
                lo = np.maximum(emit + 1 - 6000, 0)
                rec["tag_" + k_] = (cs[emit + 1] - cs[lo]) / (emit + 1 - lo)
            out.append(rec)
    return info, out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=sorted(READERS), required=True)
    ap.add_argument("--root", required=True)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--passengers", type=int, default=sim.CFG["scale"]["passengers"])
    ap.add_argument("--keep-trips", action="store_true", help="주행마다 첫 탑승 trip.bin을 data/trips/real/에 남긴다")
    ap.add_argument("--jobs", type=int, default=mp.cpu_count())
    ap.add_argument("--note", default="", help="run.json에 남길 설명")
    a = ap.parse_args()
    reader = __import__(READERS[a.source])
    found = reader.routes(a.root)
    if not found:
        sys.exit(f"{a.root}: 주행을 못 찾음 (docs/REALDATA.md의 폴더 구조 참고)")
    rng = np.random.default_rng(a.seed)
    pax = [sim.gen_passenger(rng, i) for i in range(a.passengers)]  # build_dataset과 같은 승객 풀
    prng = np.random.default_rng([a.seed, 1])
    k = sim.CFG["scale"]["riders_per_trip"]
    OUT = runs.new(f"real-{a.source}", a.note, params={"root": a.root, "seed": a.seed, "passengers": a.passengers})
    keep = None
    if a.keep_trips:
        keep = OUT / "trips" / "real" / a.source
        keep.mkdir(parents=True, exist_ok=True)
    tasks = [(a.source, str(d), segs, i, [pax[j] for j in prng.choice(a.passengers, k, replace=False)], a.seed, keep)
             for i, (d, segs) in enumerate(found)]
    with mp.Pool(a.jobs) as pool:
        res = pool.map(one, tasks)
    infos = [r[0] for r in res if r[0] is not None]
    rides = sorted([x for r in res for x in r[1]], key=lambda r: r["ride_id"])
    rides = [r for r in rides if len(r["t"])]
    cat = lambda key: np.concatenate([r[key] for r in rides])
    rep = lambda key: np.concatenate([np.full(len(r["t"]), r[key]) for r in rides])
    data = {"X": cat("X"), "y": cat("y"), "t": cat("t"), "misc": cat("misc"), "score": cat("score"),
            "future_max": cat("future_max"), "ride_id": rep("ride_id"), "trip_id": rep("trip_id"),
            "person_id": rep("person_id"), "k": np.array(sim.CFG["label"]["k"]),
            "feature_names": np.array([f["name"] for f in tripio.FEATURES["features"]]),
            "feature_version": np.array(tripio.FEATURES["version"])}
    for key in {k_ for r in rides for k_ in r if k_.startswith("tag_")}:
        data[key] = np.concatenate([r.get(key, np.full(len(r["t"]), np.nan)) for r in rides])
    np.savez_compressed(OUT / f"real_{a.source}.npz", **data)
    y = data["y"]
    report = {"source": a.source, "trips": len(infos), "rides": len(rides), "rows": int(len(y)),
              "hours": round(sum(i["minutes"] for i in infos) / 60, 2),
              "label_rate": {f"P(>={kk})": float(y[:, j].mean()) for j, kk in enumerate(sim.CFG["label"]["k"])},
              "align": infos}
    (OUT / f"real_{a.source}_report.json").write_text(json.dumps(report, indent=1))
    al = lambda key: np.round(np.percentile([i[key] for i in infos], [0, 50, 100]), 2).tolist()
    print(json.dumps({k_: v for k_, v in report.items() if k_ != "align"}, indent=1))
    print("align min/median/max  tilt", al("tilt_deg"), "sensor_x_heading", al("sensor_x_heading_deg"), "corr_long", al("corr_long"),
          "corr_lat", al("corr_lat"), "g", al("g_norm"), "still_s", al("still_s"))


if __name__ == "__main__":
    main()
