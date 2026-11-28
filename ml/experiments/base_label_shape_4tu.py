"""첫 베이스의 라벨 모양 — 원래형(A) vs 사건형(B), 같은 높이(4), 4TU 멈춤 라벨로 채점 (약 10분)

    python3 ml/experiments/base_label_shape_4tu.py ml/data/raw/ATDOMS.mat --dataset ml/out/runs/v002/dataset.npz

A 원래형 : 다음 5분 안 합성 MISC 최대 ≥ 4. 모든 행에 라벨 (repo 현행, dataset.npz 의 y).
B 사건형 : 합성 MISC 가 처음 4에 닿기 전 5분만 1, 닿은 뒤 행은 학습에서 제외, 안 닿은 탑승은 전부 0.
둘 다 높이는 4 → 시험 라벨(멈춤 = 6)과 거리가 같다. 다른 것은 라벨의 모양뿐.
(6 사건형은 시험 라벨이 6이라 홈 어드밴티지가 있어 비교에서 뺀다.)

채점: 4TU 멈춤 라벨. 라벨 0개(그대로) + 사람 n명 이어학습(n=1,2,4,전부). 사람 5-fold, 시드 3.
결정 규칙: B > A 면 사건형 채택(높이는 헤드별로). B ≈ A 면 A 유지.
출력: ml/out/base_label_shape/summary.md, results.csv, fig.png
"""
import argparse
import csv
import pathlib
import sys
import time

import numpy as np

ML = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ML)); sys.path.insert(0, str(ML / "train")); sys.path.insert(0, str(ML / "verify")); sys.path.insert(0, str(ML / "experiments"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import fo_compat as FC  # noqa: E402  (피처 목록 v3 77칸 / v5 52칸 모두)
import common as C  # noqa: E402
from stop_transfer_4tu import build_tudelft_stop, group_dropout74, gbm_fit, OMAN_POS, ENABLED, MONO  # noqa: E402

FO = C.FO
HORIZON = 300.0
H = 4   # 두 베이스의 공통 높이


def load_full(path):
    d = np.load(path, allow_pickle=False)
    assert int(d["feature_version"]) == FO["version"]
    klist = list(d["k"])
    return (d["X"][:, ENABLED].astype(np.float32), d["y"][:, klist.index(H)].astype(int),
            d["misc"].astype(float), d["t"].astype(float), d["ride_id"])


def event_labels(misc, t, ride_id, h=H, horizon=HORIZON):
    """사건형: 탑승마다 misc 가 처음 h 에 닿는 시각 t*. t < t* ≤ t+horizon → 1, t ≥ t* → 제외(w=0), 안 닿으면 전부 0."""
    y = np.zeros(len(t), int); w = np.ones(len(t), np.float32)
    order = np.argsort(ride_id, kind="stable")
    rid_sorted = ride_id[order]
    bounds = np.flatnonzero(np.r_[True, rid_sorted[1:] != rid_sorted[:-1], True])
    n_event = 0
    for a, b in zip(bounds[:-1], bounds[1:]):
        idx = order[a:b]; tt = t[idx]; mm = misc[idx]
        o = np.argsort(tt); idx, tt, mm = idx[o], tt[o], mm[o]
        hit = np.flatnonzero(mm >= h)
        if len(hit) == 0:
            continue
        ts = tt[hit[0]]; n_event += 1
        y[idx[(tt < ts) & (tt + horizon >= ts)]] = 1
        w[idx[tt >= ts]] = 0.0
    return y, w, n_event


def build_tudelft_full(mat, cache, v0_kmh=50.0, seed=0):
    """4TU → 74열 + A(다음 5분 최대 MISC≥4) + B(첫 4 도달 전 5분, 도달 뒤 제외) + 멈춤 라벨. 자극 구간 행만."""
    if cache.exists():
        z = np.load(cache); return z["X"], z["yA"], z["yB"], z["wB"], z["y_stop"], z["pid"], z["sess"]
    import tempfile, scipy.io as sio
    import oman, trip as tripio, tudelft_replay as TR
    A = sio.loadmat(mat, squeeze_me=True, struct_as_record=False)["ATDOMS"]
    persons = list(A._fieldnames); v0 = v0_kmh / 3.6
    out = {k: [] for k in ("X", "yA", "yB", "wB", "y_stop", "pid", "sess")}
    with tempfile.TemporaryDirectory() as td:
        for pi, p in enumerate(persons):
            mssq = float(getattr(A, p).MSSQ.Score)
            for ci, (c, amp) in enumerate(TR.AMPS.items()):
                seg, tr, yr = TR.session(getattr(getattr(A, p), c))
                n = int((tr[-1] + 10) * TR.FS); acc = TR.stimulus(seg, n, amp)
                rng = np.random.default_rng([seed, pi, int(c[1])])
                path = pathlib.Path(td) / "trip.bin"; tripio.write_trip(path, TR.make_trip(acc, v0, rng))
                t, Xv = tripio.featurize(path, pathlib.Path(td) / "features.bin")
                X = FC.full_row(Xv, mssq, float(oman.sensitivity(mssq)))
                in_seg = np.zeros(len(t), bool)
                for (s0, e0) in seg:
                    in_seg |= (t >= s0) & (t <= e0)
                # A: 다음 5분 최대 MISC ≥ 4 (실제 보고)
                fut = np.array([np.nanmax(w_) if np.isfinite(w_ := yr[(tr > tt) & (tr <= tt + HORIZON)]).any() else np.nan for tt in t])
                yA = (fut >= H).astype(int)
                # B: 구간별 첫 H 도달 보고 시각 → 그 전 5분 = 1, 그 뒤 = 제외
                yB = np.zeros(len(t), int); wB = np.ones(len(t), np.float32)
                y_stop = np.zeros(len(t), int); after_stop = np.zeros(len(t), bool)
                for (s0, e0) in seg:
                    inwin = (tr >= s0) & (tr <= e0 + 30)
                    hB = np.flatnonzero(inwin & (yr >= H))
                    if len(hB):
                        tb = float(tr[hB[0]]); m_ = (t >= s0) & (t <= e0)
                        yB[m_ & (t < tb) & (t + HORIZON >= tb)] = 1; wB[m_ & (t >= tb)] = 0.0
                    hS = np.flatnonzero(inwin & (yr >= 6))
                    if len(hS):
                        ts = float(tr[hS[0]]); m_ = (t >= s0) & (t <= e0)
                        y_stop[m_ & (t < ts) & (t + HORIZON >= ts)] = 1; after_stop |= m_ & (t > ts)
                v = in_seg & ~after_stop & np.isfinite(fut)
                out["X"].append(X[v][:, ENABLED]); out["yA"].append(yA[v]); out["yB"].append(yB[v]); out["wB"].append(wB[v])
                out["y_stop"].append(y_stop[v]); out["pid"].append(np.full(int(v.sum()), pi)); out["sess"].append(np.full(int(v.sum()), pi * 10 + ci))
    arr = {k: np.concatenate(v) for k, v in out.items()}; arr["X"] = arr["X"].astype(np.float32)
    np.savez_compressed(cache, **arr)
    return arr["X"], arr["yA"], arr["yB"], arr["wB"], arr["y_stop"], arr["pid"], arr["sess"]


def train_base(X, y, w, ride_id, rng, path):
    import lightgbm as lgb
    if path.exists():
        return lgb.Booster(model_file=str(path))
    keep = w > 0
    Xk, yk, rk = X[keep], y[keep], ride_id[keep]
    Xd = group_dropout74(Xk, rk, rng)
    params = dict(C.PARAMS, num_threads=4, monotone_constraints=MONO)
    params["min_data_in_leaf"] = int(min(C.PARAMS.get("min_data_in_leaf", 1000), max(20, len(Xk) // 50)))
    m = lgb.train(params, lgb.Dataset(np.vstack([Xk, Xd]), np.concatenate([yk, yk])), num_boost_round=300)
    m.save_model(str(path)); return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mat"); ap.add_argument("--dataset", help="--source synth 일 때 필요")
    ap.add_argument("--source", choices=["synth", "4tu"], default="synth",
                    help="synth: 합성으로 A·B 베이스를 굽고 4TU 멈춤으로 채점 / 4tu: 4TU 학습 사람의 실제 보고로 A·B를 굽고 시험 사람 멈춤으로 채점")
    ap.add_argument("--rows", type=int, default=300_000); ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--rounds", type=int, default=30)
    ap.add_argument("--out", default=str(FC.out_dir("base_label_shape")))
    a = ap.parse_args()
    out = pathlib.Path(a.out); out.mkdir(parents=True, exist_ok=True)
    logf = open(out / "log.txt", "w")
    def log(s): print(s); logf.write(s + "\n"); logf.flush()
    rng = np.random.default_rng(0); T0 = time.time()
    from sklearn.metrics import average_precision_score, roc_auc_score
    from sklearn.model_selection import GroupKFold

    out = out / a.source; out.mkdir(parents=True, exist_ok=True)
    if a.source == "synth":
        # ---- 합성: 두 라벨로 베이스 (폴드와 무관하게 하나씩)
        Xs, yA, misc, t, rid = load_full(a.dataset)
        yB, wB, n_ev = event_labels(misc, t, rid)
        idx = rng.choice(len(Xs), size=min(a.rows, len(Xs)), replace=False)
        Xs, yA, yB, wB, rid = Xs[idx], yA[idx], yB[idx], wB[idx], rid[idx]
        log(f"합성 {len(Xs)}행 (전체 탑승 {len(np.unique(rid))}개 중 {n_ev}개가 MISC {H} 도달)")
        log(f"  A 원래형 양성 {yA.mean():.3f} | B 사건형 양성 {yB[wB > 0].mean():.3f}, 학습에 쓰는 행 {int((wB > 0).sum())}/{len(wB)}")
        base_fixed = {"A": train_base(Xs, yA, np.ones(len(yA), np.float32), rid, rng, out / "base_A_max4.txt"),
                      "B": train_base(Xs, yB, wB, rid, rng, out / "base_B_event4.txt")}
        log(f"베이스 2개 준비 ({time.time() - T0:.0f}s)")
        cache = FC.out_dir("stop_transfer", "tudelft_stop_rows.npz")
        X4, y_stop, _, pid, sess = build_tudelft_stop(a.mat, cache if cache.exists() else out / "tudelft_stop_rows.npz")
        yA4 = yB4 = wB4 = None
    else:
        # ---- 4TU만: 학습 사람의 실제 보고로 A·B를 폴드마다 굽는다
        X4, yA4, yB4, wB4, y_stop, pid, sess = build_tudelft_full(a.mat, FC.out_dir("base_label_shape", "tudelft_full_rows.npz"))
        base_fixed = None
        log(f"4TU 실제 보고 라벨 — A 양성 {yA4.mean():.3f} | B 양성 {yB4[wB4 > 0].mean():.3f}, B 학습 행 {int((wB4 > 0).sum())}/{len(wB4)}")
    log(f"4TU {len(X4)}행, 멈춤 양성 {y_stop.mean():.3f}")

    rows = []
    def rec(fold, n, seed, cond, yte, s):
        rows.append({"fold": fold, "n": n, "seed": seed, "cond": cond,
                     "pr_auc": float(average_precision_score(yte, s)), "roc_auc": float(roc_auc_score(yte, s))})
    NAME = {"A": "A 원래형(최대≥4)", "B": "B 사건형(첫 4 도달 전 5분)"}
    for fold, (tr_idx, te) in enumerate(GroupKFold(5).split(X4, y_stop, pid)):
        tr_p = np.unique(pid[tr_idx]); yte = y_stop[te]
        if yte.sum() == 0:
            continue
        if base_fixed is not None:
            base = base_fixed
        else:
            trm = np.isin(pid, tr_p); frng = np.random.default_rng(fold)
            base = {"A": train_base(X4[trm], yA4[trm], np.ones(int(trm.sum()), np.float32), sess[trm], frng, out / f"base_A_fold{fold}.txt"),
                    "B": train_base(X4[trm], yB4[trm], wB4[trm], sess[trm], frng, out / f"base_B_fold{fold}.txt")}
        for n in [0, 1, 2, 4, 99]:
            if n == 0:
                rec(fold, 0, 0, "Oman 규칙", yte, np.nan_to_num(X4[te, OMAN_POS], nan=0.0))
                for k in ("A", "B"):
                    rec(fold, 0, 0, NAME[k] + " 그대로", yte, base[k].predict(X4[te]))
                continue
            n_eff = len(tr_p) if n == 99 else n
            for seed in ([0] if n == 99 else list(range(a.seeds))):
                srng = np.random.default_rng([fold, n, seed])
                for _ in range(50):
                    chosen = srng.choice(tr_p, size=n_eff, replace=False); tr = np.where(np.isin(pid, chosen))[0]
                    if 0 < y_stop[tr].sum() < len(tr):
                        break
                for k in ("A", "B"):
                    s = gbm_fit(X4[tr], y_stop[tr], seed, base=base[k], rounds=a.rounds).predict(X4[te])
                    rec(fold, n, seed, NAME[k] + " + 이어학습", yte, s)
        log(f"  fold {fold} 끝 ({time.time() - T0:.0f}s)")

    with open(out / "results.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    ns = [1, 2, 4, 99]; nl = lambda n: "전부" if n == 99 else str(n)
    def agg(cond, n, key):
        x = np.array([r[key] for r in rows if r["cond"] == cond and r["n"] == n]); return (x.mean(), x.std()) if len(x) else (np.nan, np.nan)
    def wins(c1, c2, n, key="roc_auc"):
        d = {}
        for r in rows:
            if r["n"] == n and r["cond"] in (c1, c2):
                d.setdefault((r["fold"], r["seed"]), {})[r["cond"]] = r[key]
        pairs = [v for v in d.values() if len(v) == 2]
        return sum(v[c1] > v[c2] for v in pairs), len(pairs)
    src = ("합성 %d행으로 베이스" % len(Xs)) if a.source == "synth" else "4TU 학습 사람의 실제 보고로 베이스 (폴드마다)"
    L = [f"# 첫 베이스의 라벨 모양 — 원래형 vs 사건형 (높이 {H} 공통, 4TU 멈춤으로 채점) · 출처: {a.source}", "",
         f"{src}. 이어학습 {a.rounds}트리. 사람 5-fold, 시드 {a.seeds}.", ""]
    for key, name in [("roc_auc", "ROC-AUC"), ("pr_auc", "PR-AUC")]:
        L += [f"## {name}", "", "| 베이스 | 라벨 0 | " + " | ".join(f"n={nl(n)}" for n in ns) + " |", "|---|---|" + "---|" * len(ns)]
        L.append(f"| Oman 규칙 | {agg('Oman 규칙', 0, key)[0]:.3f} | " + " | ".join("" for _ in ns) + " |")
        for k in ("A", "B"):
            z = agg(NAME[k] + " 그대로", 0, key)
            cells = [f"{agg(NAME[k] + ' + 이어학습', n, key)[0]:.3f} ± {agg(NAME[k] + ' + 이어학습', n, key)[1]:.3f}" for n in ns]
            L.append(f"| {NAME[k]} | {z[0]:.3f} ± {z[1]:.3f} | " + " | ".join(cells) + " |")
        L.append("")
    L += ["## 쌍 비교 — 같은 폴드·시드에서 B가 A를 이긴 횟수 (ROC)", ""]
    L.append(f"- 라벨 0: {wins(NAME['B'] + ' 그대로', NAME['A'] + ' 그대로', 0)[0]} / {wins(NAME['B'] + ' 그대로', NAME['A'] + ' 그대로', 0)[1]}")
    L += [f"- n={nl(n)}: {wins(NAME['B'] + ' + 이어학습', NAME['A'] + ' + 이어학습', n)[0]} / {wins(NAME['B'] + ' + 이어학습', NAME['A'] + ' + 이어학습', n)[1]}" for n in ns]
    L += ["", "## 결정 규칙", "- B > A (라벨 0 과 n=1·2 에서, 쌍 비교 절반 넘음) → 사건형 채택. 높이는 헤드별(송풍 3~4, 앰비언트·점 5~6).",
          "- B ≈ A → 원래형 유지 (이미 검증됨).",
          "- B < A → 원래형 유지. 사건형은 양성이 적어 덜 배웠을 수 있으니 --rows 를 늘려 한 번 더 확인.",
          f"", f"총 {time.time() - T0:.0f}초"]
    (out / "summary.md").write_text("\n".join(L), encoding="utf-8"); log("\n".join(L))

    import logging, matplotlib; matplotlib.use("Agg"); logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)
    import matplotlib.pyplot as plt
    plt.rcParams["font.family"] = ["NanumGothic", "Noto Sans CJK KR", "DejaVu Sans"]
    fig, ax = plt.subplots(figsize=(6.5, 4.2)); xs = [0] + ns; xl = ["라벨 0"] + [nl(n) for n in ns]
    ax.axhline(agg("Oman 규칙", 0, "roc_auc")[0], color="#9B9A97", ls=":", label="Oman 규칙")
    for k, c in (("A", "#9B9A97"), ("B", "#37352F")):
        m_ = [agg(NAME[k] + " 그대로", 0, "roc_auc")[0]] + [agg(NAME[k] + " + 이어학습", n, "roc_auc")[0] for n in ns]
        e_ = [agg(NAME[k] + " 그대로", 0, "roc_auc")[1]] + [agg(NAME[k] + " + 이어학습", n, "roc_auc")[1] for n in ns]
        ax.errorbar(range(len(xs)), m_, yerr=e_, marker="o", capsize=3, color=c, label=NAME[k])
    ax.set_xticks(range(len(xs))); ax.set_xticklabels(xl); ax.set_xlabel("4TU 멈춤 라벨을 쓴 사람 수"); ax.set_ylabel("ROC-AUC")
    ax.grid(alpha=.3); ax.legend(fontsize=8); fig.tight_layout(); fig.savefig(out / "fig.png", dpi=150)
    log(f"저장: {out}")


if __name__ == "__main__":
    main()
