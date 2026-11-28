"""빈 라벨 로그를 먹는 FT가, 라벨이 조금 생겼을 때 GBM보다 더 뛰나 — 4TU만으로 (합성 없음, 약 40분)

    python3 ml/experiments/unlabeled_logs_4tu.py --mat ml/data/raw/ATDOMS.mat

설정 (사람 17명을 둘로 가른다, 세 번 돌려가며)
  베이스 사람 7명 : MISC≥4 라벨로 베이스를 만든다. GBM·FT 똑같이 지도학습. "미리 학습된 모델"이 출발선.
  로그 사람 10명  : 운행 로그. 입력은 40세션 전부, 멈춤 라벨은 m세션만 (m ∈ 2, 4, 8, 전부).
     GBM : 베이스 → m세션 멈춤 라벨로 이어학습
     FT  : 베이스 → 로그 40세션 입력 전부로 마스크 학습(라벨 없음, 베이스 지도손실 조금 섞어 머리 유지) → m세션 멈춤 라벨로 미세조정
     평가: 로그 사람 중 라벨에 안 쓴 사람 (사람 5-fold, 시험 2명씩)
  두 계보의 차이는 가운데 한 단계뿐: 빈 라벨 로그를 먹느냐.

기준선(학습 없음): Oman 규칙, GBM 베이스 그대로, FT 베이스+빈라벨 그대로.
--ablation 을 주면 "FT, 빈 라벨 단계 없음"을 추가한다 (FT가 이겼을 때 원인 가르기용).

입력: ml/out/stop_transfer/tudelft_stop_rows.npz (stop_transfer_4tu.py 가 만든 캐시). 없으면 --mat 으로 다시 만든다.
출력: ml/out/unlabeled_logs/summary.md, results.csv, fig.png
"""
import argparse
import csv
import pathlib
import sys
import time

import numpy as np

ML = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ML)); sys.path.insert(0, str(ML / "train")); sys.path.insert(0, str(ML / "experiments"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import fo_compat as FC  # noqa: E402  (피처 목록 v3 77칸 / v5 52칸 모두)
import common as C  # noqa: E402

FO = C.FO
ENABLED = [i for i, f in enumerate(C.FEATS) if f["enabled"]]
POS = {i: j for j, i in enumerate(ENABLED)}
NF = len(ENABLED)
OMAN_POS = POS[C.NAMES.index("oman_score")]
MONO = [C.MONOTONE[i] for i in ENABLED]
N_BASE = 7


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


class FTRunner:
    def __init__(self, torch, FT, seed):
        self.torch, self.FT = torch, FT
        torch.manual_seed(seed); self.model = FT(NF); self.rng = np.random.default_rng(seed)

    def _bce(self, y):
        pos = float(y.mean()); return self.torch.nn.BCEWithLogitsLoss(pos_weight=self.torch.tensor([(1 - pos) / max(pos, 1e-3)]))

    def _sup(self, Z, P, y, idx, lossf):
        cls, _ = self.model(self.torch.from_numpy(Z[idx]), self.torch.from_numpy(P[idx]))
        return lossf(self.model.head(cls).squeeze(-1), self.torch.from_numpy(y[idx].astype(np.float32)))

    def _mask(self, Z, P, idx):
        present = P[idx]; target = present & (self.rng.random(present.shape) < 0.3)
        x = self.torch.from_numpy(Z[idx]); _, h = self.model(x, self.torch.from_numpy(present & ~target))
        return ((self.model.recon(h).squeeze(-1) - x) ** 2)[self.torch.from_numpy(target)].mean()

    def base(self, Z, P, y, steps, lr=1e-3):
        """베이스: 베이스 사람의 MISC≥4 라벨로 지도학습."""
        opt = self.torch.optim.AdamW(self.model.parameters(), lr=lr, weight_decay=1e-4); lossf = self._bce(y)
        self.model.train()
        for _ in range(steps):
            loss = self._sup(Z, P, y, self.rng.integers(0, len(Z), 256), lossf)
            opt.zero_grad(); loss.backward(); opt.step()
        return self

    def unlabeled(self, Zlog, Plog, Zb, Pb, yb, steps, replay=0.3, lr=5e-4):
        """빈 라벨 단계: 로그 입력 전부로 마스크 학습 + 베이스 지도손실을 replay 비율로 섞어 머리를 유지."""
        opt = self.torch.optim.AdamW(self.model.parameters(), lr=lr, weight_decay=1e-4); lossf = self._bce(yb)
        self.model.train()
        for _ in range(steps):
            loss = self._mask(Zlog, Plog, self.rng.integers(0, len(Zlog), 256))
            loss = loss + replay * self._sup(Zb, Pb, yb, self.rng.integers(0, len(Zb), 128), lossf)
            opt.zero_grad(); loss.backward(); opt.step()
        return self

    def finetune(self, Z, P, y, steps, lr=3e-4):
        import copy
        m = copy.deepcopy(self.model); opt = self.torch.optim.AdamW(m.parameters(), lr=lr, weight_decay=1e-4); lossf = self._bce(y)
        rng = np.random.default_rng(0); m.train()
        for _ in range(steps):
            idx = rng.integers(0, len(Z), min(128, len(Z)))
            cls, _ = m(self.torch.from_numpy(Z[idx]), self.torch.from_numpy(P[idx]))
            loss = lossf(m.head(cls).squeeze(-1), self.torch.from_numpy(y[idx].astype(np.float32)))
            opt.zero_grad(); loss.backward(); opt.step()
        m.eval(); return self._pred_fn(m)

    def predict(self):
        self.model.eval(); return self._pred_fn(self.model)

    def _pred_fn(self, m):
        def f(Z, P):
            with self.torch.no_grad():
                cls, _ = m(self.torch.from_numpy(Z), self.torch.from_numpy(P))
                return self.torch.sigmoid(m.head(cls).squeeze(-1)).numpy()
        return f

    def state(self):
        return {k: v.clone() for k, v in self.model.state_dict().items()}

    def load(self, st):
        self.model.load_state_dict(st); return self


# ----------------------------------------------------------------------------- GBM
def gbm_train(X, y, seed, base=None, rounds=None):
    import lightgbm as lgb
    params = dict(C.PARAMS, seed=seed, num_threads=4, monotone_constraints=MONO)
    params["min_data_in_leaf"] = int(min(100, max(5, len(X) // 20)))
    if base is None:
        return lgb.train(params, lgb.Dataset(X, y), num_boost_round=rounds or 200)
    params["learning_rate"] = 0.02
    return lgb.train(params, lgb.Dataset(X, y), num_boost_round=rounds or 30, init_model=base, keep_training_booster=True)


# ----------------------------------------------------------------------------- 실행
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mat", help="캐시가 없을 때 ATDOMS.mat")
    ap.add_argument("--cache", default=str(FC.out_dir("stop_transfer", "tudelft_stop_rows.npz")))
    ap.add_argument("--rotations", type=int, default=3); ap.add_argument("--seeds", type=int, default=2)
    ap.add_argument("--base-steps", type=int, default=1500); ap.add_argument("--unlab-steps", type=int, default=2000)
    ap.add_argument("--ft-steps", type=int, default=200)
    ap.add_argument("--ablation", action="store_true")
    ap.add_argument("--out", default=str(FC.out_dir("unlabeled_logs")))
    a = ap.parse_args()
    out = pathlib.Path(a.out); out.mkdir(parents=True, exist_ok=True)
    logf = open(out / "log.txt", "w")
    def log(s): print(s); logf.write(s + "\n"); logf.flush()
    from sklearn.metrics import average_precision_score, roc_auc_score
    torch, FT = make_torch(); T0 = time.time()

    cache = pathlib.Path(a.cache)
    if cache.exists():
        z = np.load(cache); X, y_stop, y_m4, pid, sess = z["X"], z["y_stop"], z["y_misc4"], z["pid"], z["sess"]
    else:
        assert a.mat, "캐시가 없으면 --mat 필요"
        from stop_transfer_4tu import build_tudelft_stop
        X, y_stop, y_m4, pid, sess = build_tudelft_stop(a.mat, cache)
    persons = np.unique(pid)
    mu = np.nan_to_num(np.nanmean(X, 0)); sd = np.nanstd(X, 0); sd[~np.isfinite(sd) | (sd < 1e-6)] = 1.0   # 입력 전부(라벨 없음)의 통계
    Z, P = standardize(X, mu, sd), np.isfinite(X)
    log(f"4TU {len(X)}행, {len(persons)}명, 세션 {len(np.unique(sess))}개. 멈춤 양성 {y_stop.mean():.3f}, MISC≥4 양성 {y_m4.mean():.3f}")

    rows = []
    def rec(rot, fold, m, seed, cond, yte, s):
        s = np.nan_to_num(s, nan=0.0)
        rows.append({"rot": rot, "fold": fold, "m": m, "seed": seed, "cond": cond,
                     "pr_auc": float(average_precision_score(yte, s)), "roc_auc": float(roc_auc_score(yte, s))})
    MS = [2, 4, 8, 99]
    for rot in range(a.rotations):
        prng = np.random.default_rng(rot); perm = prng.permutation(persons)
        base_p, log_p = perm[:N_BASE], perm[N_BASE:]
        b = np.isin(pid, base_p); l = np.isin(pid, log_p)
        # --- 베이스 (MISC≥4, 베이스 사람)
        gbm_base = gbm_train(X[b], y_m4[b], rot)
        ft = FTRunner(torch, FT, rot).base(Z[b], P[b], y_m4[b], a.base_steps)
        ft_base_state = ft.state()
        # --- 빈 라벨 단계 (로그 사람 입력 전부, 시험 사람 포함 — 배포에서 모든 차의 입력이 쌓이는 것과 같음)
        ft.unlabeled(Z[l], P[l], Z[b], P[b], y_m4[b], a.unlab_steps)
        ft_unlab_state = ft.state()
        log(f"rot {rot}: 베이스 사람 {sorted(base_p)}, 베이스·빈라벨 단계 끝 ({time.time() - T0:.0f}s)")
        # --- 로그 사람 5-fold
        log_pids = np.array(sorted(log_p))
        for fold in range(5):
            te_p = log_pids[fold::5]; tr_p = np.setdiff1d(log_pids, te_p)
            te = np.isin(pid, te_p); yte = y_stop[te]
            if yte.sum() == 0 or yte.sum() == len(yte):
                continue
            tr_sess = np.unique(sess[np.isin(pid, tr_p)])
            for m in MS:
                m_eff = len(tr_sess) if m == 99 else m
                for seed in ([0] if m == 99 else list(range(a.seeds))):
                    srng = np.random.default_rng([rot, fold, m, seed])
                    for _ in range(50):
                        chosen = srng.choice(tr_sess, size=m_eff, replace=False); tr = np.isin(sess, chosen)
                        if y_stop[tr].sum() > 0 and y_stop[tr].sum() < tr.sum():
                            break
                    preds = {
                        "Oman 규칙": X[te, OMAN_POS],
                        "GBM 베이스 그대로": gbm_base.predict(X[te]),
                        "GBM 베이스 + 멈춤 이어학습": gbm_train(X[tr], y_stop[tr], seed, base=gbm_base).predict(X[te]),
                        "FT 베이스+빈라벨 그대로": FTRunner(torch, FT, seed).load(ft_unlab_state).predict()(Z[te], P[te]),
                        "FT 베이스+빈라벨 + 멈춤 미세조정": FTRunner(torch, FT, seed).load(ft_unlab_state).finetune(Z[tr], P[tr], y_stop[tr], a.ft_steps)(Z[te], P[te]),
                    }
                    if a.ablation:
                        preds["FT 베이스(빈라벨 없음) + 멈춤 미세조정"] = FTRunner(torch, FT, seed).load(ft_base_state).finetune(Z[tr], P[tr], y_stop[tr], a.ft_steps)(Z[te], P[te])
                    for cond, s in preds.items():
                        rec(rot, fold, m, seed, cond, yte, s)
            log(f"  rot {rot} fold {fold} 끝 ({time.time() - T0:.0f}s)")

    with open(out / "results.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    conds = list(dict.fromkeys(r["cond"] for r in rows)); ml_ = lambda m: "전부" if m == 99 else str(m)
    def agg(cond, m, key):
        x = np.array([r[key] for r in rows if r["cond"] == cond and r["m"] == m]); return (x.mean(), x.std()) if len(x) else (np.nan, np.nan)
    def wins(c1, c2, m, key="roc_auc"):
        d = {}
        for r in rows:
            if r["m"] == m and r["cond"] in (c1, c2):
                d.setdefault((r["rot"], r["fold"], r["seed"]), {})[r["cond"]] = r[key]
        pairs = [v for v in d.values() if len(v) == 2]
        return sum(v[c1] > v[c2] for v in pairs), len(pairs)
    L = ["# 빈 라벨 로그 실험 — 4TU만 (합성 없음)", "",
         f"베이스 사람 {N_BASE}명의 MISC≥4로 GBM·FT 베이스. 로그 사람 {len(persons) - N_BASE}명: FT만 입력 전부로 마스크 학습(라벨 없음). "
         f"멈춤 라벨은 m세션만. 분할 {a.rotations}회 × 사람 5-fold × 시드 {a.seeds}.", ""]
    for key, name in [("roc_auc", "ROC-AUC"), ("pr_auc", "PR-AUC")]:
        L += [f"## {name}", "", "| 조건 | " + " | ".join(f"m={ml_(m)}" for m in MS) + " |", "|---|" + "---|" * len(MS)]
        for cond in conds:
            L.append(f"| {cond} | " + " | ".join(f"{agg(cond, m, key)[0]:.3f} ± {agg(cond, m, key)[1]:.3f}" for m in MS) + " |")
        L.append("")
    c_ft, c_gbm = "FT 베이스+빈라벨 + 멈춤 미세조정", "GBM 베이스 + 멈춤 이어학습"
    L += ["## 쌍 비교 — 같은 분할·폴드·시드에서 FT가 GBM을 이긴 횟수 (ROC-AUC)", ""]
    L += [f"- m={ml_(m)}: {wins(c_ft, c_gbm, m)[0]} / {wins(c_ft, c_gbm, m)[1]}" for m in MS]
    if a.ablation:
        c_ab = "FT 베이스(빈라벨 없음) + 멈춤 미세조정"
        L += ["", "## 쌍 비교 — 빈 라벨 단계 있음 vs 없음 (FT끼리, ROC-AUC)", ""]
        L += [f"- m={ml_(m)}: {wins(c_ft, c_ab, m)[0]} / {wins(c_ft, c_ab, m)[1]}" for m in MS]
    L += ["", "## 읽는 법",
          "- m=2·4에서 FT가 GBM보다 위이고 쌍 비교도 절반을 넘으면: 빈 라벨 로그가 라벨을 보충한다.",
          "- 둘 다 Oman 규칙 아래면 그 m에서는 비교가 의미 없음(둘 다 못 배움).",
          "- --ablation 에서 '빈라벨 없음'과 차이가 없으면 FT의 이득은 빈 라벨이 아니라 다른 데서 온 것.",
          "- 로그 사람 10명·시험 2명씩이라 숫자가 크게 흔들린다. 방향과 쌍 비교로 읽는다.",
          f"", f"총 {time.time() - T0:.0f}초"]
    (out / "summary.md").write_text("\n".join(L), encoding="utf-8"); log("\n".join(L))

    import logging, matplotlib; matplotlib.use("Agg"); logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)
    import matplotlib.pyplot as plt
    plt.rcParams["font.family"] = ["NanumGothic", "Noto Sans CJK KR", "DejaVu Sans"]
    fig, ax = plt.subplots(figsize=(7, 4.5))
    for cond in conds:
        ax.errorbar(range(len(MS)), [agg(cond, m, "roc_auc")[0] for m in MS], yerr=[agg(cond, m, "roc_auc")[1] for m in MS],
                    marker="o", capsize=3, label=cond, lw=1.2)
    ax.set_xticks(range(len(MS))); ax.set_xticklabels([ml_(m) for m in MS]); ax.set_xlabel("멈춤 라벨이 있는 세션 수")
    ax.set_ylabel("ROC-AUC"); ax.grid(alpha=.3); ax.legend(fontsize=7); fig.tight_layout(); fig.savefig(out / "fig.png", dpi=150)
    log(f"저장: {out}")


if __name__ == "__main__":
    main()
