"""라벨 없이 사전학습한 FT에 4TU를 꽂으면 바로 맞히나 — 30분짜리 실험

놓는 위치: ml/verify/ft_pretrain_4tu.py  (repo 모듈을 그대로 가져다 쓴다)

    python3 ml/verify/ft_pretrain_4tu.py ml/data/raw/ATDOMS.mat
    python3 ml/verify/ft_pretrain_4tu.py ml/data/raw/ATDOMS.mat --control     # 표 확률만 쓴 대조 몸통도 같이

준비:
    cmake -S controller -B build && cmake --build build      # featurize (4TU → 피처)
    python3 ml/simulator/build_dataset.py                    # 시뮬레이터 dataset.npz (이미 있으면 생략)
    pip install torch --index-url https://download.pytorch.org/whl/cpu

하는 일:
    1. 시뮬레이터 dataset.npz에서 --rows 행을 뽑아(라벨 버림) FT를 "가리고 복원"으로 사전학습.
       배치마다 결측 패턴 셋 중 하나: 표 확률대로 / G2~G7 전부 없음 / 그룹 무작위 k개 없음. 그 위에 칸 2% + 가리기 30%.
    2. 4TU 17명을 tudelft_replay와 같은 방법으로 77열 행 + 라벨(다음 5분 최대 MISC >= 3, >= 4)로 변환 (캐시).
    3. 사람 5-fold. 학습 사람 중 n ∈ {1, 2, 전부}명 라벨만으로:
       Oman 규칙(학습 없음) / GBM / FT-처음부터 / FT-사전학습 [/ FT-사전학습(표만)]
    4. 시험 사람 PR-AUC·ROC-AUC. 그림 한 장 + CSV.

토큰 = enabled 피처(74개) + [CLS]. 꺼진 3개(svc_conflict, tol_display, tol_phone)는 안 쓴다.
"""
import argparse
import json
import pathlib
import sys
import tempfile
import time

import numpy as np

HERE = pathlib.Path(__file__).resolve()
ML = HERE.parents[1]
REPO = HERE.parents[2]
sys.path.insert(0, str(ML))
sys.path.insert(0, str(ML / "train"))
sys.path.insert(0, str(HERE.parent))

import common  # noqa: E402  (ml/train/common.py)
import oman  # noqa: E402
import trip as tripio  # noqa: E402
import tudelft_replay as tr  # noqa: E402

FEATS = common.FEATS
ENABLED = [i for i, f in enumerate(FEATS) if f["enabled"]]          # 74
POS = {i: p for p, i in enumerate(ENABLED)}                          # 77 인덱스 → 토큰 위치
F = len(ENABLED)
# 토큰 위치 기준 그룹
GROUPS = {g: [POS[i] for i in idx if i in POS] for g, idx in common.GROUPS.items()}
DROPPABLE = {g: [POS[i] for i in idx if i in POS] for g, idx in common.DROPPABLE.items()}
DROPPABLE = {g: v for g, v in DROPPABLE.items() if v}
DROP_P = {g: common.FO["groups"][g]["dropout"] for g in DROPPABLE}
CAN_ALL = sorted(sum((GROUPS[g] for g in ("G2", "G3a", "G3b", "G4", "G5", "G6", "G7")), []))
CELL_OK = sorted(set(sum(DROPPABLE.values(), [])))                    # 칸 단위 결측을 넣어도 되는 자리
OMAN_POS = POS[common.NAMES.index("oman_score")]
MONO = [FEATS[i]["monotone"] for i in ENABLED]
KS = (3, 4)


# ----------------------------------------------------------------------------- 데이터
def load_sim(path, rows, rng):
    d = common.load(path)
    X, ride = d["X"][:, ENABLED], d["ride_id"]
    rides = np.unique(ride)
    rng.shuffle(rides)
    keep = np.isin(ride, rides[: max(1, int(len(rides) * min(1.0, rows / len(X))) + 1)])
    X = X[keep][:rows]
    print(f"시뮬레이터 {len(X):,}행 (탑승 {keep.sum() and len(np.unique(ride[keep]))}개)")
    return X


def build_4tu(mat, cache, v0=50 / 3.6, seed=0):
    """→ X (N, 74), y (N, 2) for k=3,4, person (N,)"""
    if cache.exists():
        d = np.load(cache)
        return d["X"], d["y"], d["person"]
    import scipy.io as sio
    A = sio.loadmat(mat, squeeze_me=True, struct_as_record=False)["ATDOMS"]
    persons = list(A._fieldnames)
    Xs, ys, ps = [], [], []
    with tempfile.TemporaryDirectory() as td:
        for pi, p in enumerate(persons):
            mssq = float(getattr(A, p).MSSQ.Score)
            for c, amp in tr.AMPS.items():
                seg, t_rep, y_rep = tr.session(getattr(getattr(A, p), c))
                n = int((t_rep[-1] + 10) * tr.FS)
                acc = tr.stimulus(seg, n, amp)
                rng = np.random.default_rng([seed, pi, int(c[1])])
                path = pathlib.Path(td) / "trip.bin"
                tripio.write_trip(path, tr.make_trip(acc, v0, rng))
                t, Xv = tripio.featurize(path, pathlib.Path(td) / "features.bin")
                X = np.full((len(t), 77), np.nan)
                X[:, :71] = Xv
                X[:, 73], X[:, 74], X[:, 76] = mssq, float(oman.sensitivity(mssq)), 0.0
                win = lambda tt: y_rep[(t_rep > tt) & (t_rep <= tt + 300)]
                fut = np.array([np.nanmax(w) if tt + 300 <= t_rep[-1] and np.isfinite(w := win(tt)).any() else np.nan
                                for tt in t])
                v = np.isfinite(fut)
                Xs.append(X[v][:, ENABLED]); ys.append(np.column_stack([(fut[v] >= k) for k in KS]).astype(np.float32))
                ps.append(np.full(int(v.sum()), pi))
            print(f"  4TU {p} 변환 완료")
    X, y, person = np.concatenate(Xs), np.concatenate(ys), np.concatenate(ps)
    cache.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(cache, X=X, y=y, person=person)
    return X, y, person


class Scaler:
    def __init__(self, X):
        self.m = np.nanmean(X, 0); self.s = np.nanstd(X, 0) + 1e-6
        self.m[~np.isfinite(self.m)] = 0; self.s[~np.isfinite(self.s)] = 1

    def __call__(self, X):
        z = np.clip((X - self.m) / self.s, -5, 5)
        present = np.isfinite(z)
        return np.where(present, z, 0.0).astype(np.float32), present


# ----------------------------------------------------------------------------- 결측 패턴
def apply_dropout(present, rng, mode):
    """present (B,F) bool → 결측을 더한 복사본. mode: 'all' (패턴 셋 섞음) / 'table' (표 확률만)"""
    P = present.copy()
    B = len(P)
    which = rng.integers(0, 3, B) if mode == "all" else np.zeros(B, int)
    # ① 표 확률
    for g, cols in DROPPABLE.items():
        drop = (which == 0) & (rng.random(B) < DROP_P[g])
        P[np.ix_(drop, cols)] = False
    # ② CAN 전부 없음 (4TU처럼)
    P[np.ix_(which == 1, CAN_ALL)] = False
    # ③ 그룹 무작위 k개
    gl = list(DROPPABLE)
    for b in np.where(which == 2)[0]:
        k = rng.integers(1, len(gl) + 1)
        for g in rng.choice(gl, k, replace=False):
            P[b, DROPPABLE[g]] = False
    # 칸 2%
    cell = rng.random((B, len(CELL_OK))) < common.FO["cell_dropout"]
    sub = P[:, CELL_OK]; sub[cell] = False; P[:, CELL_OK] = sub
    return P


# ----------------------------------------------------------------------------- 모델
def make_torch():
    import torch
    import torch.nn as nn

    class FT(nn.Module):
        def __init__(s, F, d=32, L=2, H=4):
            super().__init__()
            s.w = nn.Parameter(torch.randn(F, d) * 0.02)
            s.b = nn.Parameter(torch.zeros(F, d))
            s.miss = nn.Parameter(torch.randn(F, d) * 0.02)
            s.cls = nn.Parameter(torch.zeros(1, 1, d))
            layer = nn.TransformerEncoderLayer(d, H, 2 * d, dropout=0.1, batch_first=True, norm_first=True)
            s.enc = nn.TransformerEncoder(layer, L)
            s.norm = nn.LayerNorm(d)
            s.recon = nn.Linear(d, 1)
            s.head = nn.Linear(d, len(KS))

        def forward(s, z, present):
            tok = z.unsqueeze(-1) * s.w + s.b
            tok = torch.where(present.unsqueeze(-1), tok, s.miss.expand_as(tok))
            h = s.norm(s.enc(torch.cat([s.cls.expand(z.size(0), -1, -1), tok], 1)))
            return h[:, 0], h[:, 1:]

    return torch, nn, FT


def pretrain(Z, PRES, mode, steps, bs, seed, torch, FT, log_every=500):
    rng = np.random.default_rng(seed); torch.manual_seed(seed)
    model = FT(F)
    opt = torch.optim.AdamW(model.parameters(), lr=3e-4, weight_decay=0.01)
    t0 = time.time()
    for it in range(1, steps + 1):
        idx = rng.integers(0, len(Z), bs)
        z, present = Z[idx], apply_dropout(PRES[idx], rng, mode)
        masked = present & (rng.random(present.shape) < 0.30)
        vis = present & ~masked
        zt, vt, mt = torch.from_numpy(z), torch.from_numpy(vis), torch.from_numpy(masked)
        _, h = model(zt * vt, vt)
        pred = model.recon(h).squeeze(-1)
        loss = ((pred - zt) ** 2)[mt].mean()
        opt.zero_grad(); loss.backward(); opt.step()
        if it % log_every == 0 or it == steps:
            print(f"  사전학습[{mode}] {it}/{steps} 복원손실 {loss.item():.3f}  ({time.time() - t0:.0f}s)")
    return model


def finetune(init_state, Ztr, Ptr, ytr, Zte, Pte, steps, seed, torch, FT, bs=128, lr=3e-4):
    rng = np.random.default_rng(seed); torch.manual_seed(seed)
    model = FT(F)
    if init_state is not None:
        model.load_state_dict(init_state)
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=0.01)
    bce = torch.nn.BCEWithLogitsLoss()
    yt = torch.from_numpy(ytr)
    for _ in range(steps):
        idx = rng.integers(0, len(Ztr), min(bs, len(Ztr)))
        cls, _ = model(torch.from_numpy(Ztr[idx]), torch.from_numpy(Ptr[idx]))
        loss = bce(model.head(cls), yt[idx])
        opt.zero_grad(); loss.backward(); opt.step()
    model.eval()
    with torch.no_grad():
        cls, _ = model(torch.from_numpy(Zte), torch.from_numpy(Pte))
        return torch.sigmoid(model.head(cls)).numpy()


def gbm(Xtr, ytr, Xte):
    import lightgbm as lgb
    params = dict(common.PARAMS, min_data_in_leaf=20, num_threads=4)   # repo 설정은 10만 행용이라 잎 최소 행만 낮춤
    out = np.zeros((len(Xte), len(KS)))
    for j in range(len(KS)):
        if ytr[:, j].min() == ytr[:, j].max():
            out[:, j] = ytr[:, j].mean(); continue
        ds = lgb.Dataset(Xtr, ytr[:, j], params={"monotone_constraints": MONO})
        bst = lgb.train(dict(params, monotone_constraints=MONO), ds, num_boost_round=200)
        out[:, j] = bst.predict(Xte)
    return out


def scores(y, p):
    from sklearn.metrics import average_precision_score, roc_auc_score
    r = {}
    for j, k in enumerate(KS):
        if y[:, j].min() == y[:, j].max() or not np.isfinite(p[:, j]).all():
            r[k] = (np.nan, np.nan); continue
        r[k] = (roc_auc_score(y[:, j], p[:, j]), average_precision_score(y[:, j], p[:, j]))
    return r


# ----------------------------------------------------------------------------- 실험
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mat")
    ap.add_argument("--dataset", help="시뮬레이터 dataset.npz (기본: CURRENT 모델의 데이터셋)")
    ap.add_argument("--rows", type=int, default=100_000)
    ap.add_argument("--steps", type=int, default=3000)
    ap.add_argument("--ft-steps", type=int, default=300)
    ap.add_argument("--reps", type=int, default=3, help="n=1,2에서 다른 사람으로 반복")
    ap.add_argument("--control", action="store_true", help="표 확률만 쓴 대조 몸통도 학습")
    ap.add_argument("--out", default=str(ML / "verify" / "out" / "ft4tu"))
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    out = pathlib.Path(a.out); out.mkdir(parents=True, exist_ok=True)
    torch, nn, FT = make_torch()
    torch.set_num_threads(max(1, torch.get_num_threads()))
    rng = np.random.default_rng(a.seed)
    T0 = time.time()

    # 1. 데이터
    Xsim = load_sim(a.dataset, a.rows, rng)
    scaler = Scaler(Xsim)
    Zs, Ps = scaler(Xsim)
    X4, y4, person = build_4tu(a.mat, out / "tudelft_rows.npz")
    Z4, P4 = scaler(X4)
    print(f"4TU {len(X4):,}행, {len(np.unique(person))}명, 양성률 k=3 {y4[:, 0].mean():.2f} k=4 {y4[:, 1].mean():.2f}  ({time.time() - T0:.0f}s)")

    # 2. 사전학습
    bodies = {"FT-사전학습": pretrain(Zs, Ps, "all", a.steps, 256, a.seed, torch, FT).state_dict()}
    if a.control:
        bodies["FT-사전학습(표만)"] = pretrain(Zs, Ps, "table", a.steps, 256, a.seed, torch, FT).state_dict()
    print(f"사전학습 끝 ({time.time() - T0:.0f}s)")

    # 3. 4TU 꽂기
    from sklearn.model_selection import GroupKFold
    persons = np.unique(person)
    rows = []
    for fold, (tr_i, te_i) in enumerate(GroupKFold(5).split(X4, y4, person)):
        train_persons = np.unique(person[tr_i])
        for n in (1, 2, "전부"):
            reps = 1 if n == "전부" else a.reps
            for rep in range(reps):
                r2 = np.random.default_rng([a.seed, fold, rep, 0 if n == "전부" else n])
                chosen = train_persons if n == "전부" else r2.choice(train_persons, n, replace=False)
                tr_sel = tr_i[np.isin(person[tr_i], chosen)]
                Xtr, ytr, Xte, yte = X4[tr_sel], y4[tr_sel], X4[te_i], y4[te_i]
                preds = {"Oman 규칙": np.repeat(X4[te_i][:, [OMAN_POS]], len(KS), 1),
                         "GBM": gbm(Xtr, ytr, Xte),
                         "FT-처음부터": finetune(None, Z4[tr_sel], P4[tr_sel], ytr, Z4[te_i], P4[te_i], a.ft_steps,
                                                 a.seed + rep, torch, FT)}
                for name, st in bodies.items():
                    preds[name] = finetune(st, Z4[tr_sel], P4[tr_sel], ytr, Z4[te_i], P4[te_i], a.ft_steps,
                                           a.seed + rep, torch, FT)
                for name, p in preds.items():
                    for k, (roc, pr) in scores(yte, p).items():
                        rows.append(dict(cond=name, n=str(n), fold=fold, rep=rep, k=k, roc=roc, pr=pr,
                                         n_train_rows=len(tr_sel)))
        print(f"  fold {fold + 1}/5 끝 ({time.time() - T0:.0f}s)")

    # 4. 결과
    import pandas as pd
    df = pd.DataFrame(rows)
    df.to_csv(out / "results.csv", index=False)
    order = ["Oman 규칙", "GBM", "FT-처음부터", "FT-사전학습"] + (["FT-사전학습(표만)"] if a.control else [])
    summ = df.groupby(["k", "cond", "n"])[["pr", "roc"]].agg(["mean", "std"]).reset_index()
    lines = ["# FT 사전학습 → 4TU", "", f"시뮬레이터 {len(Xsim):,}행 사전학습 {a.steps}스텝, 미세조정 {a.ft_steps}스텝, 사람 5-fold", ""]
    for k in KS:
        lines += [f"## k = {k}  (PR-AUC 평균 ± 표준편차, 괄호 ROC-AUC)", "", "| 조건 | n=1 | n=2 | 전부 |", "|---|---|---|---|"]
        for c in order:
            cells = []
            for n in ("1", "2", "전부"):
                s = summ[(summ.k == k) & (summ.cond == c) & (summ.n == n)]
                cells.append(f"{s[('pr', 'mean')].iloc[0]:.3f} ± {s[('pr', 'std')].iloc[0]:.3f} ({s[('roc', 'mean')].iloc[0]:.2f})"
                             if len(s) else "-")
            lines.append(f"| {c} | " + " | ".join(cells) + " |")
        lines.append("")
    (out / "report.md").write_text("\n".join(lines))
    print("\n".join(lines))

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 2, figsize=(10, 4), sharey=False)
    for ax, k in zip(axes, KS):
        for c in order:
            s = summ[(summ.k == k) & (summ.cond == c)].set_index("n").reindex(["1", "2", "전부"])
            ax.errorbar([0, 1, 2], s[("pr", "mean")], yerr=s[("pr", "std")], marker="o", capsize=3, label=c)
        ax.set_xticks([0, 1, 2]); ax.set_xticklabels(["1명", "2명", "전부"])
        ax.set_title(f"4TU 시험 사람 PR-AUC (MISC ≥ {k})"); ax.set_xlabel("라벨을 준 학습 사람 수"); ax.grid(alpha=.3)
    axes[0].legend(fontsize=8)
    fig.tight_layout(); fig.savefig(out / "ft4tu.png", dpi=150)
    print(f"\n저장: {out / 'report.md'}, {out / 'results.csv'}, {out / 'ft4tu.png'}  (총 {time.time() - T0:.0f}s)")


if __name__ == "__main__":
    main()
