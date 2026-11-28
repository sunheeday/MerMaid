"""GBM 퓨샷 갱신이 얼마나 흔들리나 — 4TU, 게이트 후보 A~E (약 5분, CPU)

    python3 ml/experiments/gate_wobble_4tu.py ml/data/raw/ATDOMS.mat                      # 배포 모델의 데이터셋으로 베이스
    python3 ml/experiments/gate_wobble_4tu.py ml/data/raw/ATDOMS.mat --dataset ml/out/runs/v002/dataset.npz

질문: 차 한 대가 자기 로그로 모델을 갱신할 때, 갱신 방법에 따라 / 갱신할 때마다 정확도가 얼마나 흔들리나.
      게이트(검증 점수가 가장 높은 후보만 배포)가 그 흔들림을 얼마나 줄이나.

차 한 대 흉내: 4TU 17명을 섞어서 시험 5명 · 게이트 검증 4명 · 로그 8명으로 나눈다.
               로그 8명은 4번에 나눠 도착한다 (1명, 1명, 2명, 4명 → 누적 1, 2, 4, 8명).
라벨: 다음 300초 최대 MISC ≥ k (기본 k=4). 4TU에는 반응(fan/ambient/dots) 라벨이 없어서 MISC로 잰다.

갱신 때마다 만드는 후보 (전부 LightGBM):
  A 현재 모델       지금 배포된 모델 그대로
  B 이어 학습       현재 모델 위에 이번에 도착한 로그로 트리 추가
  C 리프 교체       현재 모델의 트리 모양은 두고 잎 값만 이번 로그로 다시 맞춤 (refit)
  D 베이스 재학습   합성 베이스에서 다시 출발, 지금까지 쌓인 로그 전부로 트리 추가
  E 이전 학습       바로 전 단계에 배포돼 있던 모델 (1번째 갱신에서는 A와 같다)
게이트: 검증 4명에서 ROC가 가장 높은 후보를 배포. 같으면 A.

같이 재는 길 세 개 (갱신 1~4회 뒤 시험 ROC):
  항상 B   게이트 없이 매번 이어 학습만
  항상 D   게이트 없이 매번 베이스 재학습만
  게이트   A~E 중 검증 최고

나누기를 --reps 번(기본 50) 바꿔 반복한다. 출력: <out>/summary.md, results.csv, paths.csv, fig.png, log.txt
캐시: <out>/tudelft_rows.npz (4TU → 피처, 첫 실행만 2분), <out>/gbm_base_k{k}.txt (합성 베이스)
"""
import argparse
import csv
import pathlib
import sys
import tempfile
import time
import warnings

import numpy as np

ML = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ML)); sys.path.insert(0, str(ML / "train")); sys.path.insert(0, str(ML / "verify"))
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
HORIZON = 300.0
CANDS = ["A 현재 모델", "B 이어 학습", "C 리프 교체", "D 베이스 재학습", "E 이전 학습"]
LEARN = ["B 이어 학습", "C 리프 교체", "D 베이스 재학습"]
PATHS = ["항상 B", "항상 D", "게이트"]
ARRIVE = [1, 1, 2, 4]          # 갱신마다 새로 도착하는 사람 수


# ----------------------------------------------------------------------------- 데이터
def load_sim(path, n_rows, rng, k):
    d = np.load(path, allow_pickle=False)
    assert int(d["feature_version"]) == FO["version"], "dataset이 다른 feature_order version으로 만들어짐"
    idx = rng.choice(len(d["X"]), size=min(n_rows, len(d["X"])), replace=False)
    return d["X"][idx][:, ENABLED].astype(np.float32), d["y"][idx, list(d["k"]).index(k)].astype(int), d["ride_id"][idx]


def build_tudelft(mat, cache, v0_kmh=50.0, seed=0):
    """4TU 세션을 가상 주행으로 바꿔 featurize → X(enabled 열), fut(다음 300초 최대 MISC), pid, sess. 캐시."""
    if cache.exists():
        z = np.load(cache)
        if z["X"].shape[1] == len(ENABLED) and int(z["feature_version"]) == FO["version"]:
            return z["X"], z["fut"], z["pid"], z["sess"]
    import scipy.io as sio
    A = sio.loadmat(mat, squeeze_me=True, struct_as_record=False)["ATDOMS"]
    persons = list(A._fieldnames); v0 = v0_kmh / 3.6
    Xs, futs, pids, sids = [], [], [], []
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
                X = tripio.full_vector(Xv, mssq, float(oman.sensitivity(mssq)), np.nan, 0.0)   # tudelft_replay.py와 같은 조립
                win = lambda tt: yr[(tr > tt) & (tr <= tt + HORIZON)]
                fut = np.array([np.nanmax(w) if tt + HORIZON <= tr[-1] and np.isfinite(w := win(tt)).any() else np.nan for tt in t])
                v = np.isfinite(fut)
                Xs.append(X[v][:, ENABLED]); futs.append(fut[v])
                pids.append(np.full(int(v.sum()), pi)); sids.append(np.full(int(v.sum()), pi * 10 + ci))
    X, fut = np.concatenate(Xs).astype(np.float32), np.concatenate(futs)
    pid, sess = np.concatenate(pids), np.concatenate(sids)
    np.savez_compressed(cache, X=X, fut=fut, pid=pid, sess=sess, feature_version=FO["version"])
    return X, fut, pid, sess


def group_dropout(X, ride_id, rng):
    X = X.copy(); rides, inv = np.unique(ride_id, return_inverse=True)
    for g, cols in DROP_POS.items():
        drop = rng.random(len(rides)) < FO["groups"][g]["dropout"]; X[np.ix_(drop[inv], cols)] = np.nan
    cell_cols = [i for i in range(X.shape[1]) if i not in NEVER_POS]
    mask = rng.random((len(X), len(cell_cols))) < FO["cell_dropout"]
    sub = X[:, cell_cols]; sub[mask] = np.nan; X[:, cell_cols] = sub
    return X


# ----------------------------------------------------------------------------- 모델
def params_for(n_rows, seed, threads):
    p = dict(C.PARAMS, seed=seed, num_threads=threads, monotone_constraints=MONO)
    p["min_data_in_leaf"] = int(min(100, max(5, n_rows // 20)))        # 사람 1명 ≈ 140행에 맞춤 (기존 4TU 실험과 같음)
    return p


def gbm_base(X, y, ride_id, rng, path, threads):
    import lightgbm as lgb
    if path.exists():
        return path.read_text()
    Xd = group_dropout(X, ride_id, rng)
    p = dict(C.PARAMS, num_threads=threads, monotone_constraints=MONO)
    m = lgb.train(p, lgb.Dataset(np.vstack([X, Xd]), np.concatenate([y, y])), num_boost_round=300)
    m.save_model(str(path)); return path.read_text()


def boost(model_str, X, y, seed, rounds, lr, threads):
    """model_str 위에 (X, y)로 트리 rounds개 추가 → 새 모델 문자열. 라벨이 한 종류뿐이면 배울 게 없어 그대로."""
    import lightgbm as lgb
    if y.min() == y.max():
        return model_str
    p = params_for(len(X), seed, threads); p["learning_rate"] = lr
    m = lgb.train(p, lgb.Dataset(X, y), num_boost_round=rounds, init_model=lgb.Booster(model_str=model_str))
    return m.model_to_string()


def refit(model_str, X, y, decay):
    """트리 모양은 그대로, 잎 값만: 새 값 = decay × 옛 값 + (1 − decay) × 이번 로그로 맞춘 값"""
    import lightgbm as lgb
    if y.min() == y.max():
        return model_str
    return lgb.Booster(model_str=model_str).refit(X, y, decay_rate=decay).model_to_string()


def predict(model_str, X):
    import lightgbm as lgb
    return np.nan_to_num(lgb.Booster(model_str=model_str).predict(X), nan=0.0)


# ----------------------------------------------------------------------------- 실험
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mat"); ap.add_argument("--dataset", help="합성 dataset.npz (기본: 배포 모델이 학습한 것)")
    ap.add_argument("--k", type=int, default=4, help="라벨: 다음 300초 최대 MISC ≥ k")
    ap.add_argument("--reps", type=int, default=50, help="사람 나누기를 바꿔 반복하는 횟수")
    ap.add_argument("--test-persons", type=int, default=5); ap.add_argument("--val-persons", type=int, default=4)
    ap.add_argument("--rows", type=int, default=300_000, help="베이스 학습에 쓸 합성 행 수")
    ap.add_argument("--rounds", type=int, default=30, help="이어 학습 한 번에 붙이는 트리 수")
    ap.add_argument("--lr", type=float, default=0.02); ap.add_argument("--decay", type=float, default=0.9, help="리프 교체에서 옛 값의 비중")
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--out", default=str(ML / "out" / "gate_wobble"))
    a = ap.parse_args()
    out = pathlib.Path(a.out); out.mkdir(parents=True, exist_ok=True)
    logf = open(out / "log.txt", "w")
    def log(s): print(s, flush=True); logf.write(s + "\n"); logf.flush()
    warnings.filterwarnings("ignore")
    from sklearn.metrics import average_precision_score, roc_auc_score
    T0 = time.time(); rng = np.random.default_rng(0)

    if a.dataset is None:
        import runs
        a.dataset = str(runs.dataset_for(runs.current_models()))
    X4, fut, pid, sess = build_tudelft(a.mat, out / "tudelft_rows.npz")
    y4 = (fut >= a.k).astype(int)
    persons = np.unique(pid)
    log(f"4TU {len(X4)}행 · {len(persons)}명 · 세션 {len(np.unique(sess))}개 · 피처 {X4.shape[1]}개 · MISC≥{a.k} 양성 비율 {y4.mean():.3f}")
    log("사람별 행 수 / 양성 비율: " + ", ".join(f"{(pid == p).sum()}/{y4[pid == p].mean():.2f}" for p in persons))
    base_path = out / f"gbm_base_k{a.k}.txt"
    if base_path.exists():
        base = base_path.read_text(); log(f"합성 베이스 캐시 사용: {base_path}")
    else:
        Xs, ys, rides = load_sim(a.dataset, a.rows, rng, a.k)
        log(f"합성 {len(Xs)}행 ({a.dataset}) 으로 베이스 학습 …")
        base = gbm_base(Xs, ys, rides, rng, base_path, a.threads)
    log(f"준비 끝 ({time.time() - T0:.0f}s). 반복 {a.reps}번 × 갱신 {len(ARRIVE)}회")
    assert a.test_persons + a.val_persons + sum(ARRIVE) <= len(persons), "사람 수가 모자람"

    rows, paths = [], []
    for rep in range(a.reps):
        r = np.random.default_rng([1, rep])
        for _ in range(200):                                    # 시험·검증에 두 라벨이 다 있도록
            perm = r.permutation(persons)
            te_p, va_p, lg_p = perm[:a.test_persons], perm[a.test_persons:a.test_persons + a.val_persons], perm[a.test_persons + a.val_persons:]
            te, va = np.isin(pid, te_p), np.isin(pid, va_p)
            if 0 < y4[te].mean() < 1 and 0 < y4[va].mean() < 1:
                break
        Xte, yte, Xva, yva = X4[te], y4[te], X4[va], y4[va]
        sc = lambda m: (float(roc_auc_score(yte, predict(m, Xte))), float(roc_auc_score(yva, predict(m, Xva))), float(average_precision_score(yte, predict(m, Xte))))
        base_sc = sc(base)
        oman_roc = float(roc_auc_score(yte, np.nan_to_num(Xte[:, OMAN_POS], nan=0.0)))
        cur = {p_: base for p_ in PATHS}; prev_gate = base; n_seen = 0
        for pth in PATHS:
            paths.append({"rep": rep, "update": 0, "n": 0, "path": pth, "roc_test": base_sc[0], "pr_test": base_sc[2], "picked": ""})
        paths.append({"rep": rep, "update": 0, "n": 0, "path": "Oman 규칙", "roc_test": oman_roc, "pr_test": float("nan"), "picked": ""})
        for u, n_new in enumerate(ARRIVE, start=1):
            new_p, all_p = lg_p[n_seen:n_seen + n_new], lg_p[:n_seen + n_new]; n_seen += n_new
            new, acc = np.isin(pid, new_p), np.isin(pid, all_p)
            Xn, yn, Xa, ya = X4[new], y4[new], X4[acc], y4[acc]
            # 게이트 없는 두 길
            cur["항상 B"] = boost(cur["항상 B"], Xn, yn, rep, a.rounds, a.lr, a.threads)
            cur["항상 D"] = boost(base, Xa, ya, rep, a.rounds * u, a.lr, a.threads)
            # 게이트 길: 현재 배포 모델에서 후보 다섯
            A = cur["게이트"]
            cand = {"A 현재 모델": A,
                    "B 이어 학습": boost(A, Xn, yn, rep, a.rounds, a.lr, a.threads),
                    "C 리프 교체": refit(A, Xn, yn, a.decay),
                    "D 베이스 재학습": cur["항상 D"],
                    "E 이전 학습": prev_gate}
            s = {c: sc(m) for c, m in cand.items()}
            pick = max(CANDS, key=lambda c: (round(s[c][1], 6), -CANDS.index(c)))      # 검증 ROC 최고, 같으면 앞쪽(A)
            for c in CANDS:
                rows.append({"rep": rep, "update": u, "n": int(n_seen), "cand": c, "roc_test": s[c][0], "roc_val": s[c][1],
                             "pr_test": s[c][2], "picked": int(c == pick), "new_rows": int(new.sum()), "new_pos": float(yn.mean())})
            prev_gate, cur["게이트"] = A, cand[pick]
            for pth in PATHS:
                st = s[pick] if pth == "게이트" else sc(cur[pth])
                paths.append({"rep": rep, "update": u, "n": int(n_seen), "path": pth, "roc_test": st[0], "pr_test": st[2],
                              "picked": pick[0] if pth == "게이트" else ""})
        if (rep + 1) % 5 == 0:
            log(f"  반복 {rep + 1}/{a.reps} ({time.time() - T0:.0f}s)")

    for name, data in [("results.csv", rows), ("paths.csv", paths)]:
        with open(out / name, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(data[0])); w.writeheader(); w.writerows(data)

    # ------------------------------------------------------------------------- 요약
    U = list(range(1, len(ARRIVE) + 1)); cumn = list(np.cumsum(ARRIVE))
    R = np.array([[[next(x["roc_test"] for x in rows if x["rep"] == rep and x["update"] == u and x["cand"] == c) for c in CANDS] for u in U] for rep in range(a.reps)])   # rep × 갱신 × 후보
    PK = np.array([[[next(x["picked"] for x in rows if x["rep"] == rep and x["update"] == u and x["cand"] == c) for c in CANDS] for u in U] for rep in range(a.reps)])
    P = {p_: np.array([[next(x["roc_test"] for x in paths if x["rep"] == rep and x["update"] == u and x["path"] == p_) for u in [0] + U] for rep in range(a.reps)]) for p_ in PATHS}
    om = np.array([x["roc_test"] for x in paths if x["path"] == "Oman 규칙"])
    li = [CANDS.index(c) for c in LEARN]
    ms = lambda x: f"{np.mean(x):.3f} ± {np.std(x):.3f}"
    hdr = "| " + " | ".join(f"{u}회 (누적 {n}명)" for u, n in zip(U, cumn)) + " |"
    L = [f"# GBM 퓨샷 갱신의 흔들림 — 4TU, MISC ≥ {a.k}, 시험 ROC", "",
         f"4TU {len(persons)}명을 시험 {a.test_persons} · 검증 {a.val_persons} · 로그 {sum(ARRIVE)}명으로 나누기를 {a.reps}번 반복. "
         f"갱신마다 새로 도착 {ARRIVE}명. 이어 학습 {a.rounds}트리 (lr {a.lr}), 리프 교체 decay {a.decay}.",
         f"기준선: 합성 베이스 그대로 {ms(P['게이트'][:, 0])} · Oman 규칙 {ms(om)}. 값은 평균 ± 표준편차(나누기 {a.reps}번).", "",
         "## 1. 후보별 시험 ROC (게이트 길에서 매 갱신 때 만든 후보)", "", "| 후보 " + hdr, "|---|" + "---|" * len(U)]
    for ci, c in enumerate(CANDS):
        L.append(f"| {c} | " + " | ".join(ms(R[:, ui, ci]) for ui in range(len(U))) + " |")
    spread = R[:, :, li].max(2) - R[:, :, li].min(2)                      # 학습 방법 B·C·D 사이 최고 − 최저
    rel = spread / R[:, :, li].max(2) * 100
    L += ["", "## 2. 학습 방법(B · C · D) 사이 편차 = 같은 갱신에서 최고 − 최저", "", "| " + hdr.strip("| ") + " | 전체 |", "|---|" + "---|" * (len(U) + 1)]
    for nm, fn in [("평균 (ROC)", np.mean), ("중앙값 (ROC)", np.median), ("최대 (ROC)", np.max)]:
        L.append(f"| {nm} | " + " | ".join(f"{fn(spread[:, ui]):.3f}" for ui in range(len(U))) + f" | {fn(spread):.3f} |")
    L.append("| 평균 (최고 대비 %) | " + " | ".join(f"{rel[:, ui].mean():.1f}%" for ui in range(len(U))) + f" | {rel.mean():.1f}% |")
    L.append("| 최대 (최고 대비 %) | " + " | ".join(f"{rel[:, ui].max():.1f}%" for ui in range(len(U))) + f" | {rel.max():.1f}% |")
    best = np.array([[LEARN[int(np.argmax(R[rep, ui, li]))][0] for ui in range(len(U))] for rep in range(a.reps)])
    L += ["", "가장 좋았던 방법의 횟수: " + " · ".join(f"{c[0]} {int((best == c[0]).sum())}/{best.size}" for c in LEARN),
          "", "## 3. 갱신할 때마다 나아지나 (A 현재 모델 대비 시험 ROC)", "", "| 후보 | 나아진 횟수 | 나빠진 횟수 | 평균 변화 | 가장 크게 나빠진 경우 |", "|---|---|---|---|---|"]
    for c in LEARN:
        d = (R[:, :, CANDS.index(c)] - R[:, :, 0]).ravel()
        L.append(f"| {c} | {int((d > 1e-9).sum())}/{d.size} | {int((d < -1e-9).sum())}/{d.size} | {d.mean():+.3f} | {d.min():+.3f} |")
    L += ["", "## 4. 세 가지 길의 시험 ROC (갱신 0회 = 합성 베이스)", "", "| 길 | 0회 " + hdr, "|---|" + "---|" * (len(U) + 1)]
    for p_ in PATHS:
        L.append(f"| {p_} | " + " | ".join(ms(P[p_][:, ui]) for ui in range(len(U) + 1)) + " |")
    L += ["", "| 길 | 갱신 뒤 직전보다 나빠진 횟수 | 가장 크게 나빠진 한 번 | 마지막이 베이스보다 나쁜 반복 |", "|---|---|---|---|"]
    for p_ in PATHS:
        d = np.diff(P[p_], axis=1)
        L.append(f"| {p_} | {int((d < -1e-9).sum())}/{d.size} | {d.min():+.3f} | {int((P[p_][:, -1] < P[p_][:, 0] - 1e-9).sum())}/{a.reps} |")
    share = PK.sum((0, 1)) / PK.sum()
    hit = np.mean([R[rep, ui, int(np.argmax(PK[rep, ui]))] >= R[rep, ui].max() - 1e-9 for rep in range(a.reps) for ui in range(len(U))])
    regret = np.mean([R[rep, ui].max() - R[rep, ui, int(np.argmax(PK[rep, ui]))] for rep in range(a.reps) for ui in range(len(U))])
    L += ["", "## 5. 게이트가 고른 후보", "", "| " + " | ".join(CANDS) + " |", "|" + "---|" * len(CANDS),
          "| " + " | ".join(f"{x * 100:.0f}%" for x in share) + " |", "",
          f"게이트가 고른 후보가 시험에서도 최고였던 비율 {hit * 100:.0f}% · 시험 최고 후보와의 평균 차이 {regret:.3f} (검증 {a.val_persons}명으로 고르기 때문).",
          "", "## 읽는 법",
          "- 2절의 편차가 1절의 ± (나누기에 따른 표준편차)와 비슷하면: 방법 사이 차이는 사람을 어떻게 나누느냐보다 크지 않다 → 어느 방법이 낫다고 못 한다.",
          "- 3절에서 나빠진 횟수가 많으면: 갱신 한 번은 좋아질 수도 나빠질 수도 있다 → 게이트가 필요한 이유.",
          "- 4절에서 게이트 길의 '나빠진 횟수'가 항상 B · 항상 D보다 적으면: 게이트가 흔들림을 줄인다. 0이 아니면 검증 4명으로는 다 못 막는다.",
          "- 한계: 라벨이 반응이 아니라 MISC ≥ k. 17명이라 시험 5명의 ROC 자체가 ±0.03~0.05 흔들린다. 사람 1명 ≈ 140행.",
          "", f"총 {time.time() - T0:.0f}초"]
    (out / "summary.md").write_text("\n".join(L), encoding="utf-8"); log("\n".join(L))

    try:
        import logging, matplotlib; matplotlib.use("Agg"); logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)
        import matplotlib.pyplot as plt
        plt.rcParams["font.family"] = ["NanumGothic", "Noto Sans CJK KR", "DejaVu Sans"]; plt.rcParams["axes.unicode_minus"] = False
        fig, ax = plt.subplots(1, 3, figsize=(14, 4.2))
        xs = np.arange(len(U) + 1)
        for p_, col in zip(PATHS, ["#B45309", "#6B7280", "#0F766E"]):
            m_, s_ = P[p_].mean(0), P[p_].std(0)
            ax[0].plot(xs, m_, marker="o", color=col, label=p_, lw=1.8); ax[0].fill_between(xs, m_ - s_, m_ + s_, color=col, alpha=.12)
        ax[0].set_xticks(xs); ax[0].set_xticklabels(["0"] + [f"{u}\n({n}명)" for u, n in zip(U, cumn)]); ax[0].set_xlabel("갱신 횟수 (누적 로그 사람 수)")
        ax[0].set_ylabel("시험 ROC"); ax[0].set_title("세 가지 길 · 평균 ± 표준편차"); ax[0].legend(fontsize=8); ax[0].grid(alpha=.3)
        for rep in range(a.reps):
            ax[1].plot(xs, P["항상 B"][rep], color="#B45309", alpha=.25, lw=.8); ax[1].plot(xs, P["게이트"][rep], color="#0F766E", alpha=.25, lw=.8)
        ax[1].plot([], [], color="#B45309", label="항상 B"); ax[1].plot([], [], color="#0F766E", label="게이트")
        ax[1].set_xticks(xs); ax[1].set_xlabel("갱신 횟수"); ax[1].set_title(f"반복 {a.reps}번을 한 줄씩"); ax[1].legend(fontsize=8); ax[1].grid(alpha=.3)
        ax[2].boxplot([spread[:, ui] for ui in range(len(U))]); ax[2].set_xticks(range(1, len(U) + 1)); ax[2].set_xticklabels([f"{u}회" for u in U]); ax[2].set_ylabel("B · C · D 사이 최고 − 최저 (ROC)")
        ax[2].set_title("학습 방법 사이 편차"); ax[2].grid(alpha=.3)
        fig.tight_layout(); fig.savefig(out / "fig.png", dpi=150)
    except Exception as e:                                       # 그림이 안 돼도 표는 남긴다
        log(f"그림 생략: {e}")
    log(f"저장: {out}")


if __name__ == "__main__":
    main()
