"""시뮬레이터 보정 점검표 (T-23): 시뮬레이터 데이터셋 하나를 실차 기준 두 개와 비교한 요약

    python3 ml/simulator/build_dataset.py --trips 60 --keep-trips 0 --out /tmp/x/sim.npz
    python3 ml/realdata/calibrate.py /tmp/x/sim.npz [--json out.json]

기준: BATON-Sample 사람 운전 행(경로가 길어 시간 범위를 맞추지 않음),
      comma2k19(구간이 짧아 시뮬레이터는 같은 탑승 시간까지만).
피처마다 중앙값(실차 / 시뮬)과 KS 거리, 속도대 비율, 라벨 비율을 낸다. 평균 KS가 보정 점수.
"""
import argparse
import json
import pathlib
import sys

import numpy as np
from scipy.stats import ks_2samp

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "ml"))
import runs  # noqa: E402
FEATS = ["rms_x_1m", "jerk_rms_x_1m", "decel_rms_x_1m", "hard_brake_count_5m", "hard_accel_count_5m",
         "rms_y_1m", "jerk_rms_y_1m", "yaw_rms_1m", "hard_turn_count_5m", "rms_z_1m",
         "msdv", "oman_score", "speed_mean_1m", "speed_std_1m", "stop_ratio_5m"]


def targets():
    out = {}
    b = np.load(runs.latest_with("real_baton.npz"), allow_pickle=False)
    m = b["tag_das"] < 0.05
    out["baton"] = {"X": b["X"][m], "y": b["y"][m], "tmax": np.inf}
    c = np.load(runs.latest_with("real_comma2k19.npz"), allow_pickle=False)
    out["comma"] = {"X": c["X"], "y": c["y"], "tmax": float(c["t"].max())}
    return out, list(b["feature_names"])


def speed_share(X, j):
    v = X[:, j] * 3.6
    return [float((v < 30).mean()), float(((v >= 30) & (v < 80)).mean()), float((v >= 80).mean())]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sim")
    ap.add_argument("--json")
    a = ap.parse_args()
    S = np.load(a.sim)
    T, names = targets()
    res = {}
    for tn, tgt in T.items():
        sm = S["t"] <= tgt["tmax"]
        r = {"features": {}}
        for f in FEATS:
            j = names.index(f)
            x, y = tgt["X"][:, j], S["X"][sm, j]
            x, y = x[np.isfinite(x)], y[np.isfinite(y)]
            r["features"][f] = {"real": float(np.median(x)), "sim": float(np.median(y)),
                                "ks": float(ks_2samp(x, y).statistic)}
        r["ks_mean"] = float(np.mean([v["ks"] for v in r["features"].values()]))
        js = names.index("speed_mean_1m")
        r["speed_share"] = {"real": speed_share(tgt["X"], js), "sim": speed_share(S["X"][sm], js)}
        r["label"] = {"real": tgt["y"].mean(0).round(3).tolist(), "sim": S["y"][sm].mean(0).round(3).tolist()}
        res[tn] = r
    print(f"{'feature':22s} | {'BATON real/sim  KS':>24s} | {'comma real/sim  KS':>24s}")
    for f in FEATS:
        b, c = res["baton"]["features"][f], res["comma"]["features"][f]
        print(f"{f:22s} | {b['real']:7.3f} {b['sim']:7.3f} {b['ks']:5.2f}   | {c['real']:7.3f} {c['sim']:7.3f} {c['ks']:5.2f}")
    for tn, r in res.items():
        sp = r["speed_share"]
        print(f"{tn}: KS mean {r['ks_mean']:.3f}  speed <30/30-80/>=80 real {np.round(sp['real'], 2)} sim {np.round(sp['sim'], 2)}"
              f"  P(>=2..6) real {r['label']['real']} sim {r['label']['sim']}")
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
