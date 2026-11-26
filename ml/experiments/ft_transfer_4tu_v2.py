"""라벨 없이 시뮬레이터로 사전학습한 FT에 4TU를 꽂으면 얼마나 빨리 맞히나 — 2시간판

    python3 ml/experiments/ft_transfer_4tu_v2.py ml/data/raw/ATDOMS.mat --dataset ml/out/runs/v002/dataset.npz

v1(30분판)에서 늘린 것
  - 사전학습 30만 행 × 1만 스텝, 복원 손실 곡선 저장 (충분한가)
  - 몸통 2개: 결측 전부(all) vs 표 확률만(table)  (결측 설계 효과)
  - GBM 사전학습(같은 행 + 합성 Oman 라벨 = repo 현행) 그대로 / 4TU로 이어 학습  (공정 비교)
  - FT 적응 2가지: 선형 탐침(몸통 동결) / 전체 미세조정
  - 축: n ∈ {1,2,4,전부}, k ∈ {3,4}
  - 열화 검사: n=전부 모델에 재학습 없이 G2 제거 / 프로필 기본값 입력
  - 라벨 없는 지표: 미세조정 전 몸통의 4TU 복원 손실

피처는 feature_order.json enabled=true 74개. FT 사전학습은 X만 쓴다(라벨 안 읽음).
출력: <out>/results.csv, summary.md, fig_k3.png, fig_k4.png, recon_curve.png, log.txt
"""
import argparse
import csv
import json
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
NF = len(ENABLED)
GROUP_POS = {g: [POS[i] for i in idx if i in POS] for g, idx in C.GROUPS.items()}
DROP_POS = {g: [POS[i] for i in idx if i in POS] for g, idx in C.DROPPABLE.items()}
NEVER_POS = sorted({POS[i] for i in C.NEVER if i in POS})
CAN_GROUPS = FC.present(["G2", "G3a", "G3b", "G4", "G5", "G6", "G7"])
OMAN_POS = POS[C.NAMES.index("oman_score")]
MSSQ_POS, SENS_POS = POS[C.NAMES.index("mssq")], POS[C.NAMES.index("sensitivity")]
MONO = [C.MONOTONE[i] for i in ENABLED]
DEFAULT_MSSQ, DEFAULT_SENS = 12.90, 1.0


# ----------------------------------------------------------------------------- 데이터
def load_sim(path, n_rows, rng, ks):
    d = np.load(path, allow_pickle=False)
    assert int(d["feature_version"]) == FO["version"]
    idx = rng.choice(len(d["X"]), size=min(n_rows, len(d["X"])), replace=False)
    X = d["X"][idx][:, ENABLED].astype(np.float32)
    klist = list(d["k"])
    Y = {k: d["y"][idx, klist.index(k)].astype(int) for k in ks}
    return X, Y, d["ride_id"][idx]


def build_tudelft(mat, cache, v0_kmh=50.0, seed=0):
    if cache.exists():
        z = np.load(cache); return z["X"], z["fut"], z["pid"]
    import scipy.io as sio
    A = sio.loadmat(mat, squeeze_me=True, struct_as_record=False)["ATDOMS"]
    persons = list(A._fieldnames); v0 = v0_kmh / 3.6
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
                fut = np.array([np.nanmax(w) if tt + 300 <= tr[-1] and np.isfinite(w := win(tt)).any() else np.nan for tt in t])
                v = np.isfinite(fut)
                Xs.append(X[v][:, ENABLED]); futs.append(fut[v]); pids.append(np.full(int(v.sum()), pi))
    X, fut, pid = np.concatenate(Xs).astype(np.float32), np.concatenate(futs), np.concatenate(pids)
    np.savez_compressed(cache, X=X, fut=fut, pid=pid)
    return X, fut, pid


def degrade(X, how):
    X = X.copy()
    if how == "G2 제거":
        X[:, GROUP_POS["G2"]] = np.nan
    elif how == "프로필 기본값":
        X[:, MSSQ_POS], X[:, SENS_POS] = DEFAULT_MSSQ, DEFAULT_SENS
    return X


# ----------------------------------------------------------------------------- 결측 패턴
def apply_dropout(present, rng, mode):
    B = present.shape[0]; present = present.copy()
    which = rng.integers(0, 3, size=B) if mode == "all" else np.zeros(B, dtype=int)
    r1 = which == 0
    for g, cols in DROP_POS.items():
        drop = r1 & (rng.random(B) < FO["groups"][g]["dropout"])
        present[np.ix_(drop, cols)] = False
    r2 = which == 1
    for g in CAN_GROUPS:
        present[np.ix_(r2, GROUP_POS[g])] = False
    gs = list(DROP_POS)
    for b in np.where(which == 2)[0]:
        for g in rng.choice(gs, size=rng.integers(1, len(gs)), replace=False):
            present[b, DROP_POS[g]] = False
    cell = rng.random(present.shape) < FO["cell_dropout"]; cell[:, NEVER_POS] = False
    present &= ~cell
    return present


# ----------------------------------------------------------------------------- FT
def make_torch():
    import torch, torch.nn as nn

    class FT(nn.Module):
        def __init__(self, nf, d=32, layers=2, heads=4):
            super().__init__()
            self.w = nn.Parameter(torch.randn(nf, d) * 0.1); self.b = nn.Parameter(torch.randn(nf, d) * 0.1)
            self.miss = nn.Parameter(torch.randn(nf, d) * 0.1); self.cls = nn.Parameter(torch.zeros(1, 1, d))
            enc = nn.TransformerEncoderLayer(d, heads, dim_feedforward=2 * d, dropout=0.1, batch_first=True)
            self.enc = nn.TransformerEncoder(enc, layers)
            self.recon = nn.Linear(d, 1); self.head = nn.Linear(d, 1)

        def forward(self, x, present):
            val = x.unsqueeze(-1) * self.w + self.b
            tok = torch.where(present.unsqueeze(-1), val, self.miss.expand_as(val))
            h = self.enc(torch.cat([self.cls.expand(x.shape[0], -1, -1), tok], 1))
            return h[:, 0], h[:, 1:]
    return torch, FT


def standardize(X, mu, sd):
    return np.clip(np.nan_to_num((X - mu) / sd, nan=0.0), -5, 5).astype(np.float32)


def recon_loss(torch, model, Z, P, rng, n=2000):
    """몸통의 복원 손실 (가린 30%). 라벨 없이 '이 데이터에 맞는가'를 보는 숫자."""
    idx = rng.choice(len(Z), size=min(n, len(Z)), replace=False)
    present = P[idx]; target = present & (rng.random(present.shape) < 0.3)
    model.eval()
    with torch.no_grad():
        x = torch.from_numpy(Z[idx]); _, h = model(x, torch.from_numpy(present & ~target))
        return float(((model.recon(h).squeeze(-1) - x) ** 2)[torch.from_numpy(target)].mean())


def pretrain(torch, FT, Z, P0, mode, steps, batch, rng, log, Z4=None, P4=None):
    model = FT(NF); opt = torch.optim.AdamW(model.parameters(), lr=1e-3, weight_decay=1e-4)
    curve = []; t0 = time.time()
    for s in range(steps):
        model.train()
        idx = rng.integers(0, len(Z), size=batch)
        present = apply_dropout(P0[idx], rng, mode)
        target = present & (rng.random(present.shape) < 0.3)
        x = torch.from_numpy(Z[idx]); _, h = model(x, torch.from_numpy(present & ~target))
        loss = ((model.recon(h).squeeze(-1) - x) ** 2)[torch.from_numpy(target)].mean()
        opt.zero_grad(); loss.backward(); opt.step()
        if (s + 1) % 250 == 0:
            r4 = recon_loss(torch, model, Z4, P4, np.random.default_rng(1)) if Z4 is not None else float("nan")
            curve.append((s + 1, float(loss.item()), r4))
            if (s + 1) % 1000 == 0:
                log(f"    [{mode}] {s + 1}/{steps} 시뮬 복원 {loss.item():.3f} · 4TU 복원 {r4:.3f} ({time.time() - t0:.0f}s)")
    return model, curve


def finetune(torch, FT, init_state, Ztr, Ptr, ytr, steps, seed, probe):
    """→ predict(Z, P). probe=True 면 몸통 동결, 머리만."""
    torch.manual_seed(seed); model = FT(NF)
    if init_state is not None:
        model.load_state_dict(init_state)
    if probe:
        for n_, p_ in model.named_parameters():
            p_.requires_grad = n_.startswith("head")
        opt = torch.optim.Adam(model.head.parameters(), lr=1e-2)
    else:
        opt = torch.optim.AdamW(model.parameters(), lr=3e-4, weight_decay=1e-4)
    rng = np.random.default_rng(seed)
    x_all, p_all, y_all = torch.from_numpy(Ztr), torch.from_numpy(Ptr), torch.from_numpy(ytr.astype(np.float32))
    pos = float(ytr.mean()); lossf = torch.nn.BCEWithLogitsLoss(pos_weight=torch.tensor([(1 - pos) / max(pos, 1e-3)]))
    model.train() if not probe else model.eval()
    for _ in range(steps):
        idx = rng.integers(0, len(Ztr), size=min(128, len(Ztr)))
        cls, _ = model(x_all[idx], p_all[idx])
        loss = lossf(model.head(cls).squeeze(-1), y_all[idx])
        opt.zero_grad(); loss.backward(); opt.step()
    model.eval()

    def predict(Z, P):
        with torch.no_grad():
            cls, _ = model(torch.from_numpy(Z), torch.from_numpy(P))
            return torch.sigmoid(model.head(cls).squeeze(-1)).numpy()
    return predict


# ----------------------------------------------------------------------------- GBM
def group_dropout74(X, ride_id, rng):
    """repo common.group_dropout 과 같은 규칙을 74열(enabled) 기준으로. 탑승 단위 그룹 드롭 + 칸 2%."""
    X = X.copy()
    rides, inv = np.unique(ride_id, return_inverse=True)
    for g, cols in DROP_POS.items():
        drop = rng.random(len(rides)) < FO["groups"][g]["dropout"]
        X[np.ix_(drop[inv], cols)] = np.nan
    cell_cols = [i for i in range(X.shape[1]) if i not in NEVER_POS]
    mask = rng.random((len(X), len(cell_cols))) < FO["cell_dropout"]
    sub = X[:, cell_cols]; sub[mask] = np.nan; X[:, cell_cols] = sub
    return X


def gbm_pretrain(X, y, ride_id, rng):
    import lightgbm as lgb
    Xd = group_dropout74(X, ride_id, rng)
    params = dict(C.PARAMS, num_threads=4, monotone_constraints=MONO)
    return lgb.train(params, lgb.Dataset(np.vstack([X, Xd]), np.concatenate([y, y])), num_boost_round=300)


def gbm_fit(Xtr, ytr, seed, base=None):
    import lightgbm as lgb
    params = dict(C.PARAMS, seed=seed, num_threads=4, monotone_constraints=MONO)
    params["min_data_in_leaf"] = int(min(100, max(5, len(Xtr) // 20)))
    if base is None:
        return lgb.train(params, lgb.Dataset(Xtr, ytr), num_boost_round=200)
    params["learning_rate"] = 0.02
    return lgb.train(params, lgb.Dataset(Xtr, ytr), num_boost_round=30, init_model=base, keep_training_booster=True)


# ----------------------------------------------------------------------------- 실행
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mat"); ap.add_argument("--dataset", required=True)
    ap.add_argument("--rows", type=int, default=300_000); ap.add_argument("--steps", type=int, default=10_000)
    ap.add_argument("--ft-steps", type=int, default=200); ap.add_argument("--probe-steps", type=int, default=200)
    ap.add_argument("--ks", default="3,4"); ap.add_argument("--seeds", type=int, default=2)
    ap.add_argument("--bodies", default="all,table")
    ap.add_argument("--reuse-bodies", action="store_true", help="<out>/body_<mode>.pt 가 있으면 사전학습을 건너뛰고 불러온다")
    ap.add_argument("--out", default=str(FC.out_dir("ft_transfer_v2")))
    a = ap.parse_args()
    ks = [int(k) for k in a.ks.split(",")]; modes = a.bodies.split(",")
    out = pathlib.Path(a.out); out.mkdir(parents=True, exist_ok=True)
    logf = open(out / "log.txt", "w")
    def log(s): print(s); logf.write(s + "\n"); logf.flush()
    torch, FT = make_torch(); rng = np.random.default_rng(0); T0 = time.time()
    from sklearn.metrics import average_precision_score, roc_auc_score
    from sklearn.model_selection import GroupKFold

    Xsim, Ysim, ride_sim = load_sim(a.dataset, a.rows, rng, ks)
    mu = np.nan_to_num(np.nanmean(Xsim, 0)); sd = np.nanstd(Xsim, 0); sd[~np.isfinite(sd) | (sd < 1e-6)] = 1.0
    Zs, Ps = standardize(Xsim, mu, sd), np.isfinite(Xsim)
    log(f"시뮬레이터 {len(Xsim)}행 × {NF}토큰")
    cache = out.parent / "ft_transfer" / "tudelft_rows.npz"
    X4, fut, pid = build_tudelft(a.mat, cache if cache.exists() else out / "tudelft_rows.npz")
    Z4, P4 = standardize(X4, mu, sd), np.isfinite(X4)
    log(f"4TU {len(X4)}행, {len(np.unique(pid))}명. 양성 비율 " + ", ".join(f"k={k}: {(fut >= k).mean():.2f}" for k in ks))

    # ---- 사전학습
    bodies, curves, recon4 = {}, {}, {}
    saved_curves = json.load(open(out / "pretrain_curves.json")) if (out / "pretrain_curves.json").exists() else {"curves": {}}
    for mode in modes:
        if a.reuse_bodies and (out / f"body_{mode}.pt").exists():
            log(f"FT 몸통 [{mode}] 저장본 재사용")
            bodies[mode] = torch.load(out / f"body_{mode}.pt"); m = FT(NF); m.load_state_dict(bodies[mode])
            curves[mode] = saved_curves["curves"].get(mode, [])
        else:
            log(f"FT 사전학습 [{mode}] {a.steps}스텝…")
            m, curve = pretrain(torch, FT, Zs, Ps, mode, a.steps, 256, rng, log, Z4, P4)
            bodies[mode] = {k_: v.clone() for k_, v in m.state_dict().items()}; curves[mode] = curve
            torch.save(bodies[mode], out / f"body_{mode}.pt")
            json.dump({"curves": curves}, open(out / "pretrain_curves.json", "w"))
        recon4[mode] = recon_loss(torch, m, Z4, P4, np.random.default_rng(1))
    log("GBM 사전학습 (합성 Oman 라벨)…")
    gbm_base = {k: gbm_pretrain(Xsim, Ysim[k], ride_sim, rng) for k in ks}
    log(f"  사전학습 끝 ({time.time() - T0:.0f}s)")

    # ---- 4TU 미세조정
    body_name = {"all": "결측 전부", "table": "표 확률만"}
    rows = []
    def rec(fold, n, seed, k, cond, variant, yte, s):
        s = np.nan_to_num(s, nan=float(np.nanmin(s)) if np.isfinite(s).any() else 0.0)
        rows.append({"fold": fold, "n": n, "seed": seed, "k": k, "cond": cond, "variant": variant,
                     "pr_auc": float(average_precision_score(yte, s)), "roc_auc": float(roc_auc_score(yte, s))})
    VARIANTS = ["원본", "G2 제거", "프로필 기본값"]
    for k in ks:
        y4 = (fut >= k).astype(int)
        for fold, (tr_idx, te) in enumerate(GroupKFold(5).split(X4, y4, pid)):
            tr_p = np.unique(pid[tr_idx]); yte = y4[te]
            if yte.min() == yte.max():
                continue
            Xte_v = {v: degrade(X4[te], v) for v in VARIANTS}
            Zte_v = {v: (standardize(Xte_v[v], mu, sd), np.isfinite(Xte_v[v])) for v in VARIANTS}
            for n in [1, 2, 4, 99]:
                n_eff = len(tr_p) if n == 99 else n
                for seed in ([0] if n == 99 else list(range(a.seeds))):
                    srng = np.random.default_rng([k, fold, n, seed])
                    for _ in range(30):
                        chosen = srng.choice(tr_p, size=n_eff, replace=False); tr = np.where(np.isin(pid, chosen))[0]
                        if y4[tr].min() != y4[tr].max():
                            break
                    variants = VARIANTS if n == 99 else ["원본"]
                    models = {
                        "Oman 규칙": lambda X, Z, P: X[:, OMAN_POS],
                        "GBM": (lambda m: lambda X, Z, P: m.predict(X))(gbm_fit(X4[tr], y4[tr], seed)),
                        "GBM 사전학습(그대로)": lambda X, Z, P, k=k: gbm_base[k].predict(X),
                        "GBM 사전학습+이어학습": (lambda m: lambda X, Z, P: m.predict(X))(gbm_fit(X4[tr], y4[tr], seed, base=gbm_base[k])),
                        "FT 처음부터": (lambda f: lambda X, Z, P: f(Z, P))(finetune(torch, FT, None, Z4[tr], P4[tr], y4[tr], a.ft_steps, seed, False)),
                    }
                    for mode in modes:
                        models[f"FT {body_name[mode]} · 탐침"] = (lambda f: lambda X, Z, P: f(Z, P))(
                            finetune(torch, FT, bodies[mode], Z4[tr], P4[tr], y4[tr], a.probe_steps, seed, True))
                        models[f"FT {body_name[mode]} · 전체"] = (lambda f: lambda X, Z, P: f(Z, P))(
                            finetune(torch, FT, bodies[mode], Z4[tr], P4[tr], y4[tr], a.ft_steps, seed, False))
                    for cond, fn in models.items():
                        for v in variants:
                            rec(fold, n, seed, k, cond, v, yte, fn(Xte_v[v], *Zte_v[v]))
                log(f"  k={k} fold {fold} n={'전부' if n == 99 else n} 끝 ({time.time() - T0:.0f}s)")

    # ---- 정리
    with open(out / "results.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    conds = list(dict.fromkeys(r["cond"] for r in rows)); ns = [1, 2, 4, 99]; nl = lambda n: "전부" if n == 99 else str(n)
    def agg(k, cond, n, v="원본", key="pr_auc"):
        x = np.array([r[key] for r in rows if r["k"] == k and r["cond"] == cond and r["n"] == n and r["variant"] == v])
        return (x.mean(), x.std()) if len(x) else (float("nan"), float("nan"))
    L = ["# FT 전이 실험 v2 — 4TU", "",
         f"FT 사전학습: 시뮬레이터 {len(Xsim)}행 × {a.steps}스텝, 라벨 없음. 몸통: " + ", ".join(body_name[m] for m in modes) + ".",
         f"GBM 사전학습: 같은 행 + 합성 Oman 라벨, 300트리 (→ 이어학습 30트리). FT 전체 미세조정 {a.ft_steps}스텝, 탐침 {a.probe_steps}스텝.",
         f"사람 5-fold, n<전부는 시드 {a.seeds}. 값은 PR-AUC 평균 ± 표준편차 (괄호 ROC-AUC).", "",
         "## 미세조정 전, 몸통의 4TU 복원 손실 (낮을수록 4TU에 맞음, 라벨 없이 잼)", ""]
    L += [f"- {body_name[m]}: 4TU {recon4[m]:.3f}" + (f" / 시뮬레이터 {curves[m][-1][1]:.3f}" if curves[m] else "") for m in modes]
    for k in ks:
        L += ["", f"## k = {k}  (양성 비율 {(fut >= k).mean():.2f})", "",
              "| 조건 | " + " | ".join(f"n={nl(n)}" for n in ns) + " |", "|---|" + "---|" * len(ns)]
        for cond in conds:
            cells = []
            for n in ns:
                m_, s_ = agg(k, cond, n); r_, _ = agg(k, cond, n, key="roc_auc")
                cells.append(f"{m_:.3f} ± {s_:.3f} ({r_:.2f})")
            L.append(f"| {cond} | " + " | ".join(cells) + " |")
        L += ["", f"### 열화 검사 (k={k}, n=전부, 재학습 없이 입력만 바꿈, PR-AUC)", "",
              "| 조건 | " + " | ".join(VARIANTS) + " |", "|---|---|---|---|"]
        for cond in conds:
            L.append(f"| {cond} | " + " | ".join(f"{agg(k, cond, 99, v)[0]:.3f}" for v in VARIANTS) + " |")
    L += ["", "## 읽는 법",
          "- '바로 되나': n=1·2에서 'FT 결측 전부 · 탐침'이 'Oman 규칙'과 'GBM 사전학습+이어학습'보다 위인가.",
          "- '결측 전부 효과': 같은 n에서 결측 전부 vs 표 확률만. 열화 검사에서 표 확률만 쪽이 더 크게 떨어지면 효과 있음.",
          "- '라벨 없는 사전학습 vs 라벨 있는 사전학습': FT 결측 전부 vs GBM 사전학습(+이어학습).",
          "- '충분히': recon_curve.png 가 평평해졌나. 4TU 복원 손실이 시뮬레이터보다 많이 높으면 분포 차이가 큼.",
          f"", f"총 {time.time() - T0:.0f}초"]
    (out / "summary.md").write_text("\n".join(L), encoding="utf-8"); log("\n".join(L))

    import logging, matplotlib; matplotlib.use("Agg"); logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)
    import matplotlib.pyplot as plt
    plt.rcParams["font.family"] = ["NanumGothic", "Noto Sans CJK KR", "DejaVu Sans"]
    for k in ks:
        fig, ax = plt.subplots(figsize=(7, 4.5))
        for cond in conds:
            m_ = [agg(k, cond, n)[0] for n in ns]; e_ = [agg(k, cond, n)[1] for n in ns]
            ax.errorbar(range(len(ns)), m_, yerr=e_, marker="o", capsize=3, label=cond, lw=1.2)
        ax.set_xticks(range(len(ns))); ax.set_xticklabels([nl(n) for n in ns])
        ax.set_xlabel("4TU 학습 사람 수"); ax.set_ylabel(f"PR-AUC (k={k})"); ax.grid(alpha=.3); ax.legend(fontsize=7)
        fig.tight_layout(); fig.savefig(out / f"fig_k{k}.png", dpi=150); plt.close(fig)
    fig, ax = plt.subplots(figsize=(6, 4))
    for mode in [m_ for m_ in modes if curves[m_]]:
        c = np.array(curves[mode]); ax.plot(c[:, 0], c[:, 1], label=f"{body_name[mode]} · 시뮬"); ax.plot(c[:, 0], c[:, 2], "--", label=f"{body_name[mode]} · 4TU")
    ax.set_xlabel("사전학습 스텝"); ax.set_ylabel("복원 손실 (MSE)"); ax.grid(alpha=.3); ax.legend(fontsize=8)
    fig.tight_layout(); fig.savefig(out / "recon_curve.png", dpi=150)
    json.dump({"curves": curves, "recon4": recon4}, open(out / "pretrain_curves.json", "w"))
    log(f"저장: {out}")


if __name__ == "__main__":
    main()
