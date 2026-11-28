"""라벨 없이 시뮬레이터로 사전학습한 FT에 4TU 사람 1~2명만 주면 바로 맞히는가 (노트북 30분 실험)

    python3 ml/experiments/ft_transfer_4tu.py ml/data/raw/ATDOMS.mat

조건: Oman 규칙(학습 없음) / GBM / FT 처음부터 / FT 사전학습(결측 전부 겪음)
      --also-table 을 주면 "FT 사전학습(표 확률 결측만)" 한 줄이 더 붙는다.
축:   4TU 학습 사람 수 n ∈ {1, 2, 전부}. 사람 5-fold. 지표 PR-AUC(k=3).
출력: ml/out/ft_transfer/results.csv, summary.md, fig_n_vs_prauc.png, tudelft_rows.npz(캐시)

피처는 feature_order.json의 enabled=true 74개만 토큰으로 쓴다 (깃허브 현행과 동일).
사전학습 입력은 dataset.npz의 X만 쓰고 라벨(y, misc)은 읽지 않는다.
"""
import argparse
import json
import pathlib
import sys
import tempfile
import time

import numpy as np

ML = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ML))
sys.path.insert(0, str(ML / "train"))
sys.path.insert(0, str(ML / "verify"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import fo_compat as FC  # noqa: E402  (피처 목록 v3 77칸 / v5 52칸 모두)
import common as C            # noqa: E402  feature_order, GROUPS, DROPPABLE, PARAMS
import oman                   # noqa: E402
import runs                   # noqa: E402
import trip as tripio         # noqa: E402
import tudelft_replay as TR   # noqa: E402  session, stimulus, make_trip, AMPS

FO = C.FO
ENABLED = [i for i, f in enumerate(C.FEATS) if f["enabled"]]          # 77 → 74
POS = {i: j for j, i in enumerate(ENABLED)}                            # 77 인덱스 → 토큰 위치
NF = len(ENABLED)
GROUP_POS = {g: [POS[i] for i in idx if i in POS] for g, idx in C.GROUPS.items()}
DROP_POS = {g: [POS[i] for i in idx if i in POS] for g, idx in C.DROPPABLE.items()}   # 표 확률로 지울 수 있는 칸
NEVER_POS = sorted({POS[i] for i in C.NEVER if i in POS})
CAN_GROUPS = FC.present(["G2", "G3a", "G3b", "G4", "G5", "G6", "G7"])
OMAN_POS = POS[C.NAMES.index("oman_score")]


# ----------------------------------------------------------------------------- 데이터
def load_sim_rows(path, n_rows, rng):
    d = np.load(path, allow_pickle=False)
    assert int(d["feature_version"]) == FO["version"]
    X = d["X"][:, ENABLED]
    idx = rng.choice(len(X), size=min(n_rows, len(X)), replace=False)
    return X[idx].astype(np.float32)


def build_tudelft(mat, cache, v0_kmh=50.0, seed=0):
    """4TU → 74열 행 + future_max + 사람 번호. tudelft_replay.py 와 같은 변환. featurize 결과는 캐시."""
    if cache.exists():
        z = np.load(cache)
        return z["X"], z["fut"], z["pid"]
    import scipy.io as sio
    A = sio.loadmat(mat, squeeze_me=True, struct_as_record=False)["ATDOMS"]
    persons = list(A._fieldnames)
    v0 = v0_kmh / 3.6
    Xs, futs, pids = [], [], []
    with tempfile.TemporaryDirectory() as td:
        for pi, p in enumerate(persons):
            mssq = float(getattr(A, p).MSSQ.Score)
            for c, amp in TR.AMPS.items():
                seg, tr, yr = TR.session(getattr(getattr(A, p), c))
                n = int((tr[-1] + 10) * TR.FS)
                acc = TR.stimulus(seg, n, amp)
                rng = np.random.default_rng([seed, pi, int(c[1])])
                path = pathlib.Path(td) / "trip.bin"
                tripio.write_trip(path, TR.make_trip(acc, v0, rng))
                t, Xv = tripio.featurize(path, pathlib.Path(td) / "features.bin")
                X = FC.full_row(Xv, mssq, float(oman.sensitivity(mssq)))
                win = lambda tt: yr[(tr > tt) & (tr <= tt + 300)]
                fut = np.array([np.nanmax(w) if tt + 300 <= tr[-1] and np.isfinite(w := win(tt)).any() else np.nan
                                for tt in t])
                v = np.isfinite(fut)
                Xs.append(X[v][:, ENABLED]); futs.append(fut[v]); pids.append(np.full(int(v.sum()), pi))
    X, fut, pid = np.concatenate(Xs).astype(np.float32), np.concatenate(futs), np.concatenate(pids)
    np.savez_compressed(cache, X=X, fut=fut, pid=pid)
    return X, fut, pid


# ----------------------------------------------------------------------------- 결측 패턴
def apply_dropout(present, rng, mode):
    """present: (B, NF) bool, 원래 있는 칸. mode='all' = 세 패턴 섞음, 'table' = 표 확률만."""
    B = present.shape[0]
    present = present.copy()
    which = rng.integers(0, 3, size=B) if mode == "all" else np.zeros(B, dtype=int)
    # ① 표 확률대로 그룹 드롭 (행 단위)
    r1 = which == 0
    for g, cols in DROP_POS.items():
        drop = r1 & (rng.random(B) < FO["groups"][g]["dropout"])
        present[np.ix_(drop, cols)] = False
    # ② CAN 전부 없음 (4TU·실차 공개 데이터 같은 상황)
    r2 = which == 1
    for g in CAN_GROUPS:
        present[np.ix_(r2, GROUP_POS[g])] = False
    # ③ 지울 수 있는 그룹 중 무작위 k개 없음
    r3 = np.where(which == 2)[0]
    gs = list(DROP_POS)
    for b in r3:
        k = rng.integers(1, len(gs))
        for g in rng.choice(gs, size=k, replace=False):
            present[b, DROP_POS[g]] = False
    # 칸 단위 2% (항상 있어야 하는 칸 제외)
    cell = rng.random(present.shape) < FO["cell_dropout"]
    cell[:, NEVER_POS] = False
    present &= ~cell
    return present


# ----------------------------------------------------------------------------- FT 모델
def make_torch():
    import torch
    import torch.nn as nn

    class FT(nn.Module):
        def __init__(self, nf, d=32, layers=2, heads=4):
            super().__init__()
            self.w = nn.Parameter(torch.randn(nf, d) * 0.1)        # 값 임베딩 기울기
            self.b = nn.Parameter(torch.randn(nf, d) * 0.1)        # 피처 위치 임베딩
            self.miss = nn.Parameter(torch.randn(nf, d) * 0.1)     # 피처별 [MISS]
            self.cls = nn.Parameter(torch.zeros(1, 1, d))
            enc = nn.TransformerEncoderLayer(d, heads, dim_feedforward=2 * d, dropout=0.1, batch_first=True)
            self.enc = nn.TransformerEncoder(enc, layers)
            self.recon = nn.Linear(d, 1)
            self.head = nn.Linear(d, 1)

        def tokens(self, x, present):
            # x: (B, nf) 표준화 값(결측은 0), present: (B, nf) bool
            val = x.unsqueeze(-1) * self.w + self.b
            tok = torch.where(present.unsqueeze(-1), val, self.miss.expand_as(val))
            return torch.cat([self.cls.expand(x.shape[0], -1, -1), tok], 1)

        def forward(self, x, present):
            h = self.enc(self.tokens(x, present))
            return h[:, 0], h[:, 1:]

    return torch, nn, FT


def standardize(X, mu, sd):
    Z = (X - mu) / sd
    Z = np.clip(np.nan_to_num(Z, nan=0.0), -5, 5)
    return Z.astype(np.float32)


def pretrain(torch, FT, Xsim, mu, sd, mode, steps, batch, rng, log):
    model = FT(NF)
    opt = torch.optim.AdamW(model.parameters(), lr=1e-3, weight_decay=1e-4)
    Z = standardize(Xsim, mu, sd)
    P0 = np.isfinite(Xsim)
    t0 = time.time()
    for s in range(steps):
        idx = rng.integers(0, len(Z), size=batch)
        present = apply_dropout(P0[idx], rng, mode)
        target = present & (rng.random(present.shape) < 0.3)          # 있는 칸 중 30%를 가림
        inp = present & ~target
        x = torch.from_numpy(Z[idx])
        _, h = model(x, torch.from_numpy(inp))
        pred = model.recon(h).squeeze(-1)
        tgt = torch.from_numpy(target)
        loss = ((pred - x) ** 2)[tgt].mean()
        opt.zero_grad(); loss.backward(); opt.step()
        if (s + 1) % 500 == 0:
            log(f"    사전학습[{mode}] {s + 1}/{steps} 복원손실 {loss.item():.3f} ({time.time() - t0:.0f}s)")
    return model


def finetune(torch, FT, init_state, Ztr, Ptr, ytr, Zte, Pte, steps, seed):
    torch.manual_seed(seed)
    model = FT(NF)
    if init_state is not None:
        model.load_state_dict(init_state)
    opt = torch.optim.AdamW(model.parameters(), lr=5e-4, weight_decay=1e-4)
    rng = np.random.default_rng(seed)
    x_all, p_all, y_all = torch.from_numpy(Ztr), torch.from_numpy(Ptr), torch.from_numpy(ytr.astype(np.float32))
    pos = float(ytr.mean())
    pw = torch.tensor([(1 - pos) / max(pos, 1e-3)])                   # 양성 가중 (비율 맞춤)
    lossf = torch.nn.BCEWithLogitsLoss(pos_weight=pw)
    model.train()
    for _ in range(steps):
        idx = rng.integers(0, len(Ztr), size=min(128, len(Ztr)))
        cls, _ = model(x_all[idx], p_all[idx])
        loss = lossf(model.head(cls).squeeze(-1), y_all[idx])
        opt.zero_grad(); loss.backward(); opt.step()
    model.eval()
    with torch.no_grad():
        cls, _ = model(torch.from_numpy(Zte), torch.from_numpy(Pte))
        return torch.sigmoid(model.head(cls).squeeze(-1)).numpy()


def gbm(Xtr, ytr, Xte, seed):
    import lightgbm as lgb
    params = dict(C.PARAMS, seed=seed, num_threads=4)
    params["min_data_in_leaf"] = int(min(100, max(5, len(Xtr) // 20)))   # repo 값 1000은 사람 1명 분량보다 크다
    params["monotone_constraints"] = [C.MONOTONE[i] for i in ENABLED]
    m = lgb.train(params, lgb.Dataset(Xtr, ytr), num_boost_round=200)
    return m.predict(Xte)


# ----------------------------------------------------------------------------- 실행
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mat", help="ATDOMS.mat")
    ap.add_argument("--dataset", help="시뮬레이터 dataset.npz (기본: 배포 모델이 학습한 것)")
    ap.add_argument("--rows", type=int, default=100_000)
    ap.add_argument("--steps", type=int, default=3000)
    ap.add_argument("--ft-steps", type=int, default=300)
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--also-table", action="store_true", help="표 확률 결측만으로 사전학습한 몸통도 비교")
    ap.add_argument("--out", default=str(FC.out_dir("ft_transfer")))
    a = ap.parse_args()
    out = pathlib.Path(a.out); out.mkdir(parents=True, exist_ok=True)
    logf = open(out / "log.txt", "w")
    def log(s):
        print(s); logf.write(s + "\n"); logf.flush()

    torch, nn, FT = make_torch()
    torch.set_num_threads(max(1, torch.get_num_threads()))
    rng = np.random.default_rng(0)
    T0 = time.time()

    ds = a.dataset or runs.dataset_for(runs.current_v1_models())
    log(f"시뮬레이터 입력: {ds}")
    Xsim = load_sim_rows(ds, a.rows, rng)
    mu = np.nanmean(Xsim, 0); sd = np.nanstd(Xsim, 0); sd[~np.isfinite(sd) | (sd < 1e-6)] = 1.0
    mu = np.nan_to_num(mu)
    log(f"  {len(Xsim)}행 × {NF}토큰. 라벨은 읽지 않음.")

    log("4TU 행 만들기 (featurize)…")
    X4, fut, pid = build_tudelft(a.mat, out / "tudelft_rows.npz")
    y4 = (fut >= a.k).astype(int)
    log(f"  {len(X4)}행, 사람 {len(np.unique(pid))}명, 양성 비율(k={a.k}) {y4.mean():.3f}")
    Z4, P4 = standardize(X4, mu, sd), np.isfinite(X4)

    bodies = {"all": None, "table": None} if a.also_table else {"all": None}
    for mode in bodies:
        log(f"사전학습 ({mode}) {a.steps} 스텝…")
        m = pretrain(torch, FT, Xsim, mu, sd, mode, a.steps, 256, rng, log)
        bodies[mode] = {k: v.clone() for k, v in m.state_dict().items()}
        torch.save(bodies[mode], out / f"body_{mode}.pt")

    from sklearn.metrics import average_precision_score, roc_auc_score
    from sklearn.model_selection import GroupKFold
    persons = np.unique(pid)
    rows = []
    conds = ["Oman 규칙", "GBM", "FT 처음부터", "FT 사전학습(결측 전부)"] + (["FT 사전학습(표 확률만)"] if a.also_table else [])
    for fold, (tr_idx, te_idx) in enumerate(GroupKFold(5).split(X4, y4, pid)):
        tr_p = np.unique(pid[tr_idx]); te = te_idx
        yte = y4[te]
        if yte.min() == yte.max():
            continue
        for n in [1, 2, 99]:                       # 99 = 학습 사람 전부 (폴드마다 13~14명이라 하나로 묶음)
            n_eff = len(tr_p) if n == 99 else n
            seeds = [0] if n == 99 else [0, 1, 2]
            for seed in seeds:
                srng = np.random.default_rng([fold, n, seed])
                chosen = srng.choice(tr_p, size=n_eff, replace=False)
                tr = np.where(np.isin(pid, chosen))[0]
                if y4[tr].min() == y4[tr].max():          # 뽑힌 사람이 한 번도 k에 안 닿음 → 다시 뽑기
                    for _ in range(20):
                        chosen = srng.choice(tr_p, size=n_eff, replace=False); tr = np.where(np.isin(pid, chosen))[0]
                        if y4[tr].min() != y4[tr].max():
                            break
                preds = {"Oman 규칙": X4[te, OMAN_POS]}
                preds["GBM"] = gbm(X4[tr], y4[tr], X4[te], seed)
                preds["FT 처음부터"] = finetune(torch, FT, None, Z4[tr], P4[tr], y4[tr], Z4[te], P4[te], a.ft_steps, seed)
                preds["FT 사전학습(결측 전부)"] = finetune(torch, FT, bodies["all"], Z4[tr], P4[tr], y4[tr], Z4[te], P4[te], a.ft_steps, seed)
                if a.also_table:
                    preds["FT 사전학습(표 확률만)"] = finetune(torch, FT, bodies["table"], Z4[tr], P4[tr], y4[tr], Z4[te], P4[te], a.ft_steps, seed)
                for cnd, s in preds.items():
                    s = np.nan_to_num(s, nan=float(np.nanmin(s)) if np.isfinite(s).any() else 0.0)
                    rows.append({"fold": fold, "n": n, "seed": seed, "cond": cnd,
                                 "pr_auc": float(average_precision_score(yte, s)), "roc_auc": float(roc_auc_score(yte, s))})
            log(f"  fold {fold} n={n} 끝 ({time.time() - T0:.0f}s)")

    # ---- 정리
    import csv
    with open(out / "results.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    ns = sorted({r["n"] for r in rows})
    n_label = {n: ("전부" if n == 99 else str(n)) for n in ns}
    agg = {}
    for cnd in conds:
        for n in ns:
            v = np.array([r["pr_auc"] for r in rows if r["cond"] == cnd and r["n"] == n])
            v2 = np.array([r["roc_auc"] for r in rows if r["cond"] == cnd and r["n"] == n])
            agg[(cnd, n)] = (v.mean(), v.std(), v2.mean(), len(v))
    L = [f"# FT 전이 실험 — 4TU (k={a.k}, PR-AUC 평균 ± 표준편차, 괄호는 ROC-AUC)", "",
         f"사전학습: 시뮬레이터 {len(Xsim)}행, {a.steps}스텝, 라벨 없음. 미세조정 {a.ft_steps}스텝. 사람 5-fold.", "",
         "| 조건 | " + " | ".join(f"n={n_label[n]}" for n in ns) + " |", "|---|" + "---|" * len(ns)]
    for cnd in conds:
        L.append(f"| {cnd} | " + " | ".join(f"{agg[(cnd, n)][0]:.3f} ± {agg[(cnd, n)][1]:.3f} ({agg[(cnd, n)][2]:.2f})" for n in ns) + " |")
    L += ["", "읽는 법: n=1·2에서 'FT 사전학습'이 'GBM'·'FT 처음부터'보다 위면 사전학습이 바로 통한 것. "
          "n=전부에서 비슷해지는 것은 정상. 전부 'Oman 규칙' 근처면 사람 1~2명으로는 규칙을 못 넘는 것.",
          f"", f"총 {time.time() - T0:.0f}초"]
    (out / "summary.md").write_text("\n".join(L), encoding="utf-8")
    log("\n".join(L))

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import logging; logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)
    plt.rcParams["font.family"] = ["NanumGothic", "Noto Sans CJK KR", "DejaVu Sans"]
    fig, ax = plt.subplots(figsize=(6, 4))
    xs = range(len(ns))
    for cnd in conds:
        m = [agg[(cnd, n)][0] for n in ns]; e = [agg[(cnd, n)][1] for n in ns]
        ax.errorbar(xs, m, yerr=e, marker="o", capsize=3, label=cnd)
    ax.set_xticks(list(xs)); ax.set_xticklabels([n_label[n] for n in ns])
    ax.set_xlabel("4TU 학습 사람 수"); ax.set_ylabel(f"PR-AUC (k={a.k})")
    ax.legend(fontsize=8); ax.grid(alpha=.3)
    fig.tight_layout(); fig.savefig(out / "fig_n_vs_prauc.png", dpi=150)
    log(f"저장: {out}")


if __name__ == "__main__":
    main()
