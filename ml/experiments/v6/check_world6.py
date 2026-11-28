"""세계 점검 그림 — 새 피처 분포가 그럴듯한지 (본 실험 전에 눈으로 확인).

    python3 ml/experiments/v6/check_world6.py ml/out/v6/small_base.npz [--out fig.png]
"""
import argparse
import json
import pathlib
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import world6 as W6  # noqa: E402

C = {"ink": "#1f2a37", "mute": "#8a94a6", "grid": "#e6e9ef",
     "s": ["#9aa5b5", "#6c8ebf", "#d9823b", "#c0504d", "#7b5ea7"], "ev": "#2e86ab", "ice": "#c8553d"}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("npz"); ap.add_argument("--out", default=None); a = ap.parse_args()
    d = np.load(a.npz, allow_pickle=False); X = d["X"]; names = list(d["feature_names"]); col = {n: i for i, n in enumerate(names)}
    st = d["state"]; Y, W, t, rid, pid = d["Y"], d["W"], d["t"], d["ride_id"], d["person_id"]
    meta = json.loads(pathlib.Path(a.npz[:-4] + "_rides.json").read_text()); persons = {p["pid"]: p for p in json.loads(pathlib.Path(a.npz[:-4] + "_persons.json").read_text())}
    plt.rcParams.update({"font.family": "DejaVu Sans", "axes.edgecolor": C["mute"], "axes.labelcolor": C["ink"], "xtick.color": C["ink"], "ytick.color": C["ink"],
                         "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True, "grid.color": C["grid"], "axes.axisbelow": True, "font.size": 9})
    fig, ax = plt.subplots(4, 3, figsize=(13, 15)); ax = ax.ravel()
    f = lambda n: X[:, col[n]]

    # 1 상태별 수신 속도
    for s in range(5):
        v = f("rx_rate_log_5m")[(st == s) & np.isfinite(f("rx_rate_log_5m"))]
        ax[0].hist(v, bins=40, range=(0, 3.2), histtype="step", lw=1.6, color=C["s"][s], label=f"{W6.STATES[s]} ({(st == s).mean() * 100:.0f}%)", density=True)
    ax[0].set(title="rx rate by passenger state", xlabel="log10(1 + kB/s), 5-min mean"); ax[0].legend(frameon=False)
    # 2 상태별 화면·미디어·가로
    k = np.isfinite(f("screen_on_share_5m"))
    for j, n in enumerate(["screen_on_share_5m", "media_share_5m", "landscape_share_5m", "app_fg_share_5m"]):
        ax[1].plot([np.nanmean(f(n)[(st == s) & k]) for s in range(5)], marker="o", color=C["s"][[1, 2, 4, 3][j]], label=n.replace("_share_5m", ""))
    ax[1].set(title="phone signals by state (mean share)", xticks=range(5), xticklabels=W6.STATES, ylim=(0, 1.05)); ax[1].legend(frameon=False)
    # 3 사람 유형별 화면 비율
    vt = np.array([persons[int(p)]["visual_type"] for p in pid])
    for j, n in enumerate(["nonuser", "text", "video", "gamer"]):
        v = f("screen_on_share_5m")[(vt == n) & k]; ax[2].hist(v, bins=25, range=(0, 1), histtype="step", lw=1.6, color=C["s"][j + 1], label=n, density=True)
    ax[2].set(title="screen-on share by person type", xlabel="screen_on_share_5m"); ax[2].legend(frameon=False)
    # 4 시각: 출퇴근형 / 불규칙형
    tod = (np.degrees(np.arctan2(f("tod_sin"), f("tod_cos"))) % 360) / 15
    cm = np.array([persons[int(p)]["commuter"] for p in pid])
    ax[3].hist(tod[cm], bins=48, range=(0, 24), color=C["s"][1], alpha=.8, label="commuter", density=True)
    ax[3].hist(tod[~cm], bins=48, range=(0, 24), color=C["s"][2], alpha=.6, label="irregular", density=True)
    ax[3].set(title="time of day (rows)", xlabel="hour", xticks=range(0, 25, 4)); ax[3].legend(frameon=False)
    # 5 마지막 탑승 뒤 시간
    g = np.array([m["gap_h"] if m["gap_h"] is not None else np.nan for m in meta]); cmr = np.array([m["commuter"] for m in meta])
    ax[4].hist(np.clip(g[cmr & np.isfinite(g)], 0, 120), bins=48, range=(0, 120), color=C["s"][1], alpha=.8, label="commuter")
    ax[4].hist(np.clip(g[~cmr & np.isfinite(g)], 0, 120), bins=48, range=(0, 120), color=C["s"][2], alpha=.6, label="irregular")
    ax[4].set(title="hours since last ride (per ride, clipped 120)", xlabel="h"); ax[4].legend(frameon=False)
    # 6 가속→감속 직행: EV 회생 단계 / 내연 / 펌프질
    ev = np.array([m["vehicle_type"] for m in meta]); rg = np.array([m["regen_level"] if m["regen_level"] is not None else -1 for m in meta]); pm = np.array([m["pumping"] for m in meta])
    per_ride = np.array([f("accel_decel_switch_5m")[rid == m["ride_id"]].mean() for m in meta])
    groups = [("ICE", (ev == 0) & ~pm), ("EV regen 0-1", (rg >= 0) & (rg <= 1) & ~pm), ("EV regen 2-3", (rg >= 2) & ~pm), ("pumping driver", pm)]
    ax[5].boxplot([per_ride[mk] for _, mk in groups], tick_labels=[n for n, _ in groups], showfliers=False, medianprops={"color": C["s"][2]})
    ax[5].set(title="accel→decel switches / 5 min (ride mean)"); ax[5].tick_params(axis="x", labelsize=8)
    # 7 사건 시각 분포와 장치별 비율
    for j, dname in enumerate(W6.HEADS):
        on = [m["t_on"][dname] / 60 for m in meta if dname in m["t_on"]]
        ax[6].hist(on, bins=20, range=(0, 40), histtype="step", lw=1.6, color=C["s"][j + 1], label=f"{dname} ({len(on) / len(meta) * 100:.0f}% of rides)")
    ax[6].set(title="manual switch-on time", xlabel="minute of ride"); ax[6].legend(frameon=False)
    # 8 개입 효과: 선풍기 켠 뒤 MISC 기울기 (켜기 전 5분 vs 후 5분), 사람별 효과와 비교
    misc = d["misc"]; before, after, effs = [], [], []
    for m in meta:
        if "fan" not in m["t_on"]:
            continue
        i = np.flatnonzero(rid == m["ride_id"]); tt = t[i]; mm = d["score"][i]; to = m["t_on"]["fan"]
        b = (tt >= to - 300) & (tt < to); af = (tt >= to + 120) & (tt < to + 420)
        if b.sum() > 10 and af.sum() > 10:
            before.append(np.polyfit(tt[b], mm[b], 1)[0] * 60); after.append(np.polyfit(tt[af], mm[af], 1)[0] * 60); effs.append(persons[m["person_id"]]["effect"]["fan"])
    ax[7].scatter(before, after, s=12, c=effs, cmap="viridis", alpha=.8); lim = ax[7].get_xlim(); ax[7].plot(lim, lim, color=C["mute"], lw=1)
    ax[7].set(title="fan effect: Oman slope before vs after switch-on", xlabel="slope before (MISC/min)", ylabel="slope after (2~7 min)")
    # 9 습관: 선풍기 켠 순간의 MISC vs 실내 온도 (더위 계수 색)
    xs, ys, cs = [], [], []
    for m in meta:
        if "fan" in m["t_on"]:
            i = np.flatnonzero((rid == m["ride_id"]) & (np.abs(t - m["t_on"]["fan"]) < 1e-6))
            if len(i):
                xs.append(X[i[0], col["cabin_temp"]]); ys.append(misc[i[0]]); cs.append(persons[m["person_id"]]["heat"])
    sc = ax[8].scatter(xs, ys, c=cs, cmap="plasma", s=14, alpha=.85); plt.colorbar(sc, ax=ax[8], label="person heat coef")
    ax[8].set(title="MISC when fan was switched on vs cabin temp", xlabel="cabin_temp (°C)", ylabel="MISC at press")
    # 10 조작 이력 피처
    ax[9].hist(f("ops_count_ride"), bins=[-.5, .5, 1.5, 2.5, 3.5], color=C["s"][1], rwidth=.8); ax[9].set(title="ops_count_ride (rows)", xticks=range(4))
    v = f("min_since_last_op"); ax[10].hist(v[np.isfinite(v)], bins=30, color=C["s"][2]); ax[10].set(title=f"min_since_last_op (NaN {np.isnan(v).mean() * 100:.0f}% of rows)", xlabel="min")
    # 12 반응 라벨 양성률 (자격 있는 행)
    rates = [(Y[W[:, j] > 0, j]).mean() for j in range(3)]; elig = [(W[:, j] > 0).mean() for j in range(3)]
    ax[11].bar(np.arange(3) - .18, rates, .36, color=C["s"][2], label="P(Y=1 | eligible)"); ax[11].bar(np.arange(3) + .18, elig, .36, color=C["s"][1], label="eligible share")
    ax[11].set(xticks=range(3), xticklabels=W6.HEADS, title="response labels", ylim=(0, 1)); ax[11].legend(frameon=False)
    rep = json.loads(pathlib.Path(a.npz[:-4] + "_report.json").read_text())
    fig.suptitle(f"world check — {rep['kind']} / habit={rep['habit']} / rides {rep['rides']} / rows {rep['rows']} / persons {rep['persons']}", y=0.995, fontsize=12, color=C["ink"])
    fig.tight_layout(); out = a.out or a.npz[:-4] + "_check.png"; fig.savefig(out, dpi=130); print("→", out)
    # 피처 요약표
    print(f"{'feature':26s} {'NaN%':>6s} {'mean':>8s} {'sd':>8s} {'min':>8s} {'max':>8s}")
    for n in names[50:]:
        v = f(n); fin = np.isfinite(v)
        print(f"{n:26s} {100 * (~fin).mean():6.1f} {v[fin].mean():8.3f} {v[fin].std():8.3f} {v[fin].min():8.3f} {v[fin].max():8.3f}")


if __name__ == "__main__":
    main()
