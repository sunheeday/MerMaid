"""TU Delft ATDOMS를 가상 주행으로 바꿔 모델을 실제 사람 MISC로 시험

    python3 ml/verify/tudelft_replay.py data/raw/ATDOMS.mat [--v0-kmh 50]

실험: 17명 x 조건 4개 (앞뒤 0.3 Hz 사인, 1 / 1.5 / 2 / 2.5 m/s², MISC 6에서 멈추고 휴식 뒤 다시).
변환: 같은 앞뒤 가속도를 내는 차 = 일정 속도 v0에서 3.3초마다 속도가 오르내리는 주행.
  ax = 자극 + 진동 + 잡음, ay = 진동 + 잡음, az = g + 진동 + 잡음 (sim_config.json imu), 자이로는 잡음만,
  speed = v0 + ∫ax. 휴식 구간은 v0 등속. CAN 신호는 모두 NaN.
승객 피처: MSSQ, sens = oman.sensitivity(MSSQ), EV 경험 NaN, 차량 유형 0.
라벨: 10초마다 다음 300초 안 실제 MISC 보고(30초 간격)의 최대 >= k. 미래 300초가 세션 안에 있는 시점만.
비교: 모델 / oman_score 피처 / oman_score x sens / 개인 K Oman (같은 사람의 다른 조건 3개로 맞춤, 상한 참고) /
      지금 MISC (모델은 못 보는 값, 참고).
결과: ml/verify/out/tudelft_replay.json, .md
"""
import argparse
import json
import pathlib
import sys
import tempfile

import lightgbm as lgb
import numpy as np
import scipy.io as sio
from scipy.optimize import minimize_scalar
from scipy.signal import butter, sosfilt
from sklearn.metrics import average_precision_score, roc_auc_score

ML = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ML))
sys.path.insert(0, str(ML / "train"))
import oman  # noqa: E402
import runs  # noqa: E402
import trip as tripio  # noqa: E402
from common import monotone_fix  # noqa: E402

FS, DT, G = tripio.FS, 1.0 / tripio.FS, 9.81
AMPS = {"C1": 1.0, "C2": 1.5, "C3": 2.0, "C4": 2.5}  # oman_tudelft.py에서 확인한 순서
F_STIM = 0.3
KS = [2, 3, 4, 5, 6]
IMU = json.loads((ML / "simulator" / "sim_config.json").read_text())["imu"]
NAMES = [f["name"] for f in tripio.FEATURES["features"]]
arr = lambda x: np.atleast_1d(x).astype(float)


def session(S):
    """→ 자극 구간 [(시작, 끝)], MISC 보고 시각, 점수 (oman_tudelft.py와 같은 해석)"""
    ph = {k: (arr(getattr(S, k).misc.time), arr(getattr(S, k).misc.score)) for k in ["Phase1", "Rest1", "Phase2", "Rest2"]}
    if len(ph["Rest1"][0]) == 0:
        seg = [(0.0, ph["Phase1"][0][-1] + 30)]
    else:
        seg = [(0.0, ph["Rest1"][0][0])]
        if len(ph["Phase2"][0]):
            seg.append((ph["Phase2"][0][0], ph["Rest2"][0][0] if len(ph["Rest2"][0]) else ph["Phase2"][0][-1] + 30))
    t = np.concatenate([ph[k][0] for k in ph])
    y = np.concatenate([ph[k][1] for k in ph])
    o = np.argsort(t, kind="stable")
    return seg, t[o], y[o]


def stimulus(seg, n, amp):
    t = np.arange(n) * DT
    a = np.zeros(n)
    for s, e in seg:
        m = (t >= s) & (t < e)
        ramp = np.minimum(1, np.minimum((t[m] - s) / 10, (e - t[m]) / 10))
        a[m] = ramp * amp * np.sin(2 * np.pi * F_STIM * (t[m] - s))
    return a


def make_trip(a, v0, rng):
    n = len(a)
    v = v0 + np.cumsum(a) * DT
    sos = butter(2, IMU["vib_band_hz"], btype="band", fs=FS, output="sos")

    def vib(ratio):
        x = sosfilt(sos, rng.normal(0, 1, n))
        return x / x.std() * (IMU["vib_base"] + IMU["vib_per_mps"] * v) * ratio

    C = tripio.COL
    rows = tripio.empty(n)
    an = lambda: rng.normal(0, IMU["accel_noise"], n)
    gn = lambda: rng.normal(0, IMU["gyro_noise"], n)
    rows[:, C["ax"]] = a + vib(IMU["vib_xy_ratio"]) + an()
    rows[:, C["ay"]] = vib(IMU["vib_xy_ratio"]) + an()
    rows[:, C["az"]] = G + vib(1.0) + an()
    rows[:, C["gx"]], rows[:, C["gy"]], rows[:, C["gz"]] = gn(), gn(), gn()
    rows[:, C["speed"]] = v
    return rows


def oman_parts(a):
    """F = K * Gk 이므로 K마다 다시 돌리지 않는다. score = max(K*Gk + S, 0)^0.4"""
    c = oman.input_signal(a, None, DT)
    S = oman.lp2(c, oman.OMAN["beta_slow_s"], DT)
    return oman.lp2(S * c, oman.OMAN["beta_fast_s"], DT), S


def om(K, Gk, S):
    return np.maximum(K * Gk + S, 0) ** oman.OMAN["exponent"]


def fit_k(parts):
    """parts: [(Gk at report times, S at report times, misc)] → MAE 최소 K"""
    y = np.concatenate([p[2] for p in parts])
    f = lambda lk: np.nanmean(np.abs(np.concatenate([om(np.exp(lk), p[0], p[1]) for p in parts]) - y))
    return float(np.exp(minimize_scalar(f, bounds=(np.log(0.1), np.log(1e5)), method="bounded").x))


def auc(y, s):
    m = np.isfinite(s)
    y, s = y[m], s[m]
    if y.min() == y.max():
        return None, None
    return float(roc_auc_score(y, s)), float(average_precision_score(y, s))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mat")
    ap.add_argument("--v0-kmh", type=float, default=50.0)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--models", help="모델 버전 (기본 CURRENT)")
    a_ = ap.parse_args()
    A = sio.loadmat(a_.mat, squeeze_me=True, struct_as_record=False)["ATDOMS"]
    persons = list(A._fieldnames)
    run = runs.resolve(a_.models) if a_.models else runs.current_models()
    models = {k: lgb.Booster(model_file=str(run / "models" / f"model_{k}.txt")) for k in KS}
    th = json.loads((run / "thresholds.json").read_text())["base_th"]
    tiers = json.loads((ML.parent / "controller" / "config" / "policy.json").read_text())["tiers"]
    v0 = a_.v0_kmh / 3.6

    ses = {}
    for pi, p in enumerate(persons):
        mssq = float(getattr(A, p).MSSQ.Score)
        for c, amp in AMPS.items():
            seg, tr, yr = session(getattr(getattr(A, p), c))
            n = int((tr[-1] + 10) * FS)
            acc = stimulus(seg, n, amp)
            Gk, S = oman_parts(acc)
            ses[(p, c)] = {"pi": pi, "mssq": mssq, "seg": seg, "tr": tr, "yr": yr, "acc": acc,
                           "Gk_r": np.interp(tr, np.arange(n) * DT, Gk), "S_r": np.interp(tr, np.arange(n) * DT, S),
                           "Gk": Gk, "S": S}

    rows_all = []
    with tempfile.TemporaryDirectory() as td:
        for (p, c), s in ses.items():
            rng = np.random.default_rng([a_.seed, s["pi"], int(c[1])])
            path = pathlib.Path(td) / "trip.bin"
            tripio.write_trip(path, make_trip(s["acc"], v0, rng))
            t, Xv = tripio.featurize(path, pathlib.Path(td) / "features.bin")
            X = np.full((len(t), 77), np.nan)
            X[:, :71] = Xv
            X[:, 73], X[:, 74], X[:, 76] = s["mssq"], float(oman.sensitivity(s["mssq"])), 0.0
            for i, f in enumerate(tripio.FEATURES["features"]):
                if not f["enabled"]:
                    X[:, i] = np.nan
            Pm = monotone_fix(np.column_stack([models[k].predict(X) for k in KS]))
            tr, yr = s["tr"], s["yr"]
            win = lambda tt: yr[(tr > tt) & (tr <= tt + 300)]
            fut = np.array([np.nanmax(w) if tt + 300 <= tr[-1] and np.isfinite(w := win(tt)).any() else np.nan for tt in t])
            now = np.array([yr[tr <= tt][-1] if (tr <= tt).any() else 0.0 for tt in t])
            others = [ses[(p, d)] for d in AMPS if d != c]
            Kp = fit_k([(o["Gk_r"], o["S_r"], o["yr"]) for o in others])
            idx = np.minimum((t * FS).astype(int) - 1, len(s["Gk"]) - 1)
            v = np.isfinite(fut)
            rows_all.append({"p": p, "c": c, "t": t[v], "P": Pm[v], "fut": fut[v], "now": now[v],
                             "oman": X[v, NAMES.index("oman_score")], "sens": X[v, 74],
                             "omanKp": om(Kp, s["Gk"][idx], s["S"][idx])[v], "Kp": Kp, "X": X[v],
                             "tr": tr, "yr": yr, "t_all": t, "P_all": Pm})

    cat = lambda k: np.concatenate([r[k] for r in rows_all])
    P, fut, now = cat("P"), cat("fut"), cat("now")
    preds = {"model": None, "oman_score": cat("oman"), "oman_score x sens": cat("oman") * cat("sens"),
             "personal K Oman (ref)": cat("omanKp"), "current MISC (ref)": now}
    res = {"v0_kmh": a_.v0_kmh, "rows": int(len(fut)), "sessions": len(rows_all), "persons": len(persons), "by_k": {}}
    for j, k in enumerate(KS):
        y = (fut >= k).astype(float)
        r = {"rate": float(y.mean()), "mean_p": float(P[:, j].mean())}
        for name, s in preds.items():
            r[name] = auc(y, P[:, j] if s is None else s)
        res["by_k"][k] = r

    # 사람 사이 순위: 조건마다 같은 시점(2분)의 값으로 17명이 Phase1에서 MISC 4에 닿는 순서를 맞히나
    # (흔들림이 모두 같으므로 차이는 사람 정보에서만 나온다. 닿지 않은 사람은 가장 늦은 것으로)
    from scipy.stats import spearmanr
    T_RANK = 120.0
    bp = {"model P(>=4)": [], "MSSQ (= oman_score x sens)": [], "personal K (ref)": []}
    for c in AMPS:
        reach, vals = [], {k: [] for k in bp}
        for r in [r for r in rows_all if r["c"] == c]:
            p1_end = ses[(r["p"], c)]["seg"][0][1]
            hit = r["tr"][(r["yr"] >= 4) & (r["tr"] <= p1_end + 30)]
            reach.append(hit[0] if len(hit) else 1e9)
            i = int(np.argmin(np.abs(r["t_all"] - T_RANK)))
            vals["model P(>=4)"].append(r["P_all"][i, KS.index(4)])
            vals["MSSQ (= oman_score x sens)"].append(ses[(r["p"], c)]["mssq"])
            vals["personal K (ref)"].append(r["Kp"])
        for k in bp:
            bp[k].append(float(spearmanr(vals[k], -np.array(reach)).statistic))
    res["between_person"] = {k: {"per_condition": v, "mean": float(np.mean(v))} for k, v in bp.items()}

    # 피처가 학습 범위 밖인지: 시뮬레이터 데이터셋 최대값과 비교
    D = np.load(runs.dataset_for(run), allow_pickle=False)
    Xall = cat("X")
    res["out_of_range"] = {}
    for f in ["rms_x_1m", "jerk_rms_x_1m", "decel_rms_x_1m", "hard_brake_count_5m", "hard_accel_count_5m", "msdv", "oman_score"]:
        j = NAMES.index(f)
        res["out_of_range"][f] = {"replay_median": float(np.nanmedian(Xall[:, j])),
                                  "sim_p99": float(np.nanpercentile(D["X"][:, j], 99)),
                                  "sim_max": float(np.nanmax(D["X"][:, j])),
                                  "share_above_sim_max": float(np.mean(Xall[:, j] > np.nanmax(D["X"][:, j])))}

    # Tier 시점: 첫 노출(Phase1)에서 실제로 MISC >= input_k에 처음 닿은 때 vs 모델이 문턱을 처음 넘은 때
    res["tiers"] = {}
    for tier, cfg in tiers.items():
        k = cfg["input_k"]
        j = KS.index(k)
        leads, ons, hits, missed, never = [], [], [], 0, 0
        for r in rows_all:
            p1_end = ses[(r["p"], r["c"])]["seg"][0][1]
            hit = r["tr"][(r["yr"] >= k) & (r["tr"] <= p1_end + 30)]
            if not len(hit):
                never += 1
                continue
            on = r["t_all"][(r["P_all"][:, j] >= th[tier]) & (r["t_all"] <= hit[0])]
            hits.append(float(hit[0]))
            if len(on):
                leads.append(float(hit[0] - on[0]))
                ons.append(float(on[0]))
            else:
                missed += 1
        res["tiers"][tier] = {"input_k": k, "threshold": th[tier], "sessions_reaching_k": len(leads) + missed,
                              "on_before_reaching": len(leads), "median_lead_s": float(np.median(leads)) if leads else None,
                              "lead_s_iqr": np.percentile(leads, [25, 75]).tolist() if leads else None,
                              "median_reach_s": float(np.median(hits)) if hits else None,
                              "median_on_s": float(np.median(ons)) if ons else None,
                              "on_in_first_minute": int(np.sum(np.array(ons) <= 60))}
    res["personal_K"] = {f"{r['p']} {r['c']}": r["Kp"] for r in rows_all}

    OUT = run / "verify"
    OUT.mkdir(exist_ok=True)
    res["run"] = run.name
    (OUT / "tudelft_replay.json").write_text(json.dumps(res, indent=1, ensure_ascii=False))
    fmt = lambda x: "—" if x is None else f"{x[0]:.2f} / {x[1]:.2f}"
    L = [f"# TU Delft 재생 ({res['persons']}명, 세션 {res['sessions']}개, {res['rows']}행, v0 {a_.v0_kmh:g} km/h)", "",
         "ROC-AUC / PR-AUC. (ref)는 모델이 못 쓰는 정보를 쓴 참고값", "",
         "| k | 실제 비율 | 평균 예측 P | 모델 | oman_score | oman_score x sens | 개인 K Oman (ref) | 지금 MISC (ref) |",
         "|---|---|---|---|---|---|---|---|"]
    for k, r in res["by_k"].items():
        L.append(f"| {k} | {r['rate']:.3f} | {r['mean_p']:.3f} | {fmt(r['model'])} | {fmt(r['oman_score'])} | "
                 f"{fmt(r['oman_score x sens'])} | {fmt(r['personal K Oman (ref)'])} | {fmt(r['current MISC (ref)'])} |")
    L += ["", "## 사람 사이 순위 (조건마다 2분 시점 값 vs Phase1에서 MISC 4에 먼저 닿는 순서, Spearman, 17명)", "",
          "| 예측값 | C1 | C2 | C3 | C4 | 평균 |", "|---|---|---|---|---|---|"]
    for k, v in res["between_person"].items():
        L.append(f"| {k} | " + " | ".join(f"{x:.2f}" for x in v["per_condition"]) + f" | {v['mean']:.2f} |")
    L += ["", "## 피처가 학습 범위 밖인 정도", "", "| 피처 | 재생 중앙값 | 시뮬 p99 | 시뮬 최대 | 시뮬 최대 넘는 비율 |", "|---|---|---|---|---|"]
    for f, r in res["out_of_range"].items():
        L.append(f"| {f} | {r['replay_median']:.2f} | {r['sim_p99']:.2f} | {r['sim_max']:.2f} | {r['share_above_sim_max']:.2f} |")
    L += ["", "## Tier 시점 (첫 노출, 실제로 MISC >= input_k에 닿기 전에 켜졌나)", "",
          "| Tier | input_k | 문턱 | 닿은 세션 | 닿기 전 켜짐 | 그중 첫 1분 안 | 켜진 때 중앙값 | 닿은 때 중앙값 | 리드 중앙값 (IQR) |",
          "|---|---|---|---|---|---|---|---|---|"]
    for tier, r in res["tiers"].items():
        lead = "—" if r["median_lead_s"] is None else f"{r['median_lead_s'] / 60:.1f}분 ({r['lead_s_iqr'][0] / 60:.1f}~{r['lead_s_iqr'][1] / 60:.1f})"
        mm = lambda x: "—" if x is None else f"{x / 60:.1f}분"
        L.append(f"| {tier} | {r['input_k']} | {r['threshold']} | {r['sessions_reaching_k']} | {r['on_before_reaching']} | "
                 f"{r['on_in_first_minute']} | {mm(r['median_on_s'])} | {mm(r['median_reach_s'])} | {lead} |")
    (OUT / "tudelft_replay.md").write_text("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
