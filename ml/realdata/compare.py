"""실차(real_<source>.npz) vs 시뮬레이터(dataset.npz) 비교

    python3 ml/realdata/compare.py --source comma2k19

1. 운동 피처 분포 (p10/p50/p90, KS 거리). 누적형 피처(MSDV, Oman, 시간)가 공정하도록 시뮬레이터는
   실차 행이 있는 탑승 시간(t ≤ 실차 최대 t)만 쓴다.
2. 라벨 비율 P(>=k)와 Oman 점수: 같은 시간 범위끼리.
3. 시뮬레이터로 학습한 모델(ml/out/models)을 실차 행에 그대로 적용한 PR/ROC-AUC와 보정(평균 P vs 실제 비율).
출력: ml/out/compare_<source>.json, ml/out/compare_<source>.md
"""
import argparse
import json
import pathlib
import sys

import lightgbm as lgb
import numpy as np
from scipy.stats import ks_2samp
from sklearn.metrics import average_precision_score, roc_auc_score

ML = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ML / "train"))
import common as C  # noqa: E402  (ml/train/common.py)

OUT = C.REPO / "ml" / "out"
MOTION = ["rms_x_1m", "rms_y_1m", "rms_z_1m", "decel_rms_x_1m", "accel_rms_x_1m", "jerk_rms_x_1m",
          "jerk_rms_y_1m", "yaw_rms_1m", "hard_accel_count_5m", "hard_brake_count_5m", "hard_turn_count_5m",
          "pitch_mean_1m", "msdv", "oman_score", "speed_mean_1m", "speed_std_1m", "stop_ratio_5m"]


def q(x):
    x = x[np.isfinite(x)]
    return [float(v) for v in np.percentile(x, [10, 50, 90])] if len(x) else [np.nan] * 3


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--sim", default=str(OUT / "dataset.npz"))
    a = ap.parse_args()
    R = np.load(OUT / f"real_{a.source}.npz", allow_pickle=False)
    S = np.load(a.sim, allow_pickle=False)
    names = list(S["feature_names"])
    tmax = float(R["t"].max())
    sm = S["t"] <= tmax  # 같은 탑승 시간 범위
    res = {"source": a.source, "real_rows": int(len(R["t"])), "sim_rows_matched": int(sm.sum()),
           "t_max_s": tmax, "features": {}, "labels": {}, "model": {}}

    for f in MOTION:
        j = names.index(f)
        r, s = R["X"][:, j], S["X"][sm, j]
        ks = ks_2samp(r[np.isfinite(r)], s[np.isfinite(s)]).statistic
        res["features"][f] = {"real": q(r), "sim": q(s), "ks": float(ks)}

    cov = lambda X: int(np.sum(np.isfinite(X).mean(0) > 0.5))
    res["coverage"] = {"real_features_present": cov(R["X"]), "sim_features_present": cov(S["X"]), "of": len(names)}
    res["speed_kmh_share"] = {}
    for nm, X in (("real", R["X"]), ("sim", S["X"][sm])):
        v = X[:, names.index("speed_mean_1m")] * 3.6
        res["speed_kmh_share"][nm] = {"<30": float((v < 30).mean()), "30-80": float(((v >= 30) & (v < 80)).mean()),
                                      ">=80": float((v >= 80).mean())}

    for j, k in enumerate(C.K):
        res["labels"][f"P(>={k})"] = {"real": float(R["y"][:, j].mean()), "sim_matched": float(S["y"][sm, j].mean()),
                                      "sim_all": float(S["y"][:, j].mean())}
    res["labels"]["score"] = {"real": q(R["score"]), "sim_matched": q(S["score"][sm])}

    mj = json.loads((OUT / "models" / "models.json").read_text())
    P = np.column_stack([lgb.Booster(model_file=str(OUT / "models" / mj["models"][str(k)]["file"])).predict(R["X"])
                         for k in C.K])
    P = C.monotone_fix(P)
    sim_rep = json.loads((OUT / "train" / "report.json").read_text())["summary"]["k"]
    oman = R["X"][:, names.index("oman_score")]
    for j, k in enumerate(C.K):
        y = R["y"][:, j]
        row = {"rate": float(y.mean()), "mean_p": float(P[:, j].mean()),
               "sim_cv_pr_auc": sim_rep[str(k)]["model_pr_auc"], "sim_cv_roc_auc": sim_rep[str(k)]["model_roc_auc"]}
        if 0 < y.sum() < len(y):
            row.update(pr_auc=float(average_precision_score(y, P[:, j])), roc_auc=float(roc_auc_score(y, P[:, j])),
                       oman_only_roc_auc=float(roc_auc_score(y, oman)))
        res["model"][str(k)] = row
    (OUT / f"compare_{a.source}.json").write_text(json.dumps(res, indent=1))
    md = markdown(res)
    (OUT / f"compare_{a.source}.md").write_text(md)
    print(md)


def markdown(r):
    f2 = lambda v: "—" if v is None or not np.isfinite(v) else f"{v:.2f}"
    L = [f"### {r['source']} vs 시뮬레이터 (탑승 {r['t_max_s']:.0f}초까지 맞춤)", "",
         f"실차 {r['real_rows']}행, 시뮬레이터 {r['sim_rows_matched']}행. "
         f"피처 77개 중 채워진 것: 실차 {r['coverage']['real_features_present']}, 시뮬레이터 {r['coverage']['sim_features_present']}.", "",
         "| 피처 | 실차 p10 / p50 / p90 | 시뮬 p10 / p50 / p90 | KS |", "|---|---|---|---|"]
    for f, v in r["features"].items():
        L.append(f"| {f} | {' / '.join(map(f2, v['real']))} | {' / '.join(map(f2, v['sim']))} | {v['ks']:.2f} |")
    L += ["", "| 1분 평균 속도 | 실차 | 시뮬 |", "|---|---|---|"]
    for b in ("<30", "30-80", ">=80"):
        L.append(f"| {b} km/h | {r['speed_kmh_share']['real'][b]:.0%} | {r['speed_kmh_share']['sim'][b]:.0%} |")
    L += ["", "| 라벨 | 실차 | 시뮬 (같은 시간) | 시뮬 (전체) |", "|---|---|---|---|"]
    for k, v in r["labels"].items():
        if k != "score":
            L.append(f"| {k} | {v['real']:.3f} | {v['sim_matched']:.3f} | {v['sim_all']:.3f} |")
    s = r["labels"]["score"]
    L.append(f"| Oman 점수 p10/p50/p90 | {' / '.join(map(f2, s['real']))} | {' / '.join(map(f2, s['sim_matched']))} | |")
    L += ["", "| k | 실차 비율 | 평균 예측 P | 실차 PR-AUC | 실차 ROC-AUC | Oman만 ROC | 시뮬 CV PR / ROC |",
          "|---|---|---|---|---|---|---|"]
    for k, v in r["model"].items():
        L.append(f"| {k} | {v['rate']:.3f} | {v['mean_p']:.3f} | {f2(v.get('pr_auc'))} | {f2(v.get('roc_auc'))} | "
                 f"{f2(v.get('oman_only_roc_auc'))} | {v['sim_cv_pr_auc']:.2f} / {v['sim_cv_roc_auc']:.2f} |")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    main()
