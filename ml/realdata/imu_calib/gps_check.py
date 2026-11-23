"""V7: 보정된 IMU를 폰 GPS 속도와 맞대어 검증한다.

쓰는 법 (맥 터미널):
    .venv/bin/python gps_check.py <주행 폴더> --drive-window 150 1600

run_calibration.py 를 먼저 돌려 <주행 폴더>/calibrated/ 가 있어야 한다.
GPS는 P7 기록의 gps 표(폰이 1초마다 보낸 속도 spd [m/s], 진행 방향 brg [도], 고도 alt [m])를 쓴다.

GPS는 IMU와 완전히 다른 방법으로 잰 값이라, 이것과 맞으면 보정이 맞다는 독립된 증거가 된다.
  ① 앞뒤:  ax ≈ d(속도)/dt              → 기울기 1, 절편 0 이어야 한다
  ② 좌우:  ay ≈ 속도 × 회전 속도(gz)      → 기울기 1, 절편 0
  ③ 자이로: 20초 동안 gz를 적분한 방향 변화 ≈ GPS 진행 방향 변화 → 기울기 1 (자이로 눈금 확인)
     (초 단위로 미분해 비교하면 폰이 진행 방향을 매끈하게 만들어 보내서 기울기가 0.5로 나온다. 쓰지 않는다)
  ④ 남는 것: ax − d(속도)/dt = 도로 경사로 들어온 중력(+ 차체 숙임) → 5단계 재료.
     GPS 고도로 구한 경사(g × 고도 변화 / 이동 거리)와 비교한다.
보정 전(원본)과 보정 후를 같은 식으로 계산해 나란히 보여 준다.
"""
import argparse
import json
import os
import sqlite3

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.ndimage import uniform_filter1d
from scipy.signal import savgol_filter

G = 9.80665
plt.rcParams["font.family"] = "Apple SD Gothic Neo"
plt.rcParams["axes.unicode_minus"] = False


def per_second(t, x, grid):
    """t 시각의 값 x 를 grid 각 초를 중심으로 한 1초 평균으로."""
    idx = np.searchsorted(t, grid - 0.5), np.searchsorted(t, grid + 0.5)
    cs = np.vstack([np.zeros((1, x.shape[1])), np.cumsum(x, axis=0)])
    n = np.maximum(idx[1] - idx[0], 1)[:, None]
    return (cs[idx[1]] - cs[idx[0]]) / n


def fit(x, y):
    """y = k·x + b 최소제곱. (k, b, 상관, 잔차 표준편차)"""
    k, b = np.polyfit(x, y, 1)
    return float(k), float(b), float(np.corrcoef(x, y)[0, 1]), float(np.std(y - (k * x + b)))


def heading_fit(t, gz, gps, lag, win=20.0, min_turn_deg=20.0):
    """20초 창마다 (GPS 진행 방향 변화, 자이로 적분 방향 변화)를 모아 직선을 맞춘다. 단위: 도."""
    ok = np.isfinite(gps[:, 2])
    tg, v, hd = gps[ok, 0], gps[ok, 1], -np.degrees(np.unwrap(np.radians(gps[ok, 2])))   # 반시계 +
    psi = np.degrees(np.cumsum(gz) * np.r_[0.01, np.diff(t)])
    xs, ys = [], []
    for t0 in np.arange(tg[0], tg[-1] - win, win):
        m = (tg >= t0) & (tg <= t0 + win) & (v > 4)
        if m.sum() < 0.75 * win:
            continue
        i0, i1 = np.where(m)[0][[0, -1]]
        dg = hd[i1] - hd[i0]
        if abs(dg) < min_turn_deg:
            continue
        xs.append(dg)
        ys.append(np.interp(tg[i1] - lag, t, psi) - np.interp(tg[i0] - lag, t, psi))
    if len(xs) < 4:
        return float("nan"), float("nan"), float("nan"), float("nan")
    return fit(np.array(xs), np.array(ys))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ride")
    ap.add_argument("--drive-window", nargs=2, type=float, required=True, metavar=("T0", "T1"))
    ap.add_argument("--min-speed", type=float, default=2.0, help="이 속도[m/s] 이상인 초만 비교")
    a = ap.parse_args()

    db = os.path.join(a.ride, "logs", "rides", sorted(os.listdir(os.path.join(a.ride, "logs", "rides")))[0])
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    st = int(dict(con.execute("select key, value from meta").fetchall())["mono_start_ns"])
    first = con.execute("select mono_ns0 from samples order by seq0 limit 1").fetchone()[0]
    gps = np.array(con.execute(f"select (mono_ns - {st}) / 1e9, spd, brg, alt from gps order by mono_ns").fetchall(), float)
    con.close()

    cal = np.load(os.path.join(a.ride, "calibrated", "samples_100hz.npy")).astype(float)
    raw = np.load(os.path.join(a.ride, "raw_caraxis", "samples_100hz.npy")).astype(float)
    t_cal = cal[:, 0] + (first - st) / 1e9          # 보정 데이터: 진짜 시각 (첫 샘플 = 첫 묶음 시각)
    t_raw = raw[:, 0] + (first - st) / 1e9          # 원본: 샘플 번호 / 100 (0.07% 밀림, 비교용으로는 무시)

    grid = np.arange(np.ceil(a.drive_window[0]), np.floor(a.drive_window[1]))
    v = np.interp(grid, gps[:, 0], gps[:, 1])
    vs = savgol_filter(v, 5, 2)
    dv = np.gradient(vs)                                    # GPS 속도의 변화율 [m/s²]
    ok_brg = np.isfinite(gps[:, 2])
    hd = np.unwrap(np.radians(gps[ok_brg, 2]))
    hdg = np.interp(grid, gps[ok_brg, 0], hd)
    yaw_gps = -np.gradient(savgol_filter(hdg, 5, 2))        # 진행 방향은 시계 방향이 + → 반시계(+z)로 바꿈
    alt = uniform_filter1d(np.interp(grid, gps[:, 0], np.nan_to_num(gps[:, 3], nan=np.nanmedian(gps[:, 3]))), 15)
    ds = uniform_filter1d(v, 15) * 15
    grade = np.where(ds > 20, (np.roll(alt, -7) - np.roll(alt, 8)) / np.maximum(ds, 1e-9), np.nan)

    moving = v > a.min_speed
    res = {}
    lines = ["# GPS 대조 검증 (V7)", "", f"주행: `{os.path.abspath(a.ride)}`, 비교 구간 {grid[0]:.0f}~{grid[-1]:.0f}초, "
             f"GPS {len(gps)}점, 속도 {a.min_speed} m/s 이상인 {int(moving.sum())}초 사용", ""]

    # GPS 속도는 폰에서 계산되어 IMU보다 늦게 도착한다. 늦은 만큼(지연)을 먼저 찾는다.
    s_cal0 = per_second(t_cal, cal[:, 1:7], grid)
    lags = np.arange(-3.0, 3.01, 0.1)
    cors = []
    for L in lags:
        s = per_second(t_cal, cal[:, 1:2], grid - L)[:, 0]
        cors.append(np.corrcoef(savgol_filter(s, 5, 2)[moving], dv[moving])[0, 1])
    lag = float(lags[int(np.argmax(cors))])
    lines += [f"GPS 지연: IMU보다 **{lag:+.1f}초** 늦음 (앞뒤 가속도 상관이 가장 큰 지연, 상관 {max(cors):.2f}). 아래 비교는 이만큼 당겨서 함.", ""]

    lines += ["| 항목 | 기대 | 보정 전 기울기 / 절편 / 상관 | 보정 후 기울기 / 절편 / 상관 |", "|---|---|---|---|"]
    out_series = {}
    for name, (tt, d) in {"보정 전": (t_raw, raw), "보정 후": (t_cal, cal)}.items():
        s = per_second(tt, d[:, 1:7], grid - lag)
        ax_s = savgol_filter(s[:, 0], 5, 2)                 # GPS 쪽과 같은 정도로 매끈하게
        ay = s[:, 1]
        gz = s[:, 5]
        res[name] = {
            "long": fit(dv[moving], ax_s[moving]),
            "lat": fit((v * gz)[moving], ay[moving]),
            "gyro": heading_fit(tt, d[:, 6], gps, lag),
        }
        out_series[name] = (ax_s, ay, gz)
    rows = [("① 앞뒤: ax vs d(속도)/dt", "long", "1 / 0 / 높게"),
            ("② 좌우: ay vs 속도 × gz", "lat", "1 / 0 / 높게"),
            ("③ 자이로: 20초 방향 변화, IMU vs GPS [도]", "gyro", "1 / 0 / 높게")]
    for label, key, exp in rows:
        b, c = res["보정 전"][key], res["보정 후"][key]
        lines.append(f"| {label} | {exp} | {b[0]:.3f} / {b[1]:+.3f} / {b[2]:.2f} | {c[0]:.3f} / {c[1]:+.3f} / {c[2]:.2f} |")
    lines += ["", "절편이 0에서 벗어난 만큼이 그 축에 남은 평균 오프셋(앞뒤는 평균 도로 경사도 포함)이다. "
              "기울기가 1보다 작은 것은 GPS 속도가 IMU보다 매끈해서(폰이 걸러서 보냄) 생기는 쪽이 크다.", ""]

    ax_c = out_series["보정 후"][0]
    resid = ax_c - dv                                       # 도로 경사 중력 + 차체 숙임 + 남은 오프셋
    resid10 = uniform_filter1d(resid, 10)
    ok = moving & np.isfinite(grade)
    cg = np.corrcoef(resid10[ok], G * grade[ok])[0, 1] if ok.sum() > 30 else float("nan")
    lines += ["## ④ 남는 앞뒤 가속도 = 도로 경사 중력 (5단계 재료)", "",
              f"- ax − d(속도)/dt 의 10초 평균: 중앙값 {np.median(resid10[moving]):+.3f}, "
              f"10~90% 범위 {np.percentile(resid10[moving], 10):+.3f} ~ {np.percentile(resid10[moving], 90):+.3f} m/s² "
              f"(= 경사 {np.degrees(np.arcsin(np.clip(np.percentile(resid10[moving], 10) / G, -1, 1))):+.1f} ~ "
              f"{np.degrees(np.arcsin(np.clip(np.percentile(resid10[moving], 90) / G, -1, 1))):+.1f}도)",
              f"- GPS 고도로 구한 경사 × g 와의 상관: {cg:.2f} (폰 고도는 오차가 커서 참고용)", ""]

    out = os.path.join(a.ride, "calibrated")
    json.dump({"lag_s": lag, "fits": {k: {kk: dict(zip(["slope", "intercept", "corr", "resid_sd"], vv)) for kk, vv in v_.items()} for k, v_ in res.items()},
               "grade_resid_median": float(np.median(resid10[moving])), "grade_corr_alt": float(cg)},
              open(os.path.join(out, "gps_check.json"), "w"), indent=1, ensure_ascii=False)
    open(os.path.join(out, "gps_check.md"), "w").write("\n".join(lines) + "\n")

    # ---- 그림
    fig = plt.figure(figsize=(14, 10))
    g1 = fig.add_subplot(3, 1, 1)
    g1.plot(grid, dv, color="#444", lw=0.8, label="GPS 속도 변화율")
    g1.plot(grid, ax_c, color="#1f6fb2", lw=0.8, label="IMU ax (보정 후)")
    g1.set_ylabel("앞뒤 [m/s²]"); g1.legend(loc="upper right"); g1.axhline(0, color="k", lw=0.4)
    g1.set_title(f"① 앞뒤: IMU와 GPS (GPS 지연 {lag:+.1f}초 보정)")
    g2 = fig.add_subplot(3, 1, 2, sharex=g1)
    g2.plot(grid, v * out_series["보정 후"][2], color="#444", lw=0.8, label="GPS 속도 × 회전 속도")
    g2.plot(grid, out_series["보정 후"][1], color="#1f6fb2", lw=0.8, label="IMU ay (보정 후)")
    g2.set_ylabel("좌우 [m/s²]"); g2.legend(loc="upper right"); g2.axhline(0, color="k", lw=0.4)
    g3 = fig.add_subplot(3, 1, 3, sharex=g1)
    g3.plot(grid, resid10, color="#c0392b", lw=1, label="ax - GPS 속도 변화율 (10초 평균)")
    g3.plot(grid, G * grade, color="#888", lw=0.8, label="g × GPS 고도 경사 (참고)")
    g3.axhline(0, color="k", lw=0.4); g3.set_ylim(-1.5, 1.5)
    g3.set_ylabel("남는 앞뒤 [m/s²]"); g3.set_xlabel("주행 시작부터 [초]"); g3.legend(loc="upper right")
    g3.set_title("④ 남는 것 = 도로 경사로 들어온 중력 (+ 차체 숙임)")
    fig.tight_layout(); fig.savefig(os.path.join(out, "gps_check_시간.png"), dpi=120); plt.close(fig)

    fig, axs = plt.subplots(1, 2, figsize=(12, 5.5))
    for k, (x, y, xl, yl) in enumerate([(dv, ax_c, "GPS 속도 변화율 [m/s²]", "IMU ax [m/s²]"),
                                        (v * out_series["보정 후"][2], out_series["보정 후"][1], "GPS 속도 × gz [m/s²]", "IMU ay [m/s²]")]):
        axs[k].scatter(x[moving], y[moving], s=4, alpha=0.4)
        lim = [-4, 4]; axs[k].plot(lim, lim, "k--", lw=0.8, label="기대 (기울기 1)")
        axs[k].set_xlim(lim); axs[k].set_ylim(lim); axs[k].set_aspect("equal")
        axs[k].set_xlabel(xl); axs[k].set_ylabel(yl); axs[k].legend()
    axs[0].set_title("① 앞뒤"); axs[1].set_title("② 좌우")
    fig.tight_layout(); fig.savefig(os.path.join(out, "gps_check_산점도.png"), dpi=120); plt.close(fig)

    print("\n".join(lines))
    print("저장:", out)


if __name__ == "__main__":
    main()
