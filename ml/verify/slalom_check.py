"""좌우 슬라롬 집단 비교 (Irmak, Pool, Happee 2021, Exp. Brain Res. 239:515)

    python3 ml/verify/slalom_check.py      # 먼저 oman_tudelft.py로 out/K_lpf.npy를 만든다

TU Delft 17명의 개인별 K(앞뒤 자극으로 맞춘 값)로 좌우 0.2 Hz, 0.4 g 슬라롬을 모의실험해
논문 지표(MISC 상승 속도, 평균 MISC, 중단 비율)와 비교한다. 1분마다 정수 MISC, 7에서 중단.
"""
import pathlib
import sys

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import oman  # noqa: E402

DT = 0.01
K = np.load(pathlib.Path(__file__).with_name("out") / "K_lpf.npy")
T = 1800
t = np.arange(0, T, DT)
A = 0.4 * 9.81


def slalom(cycle=49.0, stretch=40.0):
    ph = t % cycle
    return np.where(ph < stretch, A * np.sin(2 * np.pi * 0.2 * ph), 0.0)


for name, ay in [("continuous", A * np.sin(2 * np.pi * 0.2 * t)), ("40 s slalom + 9 s turn", slalom())]:
    c = oman.input_signal(np.zeros_like(ay), ay, DT)
    rates, means, drop = [], [], []
    mins = np.arange(1, 31)
    for k in K:
        m = np.round(np.interp(mins * 60, t, oman.run(c, k, DT)[0]))
        hit = np.where(m >= 7)[0]
        if len(hit):
            i = hit[0]
            drop.append(1)
            rates.append(7 / mins[i])
            m[i:] = 7
        else:
            drop.append(0)
            rates.append(m[-1] / 30)
        means.append(m.mean())
    print("%-24s median rate %.2f /min | mean MISC %.1f | dropouts %.0f%%" % (name, np.median(rates), np.mean(means), 100 * np.mean(drop)))
print("paper internal vision : median rate 0.59 /min | mean MISC 4.2 | dropouts 66%")
print("paper external vision : median rate 0.10 /min | mean MISC 2.3 | dropouts 33%")
