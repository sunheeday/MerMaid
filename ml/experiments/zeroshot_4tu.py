"""합성(라벨 있음)으로만 학습 → 4TU(MISC≥4) 라벨 0개로 바로 채점. Oman 규칙 / GBM 합성 베이스 / FT 합성 베이스 (약 15분)

    python3 ml/experiments/zeroshot_4tu.py ml/data/raw/ATDOMS.mat --dataset ml/out/runs/v002/dataset.npz

FT 합성 베이스 = 합성 X(74) + 합성 라벨(다음 5분 최대 MISC≥4)로 지도학습한 FT-Transformer. GBM 베이스와 같은 데이터·같은 라벨.
두 변형: 드롭아웃 없이 / 학습 중 그룹 드롭아웃(CAN 전부 없음 패턴 포함). 시드 2.
채점: 4TU 17명 전체, 라벨 0개. ROC(전체 합침)와 ROC(사람별 평균) 둘 다 — 실험 1의 숫자는 사람별 평균 쪽과 비교.
"""
import argparse, pathlib, sys, tempfile, time, json
import numpy as np

ML = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ML)); sys.path.insert(0, str(ML / "train")); sys.path.insert(0, str(ML / "verify"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import fo_compat as FC  # noqa: E402  (피처 목록 v3 77칸 / v5 52칸 모두)
import common as C, oman, trip as tripio, tudelft_replay as TR
from sklearn.metrics import roc_auc_score, average_precision_score
import lightgbm as lgb
import torch, torch.nn as nn


FO = C.FO; ENABLED = [i for i, f in enumerate(C.FEATS) if f["enabled"]]; POS = {i: j for j, i in enumerate(ENABLED)}; NF = len(ENABLED)
GROUP_POS = {g: [POS[i] for i in idx if i in POS] for g, idx in C.GROUPS.items()}
DROP_POS = {g: [POS[i] for i in idx if i in POS] for g, idx in C.DROPPABLE.items()}
NEVER_POS = sorted({POS[i] for i in C.NEVER if i in POS}); MONO = [C.MONOTONE[i] for i in ENABLED]
OMAN_POS = POS[C.NAMES.index("oman_score")]; CAN = FC.present(["G2", "G3a", "G3b", "G4", "G5", "G6", "G7"])
K = 4; OUT = FC.out_dir("zeroshot_4tu"); OUT.mkdir(parents=True, exist_ok=True)

def build_tudelft(mat, cache):
    if cache.exists():
        z = np.load(cache); return z["X"], z["fut"], z["pid"]
    import scipy.io as sio
    A = sio.loadmat(mat, squeeze_me=True, struct_as_record=False)["ATDOMS"]; persons = list(A._fieldnames); v0 = 50 / 3.6
    Xs, futs, pids = [], [], []
    with tempfile.TemporaryDirectory() as td:
        for pi, p in enumerate(persons):
            mssq = float(getattr(A, p).MSSQ.Score)
            for c, amp in TR.AMPS.items():
                seg, tr, yr = TR.session(getattr(getattr(A, p), c)); n = int((tr[-1] + 10) * TR.FS); acc = TR.stimulus(seg, n, amp)
                rng = np.random.default_rng([0, pi, int(c[1])]); path = pathlib.Path(td) / "trip.bin"
                tripio.write_trip(path, TR.make_trip(acc, v0, rng)); t, Xv = tripio.featurize(path, pathlib.Path(td) / "features.bin")
                X = FC.full_row(Xv, mssq, float(oman.sensitivity(mssq)))
                fut = np.array([np.nanmax(w) if tt + 300 <= tr[-1] and np.isfinite(w := yr[(tr > tt) & (tr <= tt + 300)]).any() else np.nan for tt in t])
                v = np.isfinite(fut); Xs.append(X[v][:, ENABLED]); futs.append(fut[v]); pids.append(np.full(int(v.sum()), pi))
    X, fut, pid = np.concatenate(Xs).astype(np.float32), np.concatenate(futs), np.concatenate(pids)
    np.savez_compressed(cache, X=X, fut=fut, pid=pid); return X, fut, pid

def group_dropout74(X, ride_id, rng):
    X = X.copy(); rides, inv = np.unique(ride_id, return_inverse=True)
    for g, cols in DROP_POS.items():
        drop = rng.random(len(rides)) < FO["groups"][g]["dropout"]; X[np.ix_(drop[inv], cols)] = np.nan
    cols = [i for i in range(X.shape[1]) if i not in NEVER_POS]; m = rng.random((len(X), len(cols))) < FO["cell_dropout"]
    sub = X[:, cols]; sub[m] = np.nan; X[:, cols] = sub; return X

def apply_dropout(present, rng):
    B = present.shape[0]; present = present.copy(); which = rng.integers(0, 3, size=B)
    r1 = which == 0
    for g, cols in DROP_POS.items():
        drop = r1 & (rng.random(B) < FO["groups"][g]["dropout"]); present[np.ix_(drop, cols)] = False
    r2 = which == 1
    for g in CAN: present[np.ix_(r2, GROUP_POS[g])] = False
    gs = list(DROP_POS)
    for b in np.where(which == 2)[0]:
        for g in rng.choice(gs, size=rng.integers(1, len(gs)), replace=False): present[b, DROP_POS[g]] = False
    cell = rng.random(present.shape) < FO["cell_dropout"]; cell[:, NEVER_POS] = False; return present & ~cell

class FT(nn.Module):
    def __init__(self, nf, d=32, layers=2, heads=4):
        super().__init__(); self.w = nn.Parameter(torch.randn(nf, d) * .1); self.b = nn.Parameter(torch.randn(nf, d) * .1)
        self.miss = nn.Parameter(torch.randn(nf, d) * .1); self.cls = nn.Parameter(torch.zeros(1, 1, d))
        self.enc = nn.TransformerEncoder(nn.TransformerEncoderLayer(d, heads, dim_feedforward=2 * d, dropout=.1, batch_first=True), layers)
        self.head = nn.Linear(d, 1)
    def forward(self, x, present):
        val = x.unsqueeze(-1) * self.w + self.b; tok = torch.where(present.unsqueeze(-1), val, self.miss.expand_as(val))
        return self.enc(torch.cat([self.cls.expand(x.shape[0], -1, -1), tok], 1))[:, 0]

def standardize(X, mu, sd): return np.clip(np.nan_to_num((X - mu) / sd, nan=0.0), -5, 5).astype(np.float32)

def eval_all(name, s, y, pid, res):
    s = np.nan_to_num(s, nan=float(np.nanmin(s)))
    per = [roc_auc_score(y[pid == p], s[pid == p]) for p in np.unique(pid) if 0 < y[pid == p].sum() < (pid == p).sum()]
    res[name] = {"roc_pooled": float(roc_auc_score(y, s)), "pr_pooled": float(average_precision_score(y, s)),
                 "roc_person_mean": float(np.mean(per)), "roc_person_sd": float(np.std(per))}
    print(f"{name:28s} ROC(pooled) {res[name]['roc_pooled']:.3f}  PR {res[name]['pr_pooled']:.3f}  ROC(사람평균) {res[name]['roc_person_mean']:.3f} ± {res[name]['roc_person_sd']:.3f}", flush=True)

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("mat"); ap.add_argument("--dataset", required=True)
    ap.add_argument("--rows", type=int, default=300_000); ap.add_argument("--steps", type=int, default=3000); ap.add_argument("--seeds", type=int, default=2)
    a = ap.parse_args(); t0 = time.time(); rng = np.random.default_rng(0)
    d = np.load(a.dataset); idx = rng.choice(len(d["X"]), size=min(a.rows, len(d["X"])), replace=False)
    X = d["X"][idx][:, ENABLED].astype(np.float32); y = d["y"][idx, list(d["k"]).index(K)].astype(int); rid = d["ride_id"][idx]
    cache = FC.out_dir("ft_transfer", "tudelft_rows.npz")
    X4, fut, pid = build_tudelft(a.mat, cache if cache.exists() else OUT / "tudelft_rows.npz"); y4 = (fut >= K).astype(int)
    print(f"합성 {len(X)}행 (양성 {y.mean():.3f}) · 4TU {len(X4)}행 17명 (양성 {y4.mean():.3f})", flush=True)
    res = {}
    eval_all("Oman 규칙", X4[:, OMAN_POS], y4, pid, res)
    # GBM 베이스 (repo 방식)
    Xd = group_dropout74(X, rid, rng); params = dict(C.PARAMS, num_threads=2, monotone_constraints=MONO)
    gb = lgb.train(params, lgb.Dataset(np.vstack([X, Xd]), np.concatenate([y, y])), num_boost_round=300)
    eval_all("GBM 합성 베이스", gb.predict(X4), y4, pid, res); print(f"  ({time.time() - t0:.0f}s)", flush=True)
    # FT 지도학습 베이스
    mu = np.nan_to_num(np.nanmean(X, 0)); sd = np.nanstd(X, 0); sd[~np.isfinite(sd) | (sd < 1e-6)] = 1
    Z, P = standardize(X, mu, sd), np.isfinite(X); Z4, P4 = standardize(X4, mu, sd), np.isfinite(X4)
    pos = y.mean(); pw = torch.tensor([(1 - pos) / pos])
    for aug in (False, True):
        for seed in range(a.seeds):
            torch.manual_seed(seed); r = np.random.default_rng(seed); m = FT(NF); opt = torch.optim.AdamW(m.parameters(), lr=1e-3, weight_decay=1e-4)
            lossf = nn.BCEWithLogitsLoss(pos_weight=pw); m.train()
            for s in range(a.steps):
                idx = r.integers(0, len(Z), 256); pres = apply_dropout(P[idx], r) if aug else P[idx]
                loss = lossf(m.head(m(torch.from_numpy(Z[idx]), torch.from_numpy(pres))).squeeze(-1), torch.from_numpy(y[idx].astype(np.float32)))
                opt.zero_grad(); loss.backward(); opt.step()
            m.eval()
            with torch.no_grad(): s4 = torch.sigmoid(m.head(m(torch.from_numpy(Z4), torch.from_numpy(P4))).squeeze(-1)).numpy()
            eval_all(f"FT 합성 베이스{' +드롭아웃' if aug else ''} s{seed}", s4, y4, pid, res); print(f"  ({time.time() - t0:.0f}s)", flush=True)
    json.dump(res, open(OUT / "results.json", "w"), ensure_ascii=False, indent=1)
    L = ["# 합성 베이스 → 4TU 라벨 0개 채점 (MISC≥4)", "", "| 모델 | ROC(전체) | PR(전체) | ROC(사람별 평균) |", "|---|---|---|---|"]
    L += [f"| {k} | {v['roc_pooled']:.3f} | {v['pr_pooled']:.3f} | {v['roc_person_mean']:.3f} ± {v['roc_person_sd']:.3f} |" for k, v in res.items()]
    (OUT / "summary.md").write_text("\n".join(L), encoding="utf-8"); print("\n".join(L))

if __name__ == "__main__":
    main()
