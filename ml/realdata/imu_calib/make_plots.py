"""보정 전후 비교 그래프를 만든다.

쓰는 법 (맥 터미널):
    .venv/bin/python make_plots.py <주행 폴더> <그림을 둘 폴더>

run_calibration.py 를 먼저 돌려 <주행 폴더>/calibrated/ 가 있어야 한다.
멀미 누적 점수(Oman)는 저장소의 ml/oman.py 를 그대로 불러 쓴다(--repo).
"""
import argparse
import json
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

plt.rcParams["font.family"] = "Apple SD Gothic Neo"
plt.rcParams["axes.unicode_minus"] = False
BEFORE, AFTER = "#c0392b", "#1f6fb2"


def sec_mean(x, n=100):
    k = len(x) // n
    return x[:k * n].reshape(k, n, -1).mean(axis=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ride")
    ap.add_argument("outdir")
    ap.add_argument("--zoom", nargs=2, type=float, default=(300, 700), metavar=("T0", "T1"),
                    help="그림 3에서 확대해 볼 구간 [초]")
    ap.add_argument("--still-until", type=float, default=320, help="그림 4에서 회색으로 칠할 출발 전 정지 끝 [초]")
    ap.add_argument("--until", type=float, help="이 시각[초] 뒤는 그리지 않음 (주행 뒤 센서를 만진 구간 제외)")
    ap.add_argument("--repo", default=os.environ.get("MERMAID_REPO") or os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..")))
    args = ap.parse_args()
    os.makedirs(args.outdir, exist_ok=True)

    raw = np.load(os.path.join(args.ride, "raw_caraxis", "samples_100hz.npy")).astype(float)
    cal = np.load(os.path.join(args.ride, "calibrated", "samples_100hz.npy")).astype(float)
    info = json.load(open(os.path.join(args.ride, "calibrated", "calibration.json")))
    if args.until:
        raw, cal = raw[raw[:, 0] < args.until], cal[cal[:, 0] < args.until]
        info["still_windows"] = [w for w in info["still_windows"] if w["t1"] <= args.until]
    r1, c1 = sec_mean(raw[:, 1:7]), sec_mean(cal[:, 1:7])
    tr, tc = np.arange(len(r1)), np.arange(len(c1))

    # ---- 그림 1: 주행 전체, 1초 평균
    fig, ax = plt.subplots(2, 1, figsize=(13, 6.5), sharex=True)
    for i, name in enumerate(["앞뒤 가속도 ax", "좌우 가속도 ay"]):
        ax[i].plot(tr, r1[:, i], color=BEFORE, lw=0.6, label="보정 전")
        ax[i].plot(tc, c1[:, i], color=AFTER, lw=0.6, label="보정 후")
        ax[i].axhline(0, color="k", lw=0.5)
        ax[i].set_ylabel(name + " [m/s²]")
        ax[i].set_ylim(-3, 3)
        ax[i].legend(loc="upper right", ncol=2)
    ax[1].set_xlabel("주행 시작부터 [초]")
    ax[0].set_title("주행 전체 (1초 평균): 기울기 보정으로 전체가 0 쪽으로 내려온다")
    fig.tight_layout(); fig.savefig(os.path.join(args.outdir, "1_전체_전후.png"), dpi=130); plt.close(fig)

    # ---- 그림 2: 정지 구간의 수평 값
    fig, ax = plt.subplots(1, 2, figsize=(12, 5.6))
    sw = info["still_windows"]
    b = np.array([s["before"] for s in sw]); a = np.array([s["after"] for s in sw])
    t0 = np.array([s["t0"] for s in sw])
    for k, (d, title) in enumerate([(b, "보정 전"), (a, "보정 후")]):
        sc = ax[k].scatter(d[:, 1], d[:, 0], c=t0, cmap="viridis", s=45, edgecolor="k", lw=0.4)
        ax[k].axhline(0, color="k", lw=0.5); ax[k].axvline(0, color="k", lw=0.5)
        ax[k].add_patch(plt.Circle((0, 0), 0.05, fill=False, color="g", lw=1.5))
        ax[k].set_xlim(1.2, -1.2); ax[k].set_ylim(-1.2, 1.2); ax[k].set_aspect("equal")
        ax[k].set_xlabel("좌우 ay [m/s²]  (왼쪽이 +)"); ax[k].set_ylabel("앞뒤 ax [m/s²]")
        ax[k].set_title(f"정지 구간 {len(sw)}곳의 평균 값: {title}")
    fig.colorbar(sc, ax=ax, label="정지 시작 시각 [초]", shrink=0.8)
    fig.text(0.5, 0.01, "초록 원 = 합격 범위 ±0.05 m/s².  보정 후 원 밖의 점은 그 자리의 바닥 경사(5단계에서 다룸)", ha="center")
    fig.savefig(os.path.join(args.outdir, "2_정지구간_전후.png"), dpi=130, bbox_inches="tight"); plt.close(fig)

    # ---- 그림 3: 평지 구간 확대 (부호 확인용)
    fig, ax = plt.subplots(3, 1, figsize=(13, 8), sharex=True)
    z0, z1 = args.zoom
    m = (tc >= z0) & (tc < z1)
    ax[0].plot(tc[m], c1[m, 0], color=AFTER); ax[0].set_ylabel("앞뒤 ax [m/s²]")
    ax[1].plot(tc[m], c1[m, 1], color=AFTER); ax[1].set_ylabel("좌우 ay [m/s²]")
    ax[2].plot(tc[m], np.degrees(c1[m, 5]), color="#555"); ax[2].set_ylabel("회전 속도 gz [도/초]")
    for x in ax:
        x.axhline(0, color="k", lw=0.5)
        for s in sw:
            if z0 <= s["t0"] < z1:
                x.axvspan(s["t0"], s["t1"], color="#ddd")
    ax[0].set_title(f"확대 {z0:.0f}~{z1:.0f}초 (보정 후, 1초 평균). 회색 = 정지.  좌회전이면 ay와 gz가 함께 +")
    ax[2].set_xlabel("주행 시작부터 [초]")
    fig.tight_layout(); fig.savefig(os.path.join(args.outdir, "3_구간_확대.png"), dpi=130); plt.close(fig)

    # ---- 그림 4: 멀미 누적 점수(Oman)
    sys.path.insert(0, os.path.join(args.repo, "ml"))
    import oman
    fig, ax = plt.subplots(figsize=(13, 4.5))
    res = {}
    for d, name, col in [(raw, "보정 전", BEFORE), (cal, "보정 후", AFTER)]:
        c = oman.input_signal(d[:, 1], d[:, 2], 0.01)
        score, _ = oman.run(c, oman.OMAN["K0"], 0.01)
        ax.plot(d[::100, 0], score[::100], color=col, label=name)
        res[name] = {"정지 끝": float(score[min(int(args.still_until * 100), len(score) - 1)]), "끝": float(score[-1])}
    ax.set_xlabel("주행 시작부터 [초]"); ax.set_ylabel("Oman 점수 (MISC 척도)")
    ax.set_title(f"멀미 누적 점수: 처음 {args.still_until:.0f}초는 정지 상태라 0에 가까워야 한다")
    ax.axvspan(0, args.still_until, color="#eee"); ax.legend()
    fig.tight_layout(); fig.savefig(os.path.join(args.outdir, "4_멀미점수_전후.png"), dpi=130); plt.close(fig)
    print(json.dumps(res, ensure_ascii=False, indent=1))
    print("그림 저장:", args.outdir)


if __name__ == "__main__":
    main()
