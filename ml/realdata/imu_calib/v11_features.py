"""V11: 실차 피처 분포가 시뮬레이터(학습 데이터) 분포와 얼마나 겹치는지, 보정 전후로 비교한다.

왜: 보정의 목표는 "실차 데이터를 시뮬레이터와 같은 정의로" 만드는 것이다. 정의가 같아졌다면
    보정 후 실차 피처가 학습 데이터 분포 안쪽으로 들어와야 한다(적어도 바깥으로 밀려나면 안 된다).

무엇을 비교하나:
    - 학습 데이터: 저장소 ml/out/runs/<데이터셋>/dataset.npz 의 X (10초마다 한 행, 1920개 탑승)
      탑승 시작 5분 뒤 행만 쓴다 (5분 창 피처가 다 차도록)
    - 실차: 같은 featurize(C++)로 경우별(C 보정 전, D 보정 후, --extra) 피처를 계산하고
      주행 구간(--drive-from ~ --until)만 쓴다. 비교 대상은 IMU로 만드는 피처뿐이다
      (속도·페달·배터리·실내 피처는 실차에 없어 비어 있다).
    - 피처마다: 학습 데이터의 1·5·50·95·99 백분위, 실차 중앙값이 학습 분포에서 몇 백분위인지,
      실차 행 중 학습 분포 [1%, 99%] 밖에 있는 비율.

주의:
    - 학습 데이터는 여러 차종·도로·승객을 섞은 분포라 "겹친다"는 것은 필요조건일 뿐 충분조건이 아니다.
    - msdv 는 탑승 시간이 길수록 커지는 누적값이라 탑승 시간 분포 차이도 섞인다.

쓰는 법 (imu_calib 폴더에서):
    .venv/bin/python v11_features.py <주행 폴더> <결과 폴더> [--drive-from 초] [--until 초] [--extra "이름=폴더"]
"""
import argparse
import glob
import json
import os
import sys

import numpy as np

def _default_repo():
    """이 파일이 저장소 안(ml/realdata/imu_calib)에 있으면 그 저장소, 아니면 ~/SE_project/MerMaid-main"""
    here = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
    return here if os.path.exists(os.path.join(here, "ml", "trip.py")) else os.path.expanduser("~/SE_project/MerMaid-main")


REPO = os.environ.get("MERMAID_REPO") or _default_repo()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from model_rerun import featurize  # noqa: E402  저장소 featurize 를 그대로 쓴다

IMU_FEATURES = ["rms_x_10s", "rms_x_1m", "rms_x_5m", "rms_y_10s", "rms_y_1m", "rms_y_5m",
                "rms_z_10s", "rms_z_1m", "rms_z_5m", "decel_rms_x_1m", "decel_rms_x_5m",
                "accel_rms_x_1m", "accel_rms_x_5m", "jerk_rms_x_10s", "jerk_rms_x_1m",
                "jerk_rms_y_10s", "jerk_rms_y_1m", "yaw_rms_10s", "yaw_rms_1m",
                "hard_accel_count_5m", "hard_brake_count_5m", "hard_turn_count_5m",
                "event_irregularity_5m", "pitch_mean_1m", "msdv", "msdv_inc_5m",
                "oman_score", "oman_delta_2m"]
PLOT_FEATURES = ["rms_x_1m", "rms_y_1m", "rms_z_1m", "jerk_rms_x_1m", "yaw_rms_1m", "pitch_mean_1m",
                 "oman_score", "msdv_inc_5m"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ride")
    ap.add_argument("outdir")
    ap.add_argument("--dataset", help="dataset.npz (기본: 저장소 ml/out/runs 중 첫 dataset.npz)")
    ap.add_argument("--drive-from", type=float, default=0.0, help="이 시각[초]부터를 주행으로 본다")
    ap.add_argument("--until", type=float, help="이 시각[초]까지만 쓴다")
    ap.add_argument("--extra", action="append", default=[], metavar="이름=폴더")
    a = ap.parse_args()

    ds_path = a.dataset or sorted(glob.glob(os.path.join(REPO, "ml", "out", "runs", "*", "dataset.npz")))[0]
    ds = np.load(ds_path)
    names = [str(n) for n in ds["feature_names"]]
    col = {n: i for i, n in enumerate(names)}
    rt = ds["X"][:, col["ride_time"]]
    S = ds["X"][rt >= 300]                       # ride_time 단위 확인용으로 아래에 함께 적는다
    print(f"학습 데이터 {ds_path}: {len(ds['X'])}행 중 탑승 5분 이후 {len(S)}행 (ride_time 최대 {np.nanmax(rt):.0f})")

    cases = {"C 보정 전(축만)": os.path.join(a.ride, "raw_caraxis"), "D 보정 후": os.path.join(a.ride, "calibrated")}
    for e in a.extra:
        n, f = e.split("=", 1)
        cases[n] = os.path.join(a.ride, f)
    R = {}
    for n, folder in cases.items():
        s = np.load(os.path.join(folder, "samples_100hz.npy")).astype(float)
        tf, V = featurize(s)
        m = (tf >= a.drive_from) & ((tf <= a.until) if a.until else True)
        R[n] = V[m]
        print(f"{n}: 피처 {len(tf)}행 중 {int(m.sum())}행 사용")

    os.makedirs(a.outdir, exist_ok=True)
    lines = ["# V11 실차 피처 vs 학습 데이터(시뮬레이터) 분포", "",
             f"- 주행: `{os.path.abspath(a.ride)}`, 쓴 구간 {a.drive_from:.0f}~{a.until if a.until else '끝'}초",
             f"- 학습 데이터: `{ds_path}` 중 탑승 시작 5분 이후 {len(S):,}행 (1920개 탑승)",
             "- '백분위' = 실차 중앙값이 학습 데이터 분포에서 몇 % 위치인가 (50이면 한가운데, 0이나 100에 가까우면 가장자리)",
             "- '밖' = 실차 행 중 학습 데이터 [1%, 99%] 범위를 벗어난 비율", ""]
    hdr = "| 피처 | 학습 1% / 50% / 99% | " + " | ".join(f"{n} 중앙값 (백분위, 밖)" for n in R) + " |"
    lines += [hdr, "|" + "---|" * (2 + len(R))]
    out = {}
    for f in IMU_FEATURES:
        j = col[f]
        s = S[:, j]
        s = s[np.isfinite(s)]
        if len(s) == 0:
            continue
        p1, p50, p99 = np.percentile(s, [1, 50, 99])
        row = f"| {f} | {p1:.3g} / {p50:.3g} / {p99:.3g} |"
        out[f] = {"sim_p1_p50_p99": [p1, p50, p99]}
        for n, V in R.items():
            r = V[:, j]
            r = r[np.isfinite(r)]
            if len(r) == 0:
                row += " 없음 |"
                continue
            med = float(np.median(r))
            pct = float((s < med).mean() * 100)
            outside = float(((r < p1) | (r > p99)).mean() * 100)
            row += f" {med:.3g} ({pct:.0f}, {outside:.0f}%) |"
            out[f][n] = {"median": med, "percentile": pct, "outside_pct": outside}
        lines.append(row)
    json.dump(out, open(os.path.join(a.outdir, "v11_분포.json"), "w"), ensure_ascii=False, indent=1)
    open(os.path.join(a.outdir, "v11_분포.md"), "w").write("\n".join(lines) + "\n")
    print("\n".join(lines))

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.family"] = "Apple SD Gothic Neo"
    plt.rcParams["axes.unicode_minus"] = False
    colors = ["#888", "#c0392b", "#1f6fb2", "#27ae60", "#8e44ad"]
    fig, axs = plt.subplots(2, 4, figsize=(16, 7))
    for ax, f in zip(axs.flat, PLOT_FEATURES):
        j = col[f]
        s = S[:, j]
        s = s[np.isfinite(s)]
        lo, hi = np.percentile(s, [0.5, 99.5])
        for V in R.values():
            r = V[:, j][np.isfinite(V[:, j])]
            if len(r):
                lo, hi = min(lo, np.percentile(r, 1)), max(hi, np.percentile(r, 99))
        bins = np.linspace(lo, hi, 50)
        ax.hist(s, bins=bins, density=True, color="#ddd", label="학습 데이터")
        for c, (n, V) in zip(colors, R.items()):
            r = V[:, j][np.isfinite(V[:, j])]
            if len(r):
                ax.hist(np.clip(r, lo, hi), bins=bins, density=True, histtype="step", lw=1.4, color=c, label=n)
        ax.set_title(f, fontsize=10)
        ax.set_yticks([])
    axs.flat[0].legend(fontsize=8)
    fig.suptitle(f"V11 피처 분포: 학습 데이터 vs 실차 ({os.path.basename(os.path.abspath(a.ride))})")
    fig.tight_layout()
    fig.savefig(os.path.join(a.outdir, "7_V11_피처분포.png"), dpi=120)
    print("저장:", a.outdir)


if __name__ == "__main__":
    main()
