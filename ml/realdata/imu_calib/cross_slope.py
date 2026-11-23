"""5단계 선택 ②: 좌우 가속도(ay)에서 '움직임이 아닌 느린 좌우 값'(도로 횡단 경사 중력 등)을 GPS로 뺀다.

왜: 시뮬레이터(학습 데이터)의 ay에는 횡단 경사가 없다(ml/simulator/sim.py). 실제 ay에는 늘 +0.2 안팎이 더 들어와
    모델의 멀미 누적 점수(oman_score)를 부풀린다. 앞뒤(ax)의 오르막 중력은 학습 데이터에도 있으므로 건드리지 않는다.

방법:
    진짜 좌우 가속 = GPS 속도 v × 자이로 회전 속도 gz       (원심 가속도)
    남는 값      = ay − v·gz                              (횡단 경사 중력 + 1단계 기준 자리의 좌우 기울기 + 잡음)
    느린 부분     = 남는 값의 느린 성분 (도로 경사는 천천히 바뀐다). 두 가지 방법:
      --method median (기본, 11-24 추가): GPS 정확도가 --max-acc m 이하이고 거의 직진(|gz| < --straight 도/초)인
                       1초들만 골라, --window(기본 120)초 창의 중앙값. 고르지 못한 초는 앞뒤 값으로 잇는다.
                       회전 중에는 v·gz 가 크고 GPS 속도 오차·차체 롤이 섞이므로 쓰지 않는다.
                       GPS가 막 잡힌 구간처럼 정확도가 나쁜 초도 쓰지 않는다. → <주행>/calibrated_xslope2/
      --method mean (처음 방식): 모든 1초의 --window(30)초 이동 평균. → <주행>/calibrated_xslope/
    결과 ay      = ay − 느린 부분
    정차 중에는 v·gz ≈ 0 이라 그 자리 ay 자체(그 자리 바닥 기울기)가 빠진다.
    GPS가 없는 시간은 손대지 않는다 (출발 전 정지는 1단계가 이미 0으로 맞춤).
    median 방법은 경계에서 0 → 값으로 --ramp 초에 걸쳐 바꿔 계단을 만들지 않는다.

쓰는 법 (imu_calib 폴더에서, run_calibration.py 다음에):
    .venv/bin/python cross_slope.py <주행 폴더> [--method median] [--window 120] [--lag 0.8]
    → <주행 폴더>/calibrated_xslope2/ (samples_100hz.npy 형식은 calibrated/ 와 같음, ay만 다름) + xslope.json
"""
import argparse
import json
import os
import shutil
import sqlite3

import numpy as np
from scipy.ndimage import uniform_filter1d
from scipy.signal import savgol_filter


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ride")
    ap.add_argument("--method", choices=["median", "mean"], default="median")
    ap.add_argument("--window", type=float, help="느린 부분을 고를 창 길이 [초] (기본: median 120, mean 30)")
    ap.add_argument("--straight", type=float, default=1.0, help="median: 직진으로 볼 |gz| 상한 [도/초]")
    ap.add_argument("--max-acc", type=float, default=10.0, help="median: 쓸 GPS 정확도 상한 [m]")
    ap.add_argument("--min-n", type=int, default=20, help="median: 창 안에 고른 초가 이보다 적으면 그 자리는 앞뒤 값으로 잇는다")
    ap.add_argument("--ramp", type=float, default=5.0, help="median: GPS 구간 경계에서 0과 이을 길이 [초]")
    ap.add_argument("--lag", type=float, default=0.8, help="GPS가 IMU보다 늦은 시간 [초] (gps_check.py 결과)")
    a = ap.parse_args()
    if a.window is None:
        a.window = 120.0 if a.method == "median" else 30.0

    db = os.path.join(a.ride, "logs", "rides", sorted(f for f in os.listdir(os.path.join(a.ride, "logs", "rides")) if f.endswith(".sqlite"))[0])
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    st = int(dict(con.execute("select key, value from meta").fetchall())["mono_start_ns"])
    first = con.execute("select mono_ns0 from samples order by seq0 limit 1").fetchone()[0]
    gps = np.array(con.execute(f"select (mono_ns - {st}) / 1e9, spd, acc from gps where spd is not null order by mono_ns").fetchall(), float)
    con.close()
    if len(gps) < 10:
        raise SystemExit("GPS 속도가 없어 할 수 없음 (이 주행은 선택 ①만 가능)")

    src = os.path.join(a.ride, "calibrated")
    cal = np.load(os.path.join(src, "samples_100hz.npy")).astype(float)
    t = np.arange(len(cal)) * 0.01                     # 보정 데이터는 정확히 100 Hz (파일의 t는 float32라 다시 만듦)
    t_abs = t + (first - st) / 1e9                     # GPS와 같은 시계
    ay, gz = cal[:, 2], cal[:, 6]

    # 1초 격자에서 계산 (GPS가 1초 간격)
    n1 = len(cal) // 100
    sec = np.arange(n1) + 0.5
    ay1 = ay[:n1 * 100].reshape(n1, 100).mean(1)
    gz1 = gz[:n1 * 100].reshape(n1, 100).mean(1)
    tg = gps[:, 0] - a.lag                             # GPS 시각을 IMU 쪽으로 당김
    have = (sec + (first - st) / 1e9 >= tg[0]) & (sec + (first - st) / 1e9 <= tg[-1])
    v1 = savgol_filter(np.interp(sec + (first - st) / 1e9, tg, gps[:, 1]), 5, 2)
    v1 = np.maximum(v1, 0.0)
    resid = ay1 - v1 * gz1
    resid[~have] = np.nan
    # GPS가 없는 구간은 진짜 좌우 가속을 모르므로 손대지 않는다 (뺄 값 0)
    idx = np.where(have)[0]
    slow = np.zeros(n1)
    if a.method == "mean":
        slow[have] = uniform_filter1d(resid[have], int(round(a.window)), mode="nearest")
        use = have
    else:
        gacc = np.interp(sec + (first - st) / 1e9, tg, gps[:, 2])
        use = have & (gacc <= a.max_acc) & (np.abs(np.degrees(gz1)) < a.straight)
        half = int(round(a.window / 2))
        est = np.full(n1, np.nan)
        for i in idx:
            w = np.where(use[max(0, i - half):i + half + 1])[0] + max(0, i - half)
            if len(w) >= a.min_n:
                est[i] = np.median(resid[w])
        ok = np.isfinite(est)
        if not ok.any():
            raise SystemExit("고를 수 있는 직진 구간이 없음")
        slow[have] = np.interp(sec[have], sec[ok], est[ok])
        # 경계: 0 → 값 을 ramp 초에 걸쳐 (계단 방지)
        r = max(int(round(a.ramp)), 1)
        w = np.ones(n1)
        w[idx[0]:idx[0] + r] = np.linspace(0, 1, r + 1)[1:]
        w[idx[-1] - r + 1:idx[-1] + 1] = np.linspace(1, 0, r + 1)[:-1]
        slow = slow * w
    slow100 = np.interp(t, sec, slow)

    out = cal.astype(np.float32).copy()
    out[:, 2] = (ay - slow100).astype(np.float32)
    dst = os.path.join(a.ride, "calibrated_xslope2" if a.method == "median" else "calibrated_xslope")
    os.makedirs(dst, exist_ok=True)
    np.save(os.path.join(dst, "samples_100hz.npy"), out)
    shutil.copy(os.path.join(src, "calibration.json"), os.path.join(dst, "calibration.json"))
    moving = have & (v1 > 2)
    info = {"input": "calibrated/samples_100hz.npy", "method": a.method, "window_s": a.window, "gps_lag_s": a.lag,
            **({"straight_dps": a.straight, "max_gps_acc_m": a.max_acc, "min_n": a.min_n, "ramp_s": a.ramp,
                "used_seconds": int(use.sum()), "gps_seconds": int(have.sum())} if a.method == "median" else {}),
            "gps_covered_s": [float(sec[idx[0]]), float(sec[idx[-1]])],
            "removed_ay_median_moving": float(np.median(slow[moving])),
            "removed_ay_p10_p90_moving": [float(np.percentile(slow[moving], 10)), float(np.percentile(slow[moving], 90))],
            "removed_ay_median_stopped": float(np.median(slow[have & (v1 < 0.5)])) if (have & (v1 < 0.5)).any() else None,
            "note": "ay에서 (ay − v·gz)의 느린 부분을 뺌. ax, az, 자이로는 calibrated 와 같음"}
    if a.method == "median":
        sec_abs = sec + (first - st) / 1e9
        info["removed_ay_by_minute_moving"] = {f"{int(m)}": round(float(np.median(slow[moving & (sec // 60 == m)])), 3)
                                               for m in np.unique(sec[moving] // 60)}
    json.dump(info, open(os.path.join(dst, "xslope.json"), "w"), ensure_ascii=False, indent=1)
    print(json.dumps(info, ensure_ascii=False, indent=1))

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.family"] = "Apple SD Gothic Neo"
    plt.rcParams["axes.unicode_minus"] = False
    fig, ax = plt.subplots(2, 1, figsize=(13, 6), sharex=True)
    ax[0].plot(sec, resid, color="#bbb", lw=0.6, label="ay - v·gz (1초)")
    if a.method == "median":
        ax[0].plot(sec[use], resid[use], ".", color="#555", ms=1.5, label=f"쓴 초 (직진, GPS ≤{a.max_acc:.0f} m)")
    ax[0].plot(sec, slow, color="#c0392b", lw=1.5, label=f"뺀 값 ({a.window:.0f}초 {'중앙값' if a.method == 'median' else '평균'})")
    ax[0].axhline(0, color="k", lw=0.5); ax[0].set_ylim(-1.5, 1.5); ax[0].legend(loc="upper right")
    ax[0].set_ylabel("m/s²"); ax[0].set_title("움직임이 아닌 좌우 값 (+ = 왼쪽, 1 m/s² ≈ 5.8도)")
    ax[1].plot(sec, uniform_filter1d(ay1, 10), color="#c0392b", lw=0.8, label="보정 후 ay (10초 평균)")
    ax[1].plot(sec, uniform_filter1d(ay1 - slow, 10), color="#1f6fb2", lw=0.8, label="횡단 경사 뺀 ay")
    ax[1].plot(sec, uniform_filter1d(v1 * gz1, 10), color="k", lw=0.6, ls="--", label="GPS 원심 가속도 v·gz")
    ax[1].axhline(0, color="k", lw=0.5); ax[1].set_ylim(-1.5, 1.5); ax[1].legend(loc="upper right")
    ax[1].set_xlabel("주행 시작부터 [초]"); ax[1].set_ylabel("m/s²")
    fig.tight_layout(); fig.savefig(os.path.join(dst, "xslope.png"), dpi=130); plt.close(fig)
    print("저장:", dst)


if __name__ == "__main__":
    main()
