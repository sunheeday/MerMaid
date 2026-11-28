"""라벨이 바뀌어도 합성 베이스에서 출발하는 게 맞나 — 4TU '멈춤' 라벨 실험 (GBM만, 약 15분)

    python3 ml/experiments/stop_transfer_4tu.py ml/data/raw/ATDOMS.mat --dataset ml/out/runs/v002/dataset.npz

배경: 배포에서는 라벨이 MISC(합성)에서 사용자 반응(실측)으로 바뀐다. 4TU에는 행동 라벨이 하나 있다 —
      참가자가 MISC 6에 닿으면 실험을 멈춘다. "다음 5분 안에 멈춤"을 반응 라벨의 대역으로 쓴다.
      (드물고, 사람마다 문턱이 다르고, 못 견뎌서 한 행동이라는 점이 '켜기'와 닮았다.)

비교 (전부 GBM):
  Oman 규칙                 학습 없음, oman_score 피처 그대로
  처음부터                  4TU n명의 멈춤 라벨로만
  베이스(k=4) 그대로        합성 Oman≥4 라벨로 사전학습, 4TU 라벨 0개
  베이스(k=3) + 이어학습    합성 Oman≥3 베이스 위에 멈춤 라벨로 트리 30개
  베이스(k=4) + 이어학습    합성 Oman≥4 베이스 위에 멈춤 라벨로 트리 30개   ← 라벨이 가까운 베이스가 더 나은가
축: n ∈ {1, 2, 4, 전부}, 사람 5-fold, 시드 3.
출력: <out>/summary.md, results.csv, fig.png, tudelft_stop_rows.npz(캐시), gbm_base_k{3,4}.txt(캐시)
"""
import argparse
import csv
import pathlib
import sys
import tempfile
import time

import numpy as np

ML = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ML)); sys.path.insert(0, str(ML / "train")); sys.path.insert(0, str(ML / "verify"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import fo_compat as FC  # noqa: E402  (피처 목록 v3 77칸 / v5 52칸 모두)
import common as C            # noqa: E402
import oman                   # noqa: E402
import trip as tripio         # noqa: E402
import tudelft_replay as TR   # noqa: E402

FO = C.FO
ENABLED = [i for i, f in enumerate(C.FEATS) if f["enabled"]]
POS = {i: j for j, i in enumerate(ENABLED)}
DROP_POS = {g: [POS[i] for i in idx if i in POS] for g, idx in C.DROPPABLE.items()}
NEVER_POS = sorted({POS[i] for i in C.NEVER if i in POS})
OMAN_POS = POS[C.NAMES.index("oman_score")]
MONO = [C.MONOTONE[i] for i in ENABLED]
STOP_MISC = 6          # 4TU 중단 기준
HORIZON = 300.0


def load_sim(path, n_rows, rng, ks):
    d = np.load(path, allow_pickle=False)
    assert int(d["feature_version"]) == FO["version"]
    idx = rng.choice(len(d["X"]), size=min(n_rows, len(d["X"])), replace=False)
    klist = list(d["k"])
    return d["X"][idx][:, ENABLED].astype(np.float32), {k: d["y"][idx, klist.index(k)].astype(int) for k in ks}, d["ride_id"][idx]


def build_tudelft_stop(mat, cache, v0_kmh=50.0, seed=0):
    """→ X(74열), y_stop, y_misc4(참고), pid, sess. 자극 구간 안의 행만. 캐시."""
    if cache.exists():
        z = np.load(cache); return z["X"], z["y_stop"], z["y_misc4"], z["pid"], z["sess"]
    import scipy.io as sio
    A = sio.loadmat(mat, squeeze_me=True, struct_as_record=False)["ATDOMS"]
    persons = list(A._fieldnames); v0 = v0_kmh / 3.6
    Xs, ys, ym, pids, sids = [], [], [], [], []
    n_stop = 0; n_seg = 0
    with tempfile.TemporaryDirectory() as td:
        for pi, p in enumerate(persons):
            mssq = float(getattr(A, p).MSSQ.Score)
            for ci, (c, amp) in enumerate(TR.AMPS.items()):
                seg, tr, yr = TR.session(getattr(getattr(A, p), c))
                n = int((tr[-1] + 10) * TR.FS)
                acc = TR.stimulus(seg, n, amp)
                rng = np.random.default_rng([seed, pi, int(c[1])])
                path = pathlib.Path(td) / "trip.bin"
                tripio.write_trip(path, TR.make_trip(acc, v0, rng))
                t, Xv = tripio.featurize(path, pathlib.Path(td) / "features.bin")
                X = FC.full_row(Xv, mssq, float(oman.sensitivity(mssq)))
                # 멈춤 시각: 자극 구간 안에서 MISC가 처음 STOP_MISC 에 닿은 보고 시각
                stops = []
                for (s, e) in seg:
                    n_seg += 1
                    m = (tr >= s) & (tr <= e + 30) & (yr >= STOP_MISC)
                    if m.any():
                        stops.append(float(tr[m][0])); n_stop += 1
                in_seg = np.zeros(len(t), bool)
                for (s, e) in seg:
                    in_seg |= (t >= s) & (t <= e)
                y_stop = np.zeros(len(t), int)
                for ts in stops:
                    y_stop |= ((t < ts) & (t + HORIZON >= ts)).astype(int)
                # 멈춘 뒤의 행(같은 구간)은 제외: 이미 사건이 난 뒤
                after = np.zeros(len(t), bool)
                for (s, e), in_ in [((s, e), None) for (s, e) in seg]:
                    for ts in stops:
                        if s <= ts <= e + 30:
                            after |= (t > ts) & (t <= e)
                win = lambda tt: yr[(tr > tt) & (tr <= tt + HORIZON)]
                fut = np.array([np.nanmax(w) if np.isfinite(w := win(tt)).any() else np.nan for tt in t])
                v = in_seg & ~after
                Xs.append(X[v][:, ENABLED]); ys.append(y_stop[v]); ym.append((fut[v] >= 4).astype(int))
                pids.append(np.full(int(v.sum()), pi)); sids.append(np.full(int(v.sum()), pi * 10 + ci))
    X = np.concatenate(Xs).astype(np.float32); y_stop = np.concatenate(ys); y_misc4 = np.concatenate(ym)
    pid, sess = np.concatenate(pids), np.concatenate(sids)
    np.savez_compressed(cache, X=X, y_stop=y_stop, y_misc4=y_misc4, pid=pid, sess=sess,
                        n_stop=n_stop, n_seg=n_seg)
    print(f"  자극 구간 {n_seg}개 중 멈춤 {n_stop}개")
    return X, y_stop, y_misc4, pid, sess


def group_dropout74(X, ride_id, rng):
    X = X.copy(); rides, inv = np.unique(ride_id, return_inverse=True)
    for g, cols in DROP_POS.items():
        drop = rng.random(len(rides)) < FO["groups"][g]["dropout"]; X[np.ix_(drop[inv], cols)] = np.nan
    cell_cols = [i for i in range(X.shape[1]) if i not in NEVER_POS]
    mask = rng.random((len(X), len(cell_cols))) < FO["cell_dropout"]
    sub = X[:, cell_cols]; sub[mask] = np.nan; X[:, cell_cols] = sub
    return X


def gbm_pretrain(X, y, ride_id, rng, path):
    import lightgbm as lgb
    if path.exists():
        return lgb.Booster(model_file=str(path))
    Xd = group_dropout74(X, ride_id, rng)
    params = dict(C.PARAMS, num_threads=4, monotone_constraints=MONO)
    m = lgb.train(params, lgb.Dataset(np.vstack([X, Xd]), np.concatenate([y, y])), num_boost_round=300)
    m.save_model(str(path)); return m


def gbm_fit(Xtr, ytr, seed, base=None, rounds=30):
    import lightgbm as lgb
    params = dict(C.PARAMS, seed=seed, num_threads=4, monotone_constraints=MONO)
    params["min_data_in_leaf"] = int(min(100, max(5, len(Xtr) // 20)))
    if base is None:
        return lgb.train(params, lgb.Dataset(Xtr, ytr), num_boost_round=200)
    params["learning_rate"] = 0.02
    return lgb.train(params, lgb.Dataset(Xtr, ytr), num_boost_round=rounds, init_model=base, keep_training_booster=True)


def event_hit(y, s, sess, thr_q=0.85):
    """사건 기준 지표: 멈춤 사건 중 '그 전 5분 안에 점수가 문턱을 넘은' 비율. 문턱 = 양성 행 점수의 하위 15% 분위."""
    pos = s[y == 1]
    if len(pos) == 0:
        return float("nan")
    thr = np.quantile(pos, 1 - thr_q)
    hits, tot = 0, 0
    for sid in np.unique(sess):
        m = sess == sid
        if y[m].sum() == 0:
            continue
        tot += 1; hits += int((s[m][y[m] == 1] >= thr).any())
    return hits / tot if tot else float("nan")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mat"); ap.add_argument("--dataset", required=True)
    ap.add_argument("--rows", type=int, default=300_000); ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--rounds", type=int, default=30, help="이어 학습 트리 수")
    ap.add_argument("--out", default=str(FC.out_dir("stop_transfer")))
    a = ap.parse_args()
    out = pathlib.Path(a.out); out.mkdir(parents=True, exist_ok=True)
    logf = open(out / "log.txt", "w")
    def log(s): print(s); logf.write(s + "\n"); logf.flush()
    rng = np.random.default_rng(0); T0 = time.time()
    from sklearn.metrics import average_precision_score, roc_auc_score
    from sklearn.model_selection import GroupKFold

    Xsim, Ysim, ride_sim = load_sim(a.dataset, a.rows, rng, [3, 4])
    log(f"시뮬레이터 {len(Xsim)}행")
    X4, y_stop, y_m4, pid, sess = build_tudelft_stop(a.mat, out / "tudelft_stop_rows.npz")
    log(f"4TU {len(X4)}행, {len(np.unique(pid))}명, 세션 {len(np.unique(sess))}개. "
        f"멈춤 라벨 양성 비율 {y_stop.mean():.3f} (참고: MISC≥4 라벨 {y_m4.mean():.3f}). "
        f"사람별 멈춤 있음: {sum(y_stop[pid == p].sum() > 0 for p in np.unique(pid))}/{len(np.unique(pid))}")
    base = {k: gbm_pretrain(Xsim, Ysim[k], ride_sim, rng, out / f"gbm_base_k{k}.txt") for k in (3, 4)}
    log(f"베이스 준비 ({time.time() - T0:.0f}s)")

    rows = []
    def rec(fold, n, seed, cond, yte, s, sess_te):
        rows.append({"fold": fold, "n": n, "seed": seed, "cond": cond,
                     "pr_auc": float(average_precision_score(yte, s)), "roc_auc": float(roc_auc_score(yte, s)),
                     "event_hit": event_hit(yte, s, sess_te)})
    conds = ["Oman 규칙", "처음부터", "베이스(k=4) 그대로", "베이스(k=3) + 이어학습", "베이스(k=4) + 이어학습"]
    for fold, (tr_idx, te) in enumerate(GroupKFold(5).split(X4, y_stop, pid)):
        tr_p = np.unique(pid[tr_idx]); yte = y_stop[te]
        if yte.sum() == 0:
            log(f"  fold {fold}: 시험 사람에 멈춤 없음 → 건너뜀"); continue
        for n in [1, 2, 4, 99]:
            n_eff = len(tr_p) if n == 99 else n
            for seed in ([0] if n == 99 else list(range(a.seeds))):
                srng = np.random.default_rng([fold, n, seed])
                ok = False
                for _ in range(50):
                    chosen = srng.choice(tr_p, size=n_eff, replace=False); tr = np.where(np.isin(pid, chosen))[0]
                    if y_stop[tr].min() != y_stop[tr].max():
                        ok = True; break
                if not ok:
                    continue
                preds = {
                    "Oman 규칙": X4[te, OMAN_POS],
                    "처음부터": gbm_fit(X4[tr], y_stop[tr], seed).predict(X4[te]),
                    "베이스(k=4) 그대로": base[4].predict(X4[te]),
                    "베이스(k=3) + 이어학습": gbm_fit(X4[tr], y_stop[tr], seed, base=base[3], rounds=a.rounds).predict(X4[te]),
                    "베이스(k=4) + 이어학습": gbm_fit(X4[tr], y_stop[tr], seed, base=base[4], rounds=a.rounds).predict(X4[te]),
                }
                for cond, s in preds.items():
                    rec(fold, n, seed, cond, yte, np.nan_to_num(s, nan=0.0), sess[te])
        log(f"  fold {fold} 끝 ({time.time() - T0:.0f}s)")

    with open(out / "results.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    ns = [1, 2, 4, 99]; nl = lambda n: "전부" if n == 99 else str(n)
    def agg(cond, n, key):
        x = np.array([r[key] for r in rows if r["cond"] == cond and r["n"] == n]); x = x[np.isfinite(x)]
        return (x.mean(), x.std()) if len(x) else (float("nan"), float("nan"))
    L = ["# 멈춤 라벨 전이 실험 — 4TU (라벨이 MISC→행동으로 바뀔 때 합성 베이스가 도움이 되나)", "",
         f"라벨: 자극 구간에서 다음 {int(HORIZON)}초 안에 참가자가 멈춤(MISC {STOP_MISC} 도달). 양성 비율 {y_stop.mean():.3f}.",
         f"베이스: 시뮬레이터 {len(Xsim)}행 + 합성 Oman 라벨, 300트리. 이어 학습 {a.rounds}트리(lr 0.02). 사람 5-fold, 시드 {a.seeds}.", ""]
    for key, name in [("pr_auc", "PR-AUC"), ("roc_auc", "ROC-AUC"), ("event_hit", "사건 적중률 (멈춤 전 5분 안에 문턱 넘김, 문턱=양성 재현 85%)")]:
        L += [f"## {name}", "", "| 조건 | " + " | ".join(f"n={nl(n)}" for n in ns) + " |", "|---|" + "---|" * len(ns)]
        for cond in conds:
            L.append(f"| {cond} | " + " | ".join(f"{agg(cond, n, key)[0]:.3f} ± {agg(cond, n, key)[1]:.3f}" for n in ns) + " |")
        L.append("")
    L += ["## 읽는 법",
          "- 베이스(k=4) 그대로 > Oman 규칙 이면: MISC로 배운 모델이 라벨 0개로도 '멈춤'을 규칙보다 잘 예고.",
          "- 베이스 + 이어학습 > 처음부터 (특히 n=1·2) 이면: 라벨이 바뀌어도 합성 베이스에서 출발하는 게 맞다 → 반응 전환 설계의 근거.",
          "- 베이스(k=4) + 이어 > 베이스(k=3) + 이어 이면: 새 라벨에 가까운 사전학습 라벨을 고르는 게 맞다.",
          "- 처음부터 ≥ 베이스 + 이어학습 이면: 라벨이 바뀌는 단계에선 베이스를 버려야 한다.",
          f"", f"총 {time.time() - T0:.0f}초"]
    (out / "summary.md").write_text("\n".join(L), encoding="utf-8"); log("\n".join(L))

    import logging, matplotlib; matplotlib.use("Agg"); logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)
    import matplotlib.pyplot as plt
    plt.rcParams["font.family"] = ["NanumGothic", "Noto Sans CJK KR", "DejaVu Sans"]
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    for ax, key, name in zip(axes, ["pr_auc", "event_hit"], ["PR-AUC", "사건 적중률"]):
        for cond in conds:
            m_ = [agg(cond, n, key)[0] for n in ns]; e_ = [agg(cond, n, key)[1] for n in ns]
            ax.errorbar(range(len(ns)), m_, yerr=e_, marker="o", capsize=3, label=cond, lw=1.2)
        ax.set_xticks(range(len(ns))); ax.set_xticklabels([nl(n) for n in ns]); ax.set_xlabel("4TU 학습 사람 수")
        ax.set_ylabel(name); ax.grid(alpha=.3)
    axes[0].legend(fontsize=7); fig.tight_layout(); fig.savefig(out / "fig.png", dpi=150)
    log(f"저장: {out}")


if __name__ == "__main__":
    main()
