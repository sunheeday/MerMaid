"""P2 실시간 횡단 경사 제거(controller/common/cross_slope.hpp)를 맥에서 똑같이 돌려 본다.

cross_slope.py(오프라인)와 다른 점: 지난 값만 쓴다(창이 현재 초에서 끝남), GPS는 받은 시각 그대로(P6 → P2),
GPS 늦음은 IMU를 lag초 늦춰 맞춘다, 고른 초가 모자라면 마지막 추정을 유지, 뺄 값은 초당 rate까지만 바뀐다.

    1초마다 (100샘플):  ay1, gz1 = lag초 전 1초 IMU 평균,  v1, acc1 = 이번 1초 GPS 속도·정확도 평균
                        쓸 초 = v1 있음 & acc1 <= max_acc & |gz1| < straight
                        남는 값 = ay1 − v1·gz1 → 지난 window초 버퍼
                        추정 = 버퍼 안 쓸 초 >= min_n 이면 중앙값, 아니면 이전 추정 (처음엔 없음 = 0)
    샘플마다:            뺄 값이 추정을 향해 샘플당 rate/100 만큼 움직임,  ay ← ay − 뺄 값

쓰는 법:
    .venv/bin/python cross_slope_rt.py <주행 폴더> [--window 30] [--min-n 10]    → calibrated_xslope_rt/
    .venv/bin/python cross_slope_rt.py <주행 폴더> --compare 30 60 120 240     → 창 길이 비교 표 (저장 안 함)
"""
import argparse
import json
import os
import shutil
import sqlite3

import numpy as np
from scipy.ndimage import uniform_filter1d


def run_rt(ay, gz, spd, acc, window=30, straight_dps=1.0, max_acc=10.0, min_n=10, rate=0.1, lag=0.8):
    """C++ CrossSlope와 같은 계산. 입력은 100 Hz 배열(spd/acc는 NaN = 없음). 돌려줌: (뺄 값 100 Hz, 1초 기록)"""
    n = len(ay)
    d = int(round(lag * 100))
    win = int(round(window))
    buf_r, buf_u = [], []
    est, have_est, applied = 0.0, False, 0.0
    out = np.zeros(n)
    sec = []
    sa = sg = 0.0
    sv = sacc = 0.0
    nv = nacc = 0
    k = 0
    step = rate / 100.0
    for i in range(n):
        j = i - d                     # lag 늦춘 IMU
        a_d = ay[j] if j >= 0 else np.nan
        g_d = gz[j] if j >= 0 else np.nan
        sa += a_d if j >= 0 else 0.0
        sg += g_d if j >= 0 else 0.0
        if np.isfinite(spd[i]):
            sv += spd[i]; nv += 1
        if np.isfinite(acc[i]):
            sacc += acc[i]; nacc += 1
        k += 1
        if k == 100:
            imu_ok = j - 99 >= 0
            v1 = max(sv / nv, 0.0) if nv == 100 else np.nan        # 1초 내내 속도가 있어야
            a1 = sacc / nacc if nacc else np.nan                     # 정확도 모름(재생)은 통과
            ay1, gz1 = sa / 100, sg / 100
            ok = imu_ok and np.isfinite(v1)
            use = ok and (not np.isfinite(a1) or a1 <= max_acc) and abs(np.degrees(gz1)) < straight_dps
            r = ay1 - v1 * gz1 if ok else np.nan
            buf_r.append(r); buf_u.append(use)
            if len(buf_r) > win:
                buf_r.pop(0); buf_u.pop(0)
            w = [x for x, u in zip(buf_r, buf_u) if u]
            if len(w) >= min_n:
                est = float(np.median(w)); have_est = True
            sec.append((i / 100.0, r, use, est if have_est else np.nan, applied))
            sa = sg = sv = sacc = 0.0
            nv = nacc = k = 0
        target = est if have_est else 0.0
        applied += float(np.clip(target - applied, -step, step))
        out[i] = applied
    return out, np.array(sec, float)


def load(ride):
    rides = os.path.join(ride, "logs", "rides")
    db = os.path.join(rides, sorted(f for f in os.listdir(rides) if f.endswith(".sqlite"))[0])
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    first = con.execute("select mono_ns0 from samples order by seq0 limit 1").fetchone()[0]
    gps = np.array(con.execute(f"select (mono_ns - {first}) / 1e9, spd, acc from gps where spd is not null order by mono_ns").fetchall(), float)
    con.close()
    cal = np.load(os.path.join(ride, "calibrated", "samples_100hz.npy")).astype(float)
    t = np.arange(len(cal)) * 0.01
    # P2 SpeedHold(폰 1대): 가장 최근 값, 3초 넘으면 없음
    idx = np.searchsorted(gps[:, 0], t, side="right") - 1
    okg = (idx >= 0)
    age = np.where(okg, t - gps[np.maximum(idx, 0), 0], np.inf)
    fresh = okg & (age <= 3.0)
    spd = np.where(fresh, gps[np.maximum(idx, 0), 1], np.nan)
    acc = np.where(fresh, gps[np.maximum(idx, 0), 2], np.nan)
    return cal, t, spd, acc


def metrics(cal, spd, applied, lag=0.8, straight_dps=1.0, max_acc=10.0, acc=None, t0=None, t1=None):
    """1초 격자에서: 직진·GPS 좋은 초의 (ay − v·gz − 뺄 값)의 느린 성분(20초 평균)이 0에서 얼마나 벗어나나"""
    n1 = len(cal) // 100
    d = int(round(lag * 100))
    ay = np.r_[np.full(d, np.nan), cal[:, 2]][:len(cal)]
    gz = np.r_[np.full(d, np.nan), cal[:, 6]][:len(cal)]
    m = lambda x: x[:n1 * 100].reshape(n1, 100).mean(1)
    ay1, gz1, v1, ap1 = m(ay), m(gz), m(spd), m(np.r_[np.full(d, 0.0), applied][:len(cal)])
    a1 = m(acc)
    sec = np.arange(n1) + 0.5
    use = np.isfinite(ay1) & np.isfinite(v1) & (a1 <= max_acc) & (np.abs(np.degrees(gz1)) < straight_dps) & (v1 > 2)
    if t0 is not None:
        use &= (sec >= t0) & (sec <= t1)
    r = ay1 - np.maximum(v1, 0) * gz1
    res = {}
    for name, x in (("before", r), ("after", r - ap1)):
        y = np.where(use, x, np.nan)
        s20 = np.array([np.nanmean(y[max(0, i - 10):i + 10]) if np.isfinite(y[max(0, i - 10):i + 10]).sum() >= 5 else np.nan for i in range(n1)])
        ok = use & np.isfinite(s20)
        res[name] = {"mean": float(np.mean(x[use])), "slow20_rms": float(np.sqrt(np.mean(s20[ok] ** 2)))}
    return res, int(use.sum())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ride")
    ap.add_argument("--window", type=float, default=30)
    ap.add_argument("--min-n", type=int, default=10)
    ap.add_argument("--compare", type=float, nargs="*")
    ap.add_argument("--rate", type=float, default=0.1)
    ap.add_argument("--from", dest="t0", type=float, default=150)
    ap.add_argument("--until", type=float, default=1600)
    a = ap.parse_args()
    cal, t, spd, acc = load(a.ride)
    offline = None
    off_path = os.path.join(a.ride, "calibrated_xslope2", "samples_100hz.npy")
    if os.path.exists(off_path):
        offline = cal[:, 2] - np.load(off_path).astype(float)[:, 2]
    if a.compare:
        rows = []
        for w in a.compare:
            applied, sec = run_rt(cal[:, 2], cal[:, 6], spd, acc, window=w, min_n=a.min_n, rate=a.rate)
            res, nuse = metrics(cal, spd, applied, acc=acc, t0=a.t0, t1=a.until)
            rng = (t >= a.t0) & (t <= a.until)
            dif = float(np.sqrt(np.mean((applied - offline)[rng] ** 2))) if offline is not None else np.nan
            jump = float(np.max(np.abs(np.diff(applied[rng][::100]))))
            rows.append((w, res, dif, jump, float(np.median(applied[rng]))))
        if offline is not None:
            res, nuse = metrics(cal, spd, offline, acc=acc, t0=a.t0, t1=a.until)
            rows.append(("오프라인 120 (앞뒤)", res, 0.0, float(np.max(np.abs(np.diff(offline[(t >= a.t0) & (t <= a.until)][::100])))),
                         float(np.median(offline[(t >= a.t0) & (t <= a.until)]))))
        print(f"평가 초 {nuse} (직진, GPS ≤10 m, 속도 > 2 m/s, {a.t0:.0f}~{a.until:.0f}초)")
        print(f"보정 전: 평균 {rows[0][1]['before']['mean']:+.3f}, 느린 성분(20초) RMS {rows[0][1]['before']['slow20_rms']:.3f} m/s²")
        print("| 창 [초] | 뺀 뒤 평균 | 뺀 뒤 느린 성분 RMS | 오프라인과 차이 RMS | 1초 최대 변화 | 뺀 값 중앙값 |")
        print("|---|---|---|---|---|---|")
        for w, res, dif, jump, med in rows:
            print(f"| {w} | {res['after']['mean']:+.3f} | {res['after']['slow20_rms']:.3f} | {dif:.3f} | {jump:.3f} | {med:+.3f} |")
        return
    applied, sec = run_rt(cal[:, 2], cal[:, 6], spd, acc, window=a.window, min_n=a.min_n, rate=a.rate)
    out = cal.astype(np.float32).copy()
    out[:, 2] = (cal[:, 2] - applied).astype(np.float32)
    dst = os.path.join(a.ride, "calibrated_xslope_rt")
    os.makedirs(dst, exist_ok=True)
    np.save(os.path.join(dst, "samples_100hz.npy"), out)
    shutil.copy(os.path.join(a.ride, "calibrated", "calibration.json"), os.path.join(dst, "calibration.json"))
    np.savetxt(os.path.join(dst, "xslope_rt_sec.csv"), sec, delimiter=",", header="t,resid,use,est,applied", comments="", fmt="%.5f")
    info = {"method": "realtime (P2 cross_slope.hpp mirror)", "window_s": a.window, "min_n": a.min_n, "rate_mps2_per_s": a.rate,
            "removed_median": float(np.median(applied[(t >= a.t0) & (t <= a.until)]))}
    json.dump(info, open(os.path.join(dst, "xslope_rt.json"), "w"), ensure_ascii=False, indent=1)
    print(json.dumps(info, ensure_ascii=False), "→", dst)


if __name__ == "__main__":
    main()
