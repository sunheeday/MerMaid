"""S6 파이프라인 확인: Pi의 P1→P4 결과(cycles.csv)가 Mac의 featurize + Python LightGBM + 정책 재생과 같은지

    python3 ml/verify/pipeline_check.py spec <ride_id>...          # Pi 실행 인자 (폰 프로필, 차량 유형)
    (Pi)    controller/qnx/run_pipeline.sh data/trips/ride_XXXX.bin <out> <speed> <vtype> <폰들>
    python3 ml/verify/pipeline_check.py check <trip.bin> <cycles.csv> <ride_id>...
    python3 ml/verify/pipeline_check.py check <trip.bin> <cycles.csv> default:<vtype>   # 폰 없이 돌린 경우

폰 i의 승객 피처(MSSQ, 민감도, EV 경험)는 데이터셋의 ride_id 탑승에서 가져온다. 값은 15자리로 반올림해
양쪽에 같은 수가 들어가게 한다. 재생 조건: HUD 연결, 화면 켜짐, 주의 있음, 수동 조작 없음, 허용도 0.
합격: 주기 수가 같고, 확률 max |차이| < 1e-6, 모든 기기의 ON/OFF가 모든 주기에서 같음.
"""
import csv
import json
import pathlib
import subprocess
import sys
import tempfile

import numpy as np

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "ml" / "train"))
sys.path.insert(0, str(REPO / "ml" / "verify"))
import common as C  # noqa: E402
import policy  # noqa: E402
from model_parity import boosters, predict  # noqa: E402

ENABLED = np.array([f["enabled"] for f in C.FEATS])
DEFAULT = json.loads((REPO / "protocol" / "constants.json").read_text())["mssq"]["default_profile"]


def g10(ride_ids):
    if len(ride_ids) == 1 and str(ride_ids[0]).startswith("default:"):  # P3 기본 프로필, EV 경험은 모름
        return [[DEFAULT["mssq"], DEFAULT["sens"], np.nan, float(ride_ids[0].split(":")[1])]]
    d = C.load()
    out = []
    for r in ride_ids:
        row = d["X"][d["ride_id"] == r][0]
        out.append([float(f"{v:.15g}") for v in row[73:77]])  # mssq, sens, ev, vehicle_type
    return out


def spec(ride_ids):
    g = g10(ride_ids)
    phones = [f"phone-{i}:{m!r}:{s!r}:{e!r}" for i, (m, s, e, _) in enumerate(g)]
    print(int(g[0][3]), " ".join(phones))


def featurize(trip):
    with tempfile.TemporaryDirectory() as td:
        out = pathlib.Path(td) / "f.bin"
        subprocess.run([str(REPO / "build" / "featurize"), str(trip), str(out)], check=True, capture_output=True)
        a = np.fromfile(out, dtype="<f8")
    ncol = int(a[1])
    a = a[2:].reshape(-1, ncol)
    return a[:, 0], a[:, 1:]


def check(trip, cycles_csv, ride_ids):
    g = g10(ride_ids)
    t, V = featurize(trip)
    bs = boosters()
    P = []
    for m, s, e, vt in g:
        X = np.full((len(t), 77), np.nan)
        X[:, :71] = V
        X[:, 73:77] = m, s, e, vt
        X[:, ~ENABLED] = np.nan
        P.append(predict(bs, X))
    P = np.stack(P)  # phone, cycle, k

    th = {k: float(v) for k, v in json.loads((REPO / "controller" / "config" / "thresholds.json").read_text())["base_th"].items()}
    tiers = policy.POLICY["tiers"]
    rep = lambda r, tier: policy.replay_ride(r, th[tier], tiers[tier]["hold_cycles"], policy.POLICY["off_margin"],
                                             policy.POLICY["off_hold_cycles"])[0]
    col = lambda tier: C.K.index(tiers[tier]["input_k"])
    want = {"airflow-01": rep(P[:, :, col("1")].max(axis=0), "1"),
            "display-01": rep(P[:, :, col("2")].max(axis=0), "2")}
    default = str(ride_ids[0]).startswith("default:")
    names = ["default"] if default else [f"phone-{i}" for i in range(len(g))]
    for i in range(len(g)):
        if not default:  # 기본 프로필 행은 폰 기기(Tier 3)가 없다
            want[f"phone-{i}"] = rep(P[i, :, col("3")], "3")

    rows = list(csv.DictReader(open(cycles_csv)))
    n_ph = len(g)
    got_t = np.array([float(r["t"]) for r in rows[::n_ph]])
    res = {"cycles_mac": len(t), "cycles_pi": len(got_t)}
    ok = len(got_t) == len(t)
    if ok:
        A = np.array([[float(r[f"p{k}"]) for k in C.K] for r in rows]).reshape(len(t), n_ph, 5).transpose(1, 0, 2)
        ids = [r["phone"] for r in rows[:n_ph]]
        res["phones"] = ids
        res["t_max_diff"] = float(np.abs(got_t - t).max())
        res["prob_max_abs_diff"] = float(np.abs(A - P).max())
        res["devices"] = {}
        for d, w in want.items():
            got = np.array([int(r[d]) for r in rows[::n_ph]], bool)
            res["devices"][d] = {"on_cycles_mac": int(w.sum()), "on_cycles_pi": int(got.sum()),
                                 "mismatch": int((got != w).sum())}
        ok = (ids == names and res["t_max_diff"] < 1e-9 and res["prob_max_abs_diff"] < 1e-6
              and all(v["mismatch"] == 0 for v in res["devices"].values()))
        lat = np.array([float(r["latency_ms"]) for r in rows[::n_ph]])
        res["latency_ms_median"], res["latency_ms_max"] = float(np.median(lat)), float(lat.max())
    res["pass"] = bool(ok)
    print(json.dumps(res, indent=1))
    return 0 if ok else 1


if __name__ == "__main__":
    if sys.argv[1:2] == ["spec"] and len(sys.argv) > 2:
        spec([int(x) for x in sys.argv[2:]])
    elif sys.argv[1:2] == ["check"] and len(sys.argv) > 4:
        rides = sys.argv[4:] if sys.argv[4].startswith("default:") else [int(x) for x in sys.argv[4:]]
        sys.exit(check(sys.argv[2], sys.argv[3], rides))
    else:
        print(__doc__)
        sys.exit(2)
