"""TU Delft ATDOMS로 Oman 구현(ml/oman.py) 검증 (S1)

    python3 ml/verify/oman_tudelft.py ml/data/raw/ATDOMS.mat [--hz 100]

1. 자극 재구성: 앞뒤 0.3 Hz 사인파, C1~C4 = 1 / 1.5 / 2 / 2.5 m/s², 시작과 끝 10초 램프
2. 진폭 순서 확인: 24가지 순서 중 공통 K = 70.8에서 가장 잘 맞는 것
3. 사람마다 K 하나만 맞춤 (시간상수, 지수 고정). 입력 필터 없음(raw) / 0.63 Hz(lpf)
4. 한 명씩 빼고 예측(LOPO): K0만 / K0 x MSSQ 민감도. TU Delft 평가의 Oman 단독 기준선
합격: raw 맞춤 MAE 0.97 +- 0.05, constants.json의 K0가 lpf 중앙값과 1% 안에서 같음
결과: ml/verify/out/oman_tudelft.json, K_raw.npy, K_lpf.npy, mssq.npy
"""
import argparse
import itertools
import json
import pathlib
import sys

import numpy as np
import scipy.io as sio
from scipy.optimize import minimize_scalar

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import oman  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("mat")
ap.add_argument("--hz", type=float, default=100.0)
args = ap.parse_args()
DT = 1.0 / args.hz
F_STIM = 0.3
AMPS = [1.0, 1.5, 2.0, 2.5]
C = ["C1", "C2", "C3", "C4"]
OUT = pathlib.Path(__file__).with_name("out")
OUT.mkdir(exist_ok=True)

A = sio.loadmat(args.mat, squeeze_me=True, struct_as_record=False)["ATDOMS"]
P = list(A._fieldnames)
arr = lambda x: np.atleast_1d(x).astype(float)


def session(p, c):
    """자극 구간 [(시작, 끝)]과 MISC 보고 (시각, 점수)."""
    S = getattr(getattr(A, p), c)
    ph = {k: (arr(getattr(S, k).misc.time), arr(getattr(S, k).misc.score)) for k in ["Phase1", "Rest1", "Phase2", "Rest2"]}
    if len(ph["Rest1"][0]) == 0:
        seg = [(0.0, ph["Phase1"][0][-1] + 30)]
    else:
        seg = [(0.0, ph["Rest1"][0][0])]
        if len(ph["Phase2"][0]):
            seg.append((ph["Phase2"][0][0], ph["Rest2"][0][0] if len(ph["Rest2"][0]) else ph["Phase2"][0][-1] + 30))
    t = np.concatenate([ph[k][0] for k in ph])
    y = np.concatenate([ph[k][1] for k in ph])
    return seg, t, y


def stimulus(seg, T, amp):
    t = np.arange(0, T, DT)
    a = np.zeros_like(t)
    for s, e in seg:
        m = (t >= s) & (t < e)
        ramp = np.minimum(1, np.minimum((t[m] - s) / 10, (e - t[m]) / 10))
        a[m] = ramp * amp * np.sin(2 * np.pi * F_STIM * (t[m] - s))
    return t, a


SES = {(p, c): session(p, c) for p in P for c in C}
LP = {"raw": None, "lpf": oman.OMAN["input_lowpass_hz"]}
_cache = {}


def inp(p, c, amp, mode):
    key = (p, c, amp, mode)
    if key not in _cache:
        seg, t, _ = SES[(p, c)]
        tt, a = stimulus(seg, t[-1] + 60, amp)
        _cache[key] = (tt, oman.input_signal(a, None, DT, LP[mode]))
    return _cache[key]


def abs_err(p, K, mode, amps=AMPS):
    e = []
    for i, c in enumerate(C):
        _, t, y = SES[(p, c)]
        tt, x = inp(p, c, amps[i], mode)
        e.append(np.abs(np.interp(t, tt, oman.run(x, K, DT)[0]) - y))
    return np.concatenate(e)  # P_09 C3의 NaN 보고는 nanmean으로 빠짐


def fit_k(p, mode):
    r = minimize_scalar(lambda lk: np.nanmean(abs_err(p, np.exp(lk), mode)),
                        bounds=(np.log(0.1), np.log(1e5)), method="bounded")
    return float(np.exp(r.x)), float(r.fun)


res = {"hz": args.hz}

# 2. 진폭 순서
maps = sorted((float(np.nanmean(np.concatenate([abs_err(p, 70.8, "raw", perm) for p in P]))), perm)
              for perm in itertools.permutations(AMPS))
res["mapping"] = {"best": maps[0][1], "best_mae": maps[0][0], "second": maps[1][1], "second_mae": maps[1][0]}
print(f"진폭 순서 (K=70.8): 1위 {maps[0][1]} MAE {maps[0][0]:.3f}, 2위 {maps[1][1]} MAE {maps[1][0]:.3f}")

# 3. 개인별 K
mssq = np.array([float(getattr(A, p).MSSQ.Score) for p in P])
np.save(OUT / "mssq.npy", mssq)
K = {}
for mode in LP:
    fits = [fit_k(p, mode) for p in P]
    K[mode] = np.array([k for k, _ in fits])
    np.save(OUT / f"K_{mode}.npy", K[mode])
    mae = float(np.mean([e for _, e in fits]))
    q1, med, q3 = np.percentile(K[mode], [25, 50, 75])
    res[mode] = {"K_median": float(med), "K_iqr": [float(q1), float(q3)], "fit_mae": mae}
    print(f"[{mode}] 개인별 K 중앙값 {med:.1f} (IQR {q1:.1f}-{q3:.1f}), 맞춤 MAE {mae:.3f}")

# 4. LOPO 기준선 (lpf, 실제 사용 조건)
z = (mssq - oman.CONST["mssq"]["norm_mean"]) / oman.CONST["mssq"]["norm_sd"]
coef = oman.CONST["mssq"]["sens_coef"]
lopo = {}
for name, s in [("K0_only", 0.0), ("K0_x_mssq", coef)]:
    E = []
    for i, p in enumerate(P):
        m = np.arange(len(P)) != i
        k0 = np.exp(np.median(np.log(K["lpf"][m]) - s * z[m]))
        E.append(float(np.nanmean(abs_err(p, k0 * oman.sensitivity(mssq[i]) if s else k0, "lpf"))))
    lopo[name] = float(np.mean(E))
res["lopo_lpf"] = lopo
print(f"LOPO (lpf): K0만 MAE {lopo['K0_only']:.3f}, K0 x 민감도 MAE {lopo['K0_x_mssq']:.3f}")

(OUT / "oman_tudelft.json").write_text(json.dumps(res, indent=1, ensure_ascii=False))

fail = []
if abs(res["raw"]["fit_mae"] - 0.97) > 0.05:
    fail.append(f"raw 맞춤 MAE {res['raw']['fit_mae']:.3f}가 0.97 +- 0.05 밖")
k0 = oman.OMAN["K0"]
if abs(k0 / res["lpf"]["K_median"] - 1) > 0.01:
    fail.append(f"constants.json K0 {k0}가 lpf 중앙값 {res['lpf']['K_median']:.1f}와 다름")
print("RESULT: " + ("FAIL\n  " + "\n  ".join(fail) if fail else "PASS"))
sys.exit(1 if fail else 0)
