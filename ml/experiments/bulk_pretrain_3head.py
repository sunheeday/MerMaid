"""덩어리(빈 라벨 로그)를 먹은 FT가 같은 베이스의 GBM보다 더 뛰나 — 3헤드 반응 라벨, 두 세계 (노트북 ~1.5시간)

    python3 ml/experiments/make_log_world.py                       # 로그 세계 한 번 생성 (15~25분)
    python3 ml/experiments/bulk_pretrain_3head.py --base-dataset ml/out/runs/v002/dataset.npz \
                                                  --log-dataset ml/out/bulk_world/log_dataset.npz

세계 둘 : 베이스 세계(기본 시뮬레이터) 로 베이스를 굽고, 로그 세계(운전·센서·EV 비율·실내온도가 다른 시뮬레이터, 새 사람들)
          에서 덩어리·라벨·채점. 배포에서 "합성으로 배우고 실차에서 쓴다"는 상황.
사람    : person_id 로 같은 사람이 여러 탑승. 숨은 성질은 사람마다 하나 — θ_fan/θ_amb/θ_dots(보이는 민감도와 0.4쯤 상관 + 숨은 잡음),
          더위 가중 h, 기능 인지 aware_d. 게이트(화면·디스플레이)는 탑승마다. 전부 피처에 없음.
로그 세계 결측: 내비 80%·소음 60%·조작 50% 탑승 단위 결측 (베이스 세계 표와 다른 패턴).
라벨    : 헤드 d 사건 = aware_d 이고 자격 있을 때 진짜 MISC 가 처음 유효 문턱에 닿는 시각.
          Y_d = 사건 전 5분 1 / 자격 있는 나머지 0.  W_d = 자격 없음(게이트 닫힘, 사건 뒤) 0, 아니면 1.
출발점  : 셋 다 같은 합성 대리 라벨 3헤드 베이스 (fan = 최대 MISC≥3, amb·dots = ≥4, 원래형).
계보    : ① GBM 베이스 → 공개 사건  ② FT 베이스 → 공개 사건  ③ FT 베이스 → 덩어리 마스크 학습 → 공개 사건
축      : 덩어리 {1만, 전부} × 공개 사건 {헤드당 50, 전부}.  공개 사건은 세 계보에 동일.
출력    : ml/out/bulk_3head/summary.md, results.csv, fig.png
"""
import argparse
import csv
import pathlib
import sys
import time

import numpy as np

ML = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ML)); sys.path.insert(0, str(ML / "train"))
import common as C  # noqa: E402

FO = C.FO
ENABLED = [i for i, f in enumerate(C.FEATS) if f["enabled"]]
POS = {i: j for j, i in enumerate(ENABLED)}
NF = len(ENABLED)
DROP_POS = {g: [POS[i] for i in idx if i in POS] for g, idx in C.DROPPABLE.items()}
NEVER_POS = sorted({POS[i] for i in C.NEVER if i in POS})
MONO = [C.MONOTONE[i] for i in ENABLED]
OMAN_POS = POS[C.NAMES.index("oman_score")]
TEMP_POS = POS[C.NAMES.index("cabin_temp")]
HEADS = ["fan", "ambient", "dots"]
BASE_K = {"fan": 3, "ambient": 4, "dots": 4}
HORIZON = 300.0; DT = 10.0


# ----------------------------------------------------------------------------- 라벨
SENS_POS = POS[C.NAMES.index("sensitivity")]


def person_traits(person_id, sens_by_person, rng, theta_sd=(0.8, 1.4, 1.6)):
    """사람마다 하나. 문턱은 보이는 민감도와 반대로 움직이되(민감한 사람이 일찍 켬) 숨은 잡음이 더 크다.
    fan 문턱은 2.6 아래로 못 내려가게(더위만으로 켜지는 사람이 너무 많지 않게), 더위 가중은 작게."""
    tr = {}
    for p in np.unique(person_id):
        s_ = float(np.clip(sens_by_person[p], 0.5, 2.0)); shift = -0.8 * np.log(s_)        # sens 2.0 → -0.55, 0.5 → +0.55
        tr[p] = {"fan": max(2.6, 3.4 + shift + rng.normal(0, theta_sd[0])),
                 "ambient": max(2.5, 4.3 + shift + rng.normal(0, theta_sd[1])),
                 "dots": max(2.5, 4.6 + shift + rng.normal(0, theta_sd[2])), "h": rng.uniform(0, 0.15),
                 "aware": {d: rng.random() < 0.65 for d in HEADS}}
    return tr


def make_response_labels(X, misc, t, ride_id, person_id, rng, theta_sd=(0.8, 1.4, 1.6), mood_sd=0.5):
    """→ Y (n,3), W (n,3), 사람별 숨은 성질 dict."""
    n = len(t); Y = np.zeros((n, 3), int); W = np.ones((n, 3), np.float32)
    rides = np.unique(ride_id); temp = X[:, TEMP_POS]
    sens_by_person = {p: float(np.nanmedian(X[person_id == p, SENS_POS])) for p in np.unique(person_id)}
    traits = person_traits(person_id, sens_by_person, rng, theta_sd)
    for r in rides:
        idx = np.flatnonzero(ride_id == r); o = np.argsort(t[idx]); idx = idx[o]
        tt, mm = t[idx], misc[idx]; m = len(idx)
        pt = traits[int(person_id[idx[0]])]
        mood = rng.normal(0, mood_sd)                                   # 탑승별 기분: 그날 문턱이 조금 오르내림 (숨음)
        th = {d: pt[d] + mood for d in HEADS}; h = pt["h"]; aware = pt["aware"]
        display_on = rng.random() < 0.7
        # 화면 ON/OFF 마르코프 (10초 스텝): 켜짐 평균 3분, 꺼짐 평균 2분
        screen = np.zeros(m, bool); s = rng.random() < 0.6
        for i in range(m):
            screen[i] = s
            s = (rng.random() >= DT / 180) if s else (rng.random() < DT / 120)
        tmp = np.nan_to_num(temp[idx], nan=24.0)
        gate = {"fan": np.ones(m, bool), "ambient": np.full(m, display_on), "dots": screen}
        eff = {"fan": th["fan"] - h * np.maximum(tmp - 24.0, 0), "ambient": np.full(m, th["ambient"]), "dots": np.full(m, th["dots"])}
        for j, d in enumerate(HEADS):
            w = gate[d].astype(np.float32); y = np.zeros(m, int)
            if aware[d]:
                hit = np.flatnonzero(gate[d] & (mm >= eff[d]))
                if len(hit):
                    te = tt[hit[0]]
                    y[(tt < te) & (tt + HORIZON >= te)] = 1
                    w[tt >= te] = 0.0
            Y[idx, j] = y; W[idx, j] = w
    return Y, W, traits


def log_world_missing(X, ride_id, rng, rates=(("G4", 0.8), ("G7", 0.6), ("G3a", 0.5))):
    """로그 세계의 결측 패턴 (탑승 단위). 베이스 세계 표와 다르게."""
    X = X.copy(); rides, inv = np.unique(ride_id, return_inverse=True)
    for g, p in rates:
        cols = [POS[i] for i in C.GROUPS[g] if i in POS]
        drop = rng.random(len(rides)) < p; X[np.ix_(drop[inv], cols)] = np.nan
    return X


def group_dropout74(X, ride_id, rng):
    X = X.copy(); rides, inv = np.unique(ride_id, return_inverse=True)
    for g, cols in DROP_POS.items():
        drop = rng.random(len(rides)) < FO["groups"][g]["dropout"]; X[np.ix_(drop[inv], cols)] = np.nan
    cols = [i for i in range(X.shape[1]) if i not in NEVER_POS]
    mask = rng.random((len(X), len(cols))) < FO["cell_dropout"]; sub = X[:, cols]; sub[mask] = np.nan; X[:, cols] = sub
    return X


# ----------------------------------------------------------------------------- GBM
def gbm_base(X, yb, ride_id, rng, out, wb=None, tag="misc"):
    import lightgbm as lgb
    boosters = {}
    Xd = group_dropout74(X, ride_id, rng)
    for j, d in enumerate(HEADS):
        p = out / f"gbm_base_{tag}_{d}.txt"
        if p.exists():
            boosters[d] = lgb.Booster(model_file=str(p)); continue
        k = np.ones(len(X), bool) if wb is None else (wb[:, j] > 0)
        params = dict(C.PARAMS, num_threads=4, monotone_constraints=MONO)
        boosters[d] = lgb.train(params, lgb.Dataset(np.vstack([X[k], Xd[k]]), np.concatenate([yb[k, j], yb[k, j]])), num_boost_round=300)
        boosters[d].save_model(str(p))
    return boosters


def mono_for(X):
    """단조 제약: 74피처 + (쌓기 방식이면) 헤드 번호 열은 제약 없음."""
    return MONO + [0] * (X.shape[1] - NF)


def stack3(X, Y, W, ride):
    """3헤드 쌓기: 헤드 번호를 75번째 피처로 붙여 행을 3배로. (X·Y·W·ride 모두 같은 순서)"""
    Xs = np.vstack([np.hstack([X, np.full((len(X), 1), j, np.float32)]) for j in range(len(HEADS))])
    return Xs, np.concatenate([Y[:, j] for j in range(len(HEADS))]), np.concatenate([W[:, j] for j in range(len(HEADS))]), np.tile(ride, len(HEADS))


def head_X(X, j):
    return np.hstack([X, np.full((len(X), 1), j, np.float32)])


def gbm_base_stacked(X, yb, ride_id, rng, out, wb, tag="misc"):
    """3헤드 한 모델 베이스: repo 규제 그대로, 헤드 번호 피처 + 행 3배, 300트리."""
    import lightgbm as lgb
    p = out / f"gbm_base_{tag}_stacked.txt"
    if p.exists():
        return lgb.Booster(model_file=str(p))
    Xd = group_dropout74(X, ride_id, rng)
    Xs, ys, ws, _ = stack3(X, yb, wb, ride_id); Xds, _, _, _ = stack3(Xd, yb, wb, ride_id); k = ws > 0
    params = dict(C.PARAMS, num_threads=4, monotone_constraints=mono_for(Xs))
    b = lgb.train(params, lgb.Dataset(np.vstack([Xs[k], Xds[k]]), np.concatenate([ys[k], ys[k]])), num_boost_round=300)
    b.save_model(str(p)); return b


def gbm_continue(base, X, y, w, ride, seed):
    """양성 행 300 이하: 30트리/0.02 고정 (배포 규칙). 그 이상: 공개 탑승의 20%를 검증으로 떼어 조기 종료(최대 300트리/0.05)."""
    import lightgbm as lgb
    k = w > 0
    if y[k].min() == y[k].max():
        return base
    n_pos = int(y[k].sum()); rng = np.random.default_rng(seed)
    params = dict(C.PARAMS, seed=seed, num_threads=4, monotone_constraints=mono_for(X), bagging_fraction=0.8, bagging_freq=1, feature_fraction=0.8)
    params["min_data_in_leaf"] = int(min(100, max(5, k.sum() // 20)))
    if n_pos <= 300:
        params["learning_rate"] = 0.02
        return lgb.train(params, lgb.Dataset(X[k], y[k]), num_boost_round=30, init_model=base, keep_training_booster=True)
    rides = np.unique(ride[k]); val_r = rng.choice(rides, size=max(1, len(rides) // 5), replace=False)
    v = k & np.isin(ride, val_r); tr = k & ~v
    if y[v].min() == y[v].max() or y[tr].min() == y[tr].max():
        v, tr = k, k
    params["learning_rate"] = 0.05
    return lgb.train(params, lgb.Dataset(X[tr], y[tr]), num_boost_round=300, init_model=base, keep_training_booster=True,
                     valid_sets=[lgb.Dataset(X[v], y[v])], callbacks=[lgb.early_stopping(30, verbose=False)])


def gbm_continue_big(base, X, y, w, ride, seed):
    """GBM을 풀어 준 버전: 라벨 양과 무관하게 공개 탑승 20% 검증으로 조기 종료, 최대 300트리·0.05."""
    import lightgbm as lgb
    k = w > 0
    if y[k].min() == y[k].max():
        return base
    rng = np.random.default_rng(seed)
    params = dict(C.PARAMS, seed=seed, num_threads=4, monotone_constraints=mono_for(X), learning_rate=0.05,
                  bagging_fraction=0.8, bagging_freq=1, feature_fraction=0.8)
    params["min_data_in_leaf"] = int(min(100, max(5, k.sum() // 20)))
    rides = np.unique(ride[k]); val_r = rng.choice(rides, size=max(1, len(rides) // 5), replace=False)
    v = k & np.isin(ride, val_r); tr = k & ~v
    if y[v].min() == y[v].max() or y[tr].min() == y[tr].max():
        return lgb.train(params, lgb.Dataset(X[k], y[k]), num_boost_round=100, init_model=base, keep_training_booster=True)
    return lgb.train(params, lgb.Dataset(X[tr], y[tr]), num_boost_round=300, init_model=base, keep_training_booster=True,
                     valid_sets=[lgb.Dataset(X[v], y[v])], callbacks=[lgb.early_stopping(30, verbose=False)])


def gbm_refit(base, X, y, w):
    """잎 재적합: 트리 구조는 베이스 그대로, 잎 값만 새 라벨로 다시 채움 (decay 0.9)."""
    k = w > 0
    if y[k].min() == y[k].max():
        return base
    return base.refit(X[k], y[k], decay_rate=0.9)


# ----------------------------------------------------------------------------- FT (3헤드)
def make_torch():
    import torch, torch.nn as nn

    class FT(nn.Module):
        def __init__(self, nf, d=32, layers=2, heads=4, n_out=3):
            super().__init__()
            self.w = nn.Parameter(torch.randn(nf, d) * 0.1); self.b = nn.Parameter(torch.randn(nf, d) * 0.1)
            self.miss = nn.Parameter(torch.randn(nf, d) * 0.1); self.cls = nn.Parameter(torch.zeros(1, 1, d))
            enc = nn.TransformerEncoderLayer(d, heads, dim_feedforward=2 * d, dropout=0.1, batch_first=True)
            self.enc = nn.TransformerEncoder(enc, layers)
            self.recon = nn.Linear(d, 1); self.head = nn.Linear(d, n_out)

        def forward(self, x, present):
            val = x.unsqueeze(-1) * self.w + self.b
            tok = torch.where(present.unsqueeze(-1), val, self.miss.expand_as(val))
            h = self.enc(torch.cat([self.cls.expand(x.shape[0], -1, -1), tok], 1))
            return h[:, 0], h[:, 1:]
    return torch, FT


def standardize(X, mu, sd):
    return np.clip(np.nan_to_num((X - mu) / sd, nan=0.0), -5, 5).astype(np.float32)


class Runner:
    def __init__(self, torch, FT, seed):
        self.torch, self.FT = torch, FT; torch.manual_seed(seed); self.model = FT(NF); self.rng = np.random.default_rng(seed)

    def _sup(self, m, Z, P, Y, W, idx, pw):
        cls, _ = m(self.torch.from_numpy(Z[idx]), self.torch.from_numpy(P[idx]))
        logit = m.head(cls)
        y = self.torch.from_numpy(Y[idx].astype(np.float32)); w = self.torch.from_numpy(W[idx])
        bce = self.torch.nn.functional.binary_cross_entropy_with_logits(logit, y, pos_weight=pw, reduction="none")
        return (bce * w).sum() / w.sum().clamp(min=1.0)

    def _mask(self, m, Z, P, idx):
        present = P[idx]; target = present & (self.rng.random(present.shape) < 0.3)
        x = self.torch.from_numpy(Z[idx]); _, h = m(x, self.torch.from_numpy(present & ~target))
        return ((m.recon(h).squeeze(-1) - x) ** 2)[self.torch.from_numpy(target)].mean()

    @staticmethod
    def _pw(torch, Y, W):
        pos = (Y * W).sum(0) / np.maximum(W.sum(0), 1); return torch.tensor(((1 - pos) / np.maximum(pos, 1e-3)).astype(np.float32))

    def supervised(self, Z, P, Y, W, steps, lr=1e-3, replay=None):
        """베이스(지도) 또는 덩어리(마스크 + 지도 replay)."""
        m = self.model; opt = self.torch.optim.AdamW(m.parameters(), lr=lr, weight_decay=1e-4); pw = self._pw(self.torch, Y, W); m.train()
        for _ in range(steps):
            idx = self.rng.integers(0, len(Z), 256)
            if replay is None:
                loss = self._sup(m, Z, P, Y, W, idx, pw)
            else:
                Zb, Pb, Yb, Wb, bulk_Z, bulk_P, lam = replay
                loss = self._mask(m, bulk_Z, bulk_P, self.rng.integers(0, len(bulk_Z), 256)) + lam * self._sup(m, Zb, Pb, Yb, Wb, self.rng.integers(0, len(Zb), 128), pw)
            opt.zero_grad(); loss.backward(); opt.step()
        return self

    def finetune_predict(self, Z, P, Y, W, steps, Zte, Pte, seed, lr=3e-4):
        import copy
        self.torch.manual_seed(seed); m = copy.deepcopy(self.model); opt = self.torch.optim.AdamW(m.parameters(), lr=lr, weight_decay=1e-4)
        pw = self._pw(self.torch, Y, W); rng = np.random.default_rng(seed); m.train()
        for _ in range(steps):
            loss = self._sup(m, Z, P, Y, W, rng.integers(0, len(Z), min(128, len(Z))), pw)
            opt.zero_grad(); loss.backward(); opt.step()
        m.eval()
        with self.torch.no_grad():
            cls, _ = m(self.torch.from_numpy(Zte), self.torch.from_numpy(Pte)); return self.torch.sigmoid(m.head(cls)).numpy()

    def state(self): return {k: v.clone() for k, v in self.model.state_dict().items()}
    def load(self, st): self.model.load_state_dict(st); return self


# ----------------------------------------------------------------------------- 실행
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-dataset", required=True, help="베이스 세계 (repo dataset.npz)")
    ap.add_argument("--log-dataset", required=True, help="로그 세계 (make_log_world.py 출력)")
    ap.add_argument("--no-log-missing", action="store_true", help="로그 세계 결측 패턴을 적용하지 않음")
    ap.add_argument("--test-frac", type=float, default=0.2); ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--bulk-sizes", default="10000,all"); ap.add_argument("--events", default="50,all")
    ap.add_argument("--base-steps", type=int, default=1500); ap.add_argument("--bulk-steps", type=int, default=5000); ap.add_argument("--ft-steps", type=int, default=200)
    ap.add_argument("--exclude-test-from-bulk", action="store_true")
    ap.add_argument("--splits", type=int, default=3, help="시험 사람 분할 수 (서로 겹치지 않게)")
    ap.add_argument("--base-label", choices=["misc", "response"], default="misc",
                    help="misc: 합성 MISC 대리 라벨(≥3/≥4) / response: 베이스 세계에도 같은 반응 생성기로 켬 라벨을 만들어 베이스를 굽는다")
    ap.add_argument("--no-bulk", action="store_true", help="③ 덩어리와 ②′ 추가학습을 생략 (① ①″ ② 만)")
    ap.add_argument("--gbm", choices=["perhead", "stacked"], default="perhead", help="GBM 베이스: 헤드별 부스터 3개 | 헤드 번호 피처로 쌓은 한 모델")
    ap.add_argument("--out", default=str(ML / "out" / "bulk_3head"))
    a = ap.parse_args()
    out = pathlib.Path(a.out) / a.base_label; out.mkdir(parents=True, exist_ok=True)
    logf = open(out / "log.txt", "w")
    def log(s): print(s); logf.write(s + "\n"); logf.flush()
    from sklearn.metrics import average_precision_score, roc_auc_score
    torch, FT = make_torch(); T0 = time.time(); rng = np.random.default_rng(0)

    # ---- 베이스 세계 (베이스용)
    db = np.load(a.base_dataset, allow_pickle=False); assert int(db["feature_version"]) == FO["version"]
    XB = db["X"][:, ENABLED].astype(np.float32); ridB = db["ride_id"]
    klist = list(db["k"])
    if a.base_label == "misc":
        YBb = np.stack([db["y"][:, klist.index(BASE_K[h])] for h in HEADS], 1).astype(int); WBb = np.ones_like(YBb, dtype=np.float32)
    else:   # 베이스 세계 사람들에게도 숨은 성질을 주고 켬 라벨을 만든다 (페르소나 시드는 로그 세계와 다르게)
        YBb, WBb, _ = make_response_labels(XB, db["misc"].astype(float), db["t"].astype(float), ridB, db["person_id"], np.random.default_rng(11))
    # ---- 로그 세계 (덩어리·라벨·채점)
    d = np.load(a.log_dataset, allow_pickle=False); assert int(d["feature_version"]) == FO["version"]
    X = d["X"][:, ENABLED].astype(np.float32); misc = d["misc"].astype(float); t = d["t"].astype(float); rid = d["ride_id"]; pid = d["person_id"]
    if not a.no_log_missing:
        X = log_world_missing(X, rid, np.random.default_rng(4))
    Y, W, traits = make_response_labels(X, misc, t, rid, pid, np.random.default_rng(1))
    persons = np.unique(pid); perm_p = rng.permutation(persons); n_te = max(1, int(len(persons) * a.test_frac))
    splits = [perm_p[i * n_te:(i + 1) * n_te] for i in range(a.splits)]
    te = np.isin(pid, splits[0]); lg = ~te; rides = np.unique(rid)          # 사전 점검·로그용 (분할 0)
    log(f"베이스 세계 {len(XB)}행 · 로그 세계 {len(X)}행, 사람 {len(persons)} (시험 {n_te}명 × 분할 {a.splits}), 탑승 {len(rides)}.")
    log("  반응 라벨 양성 비율(자격 행 중): " + ", ".join(f"{h} {Y[W[:, j] > 0, j].mean():.3f}" for j, h in enumerate(HEADS)) +
        " · 사건 있는 탑승: " + ", ".join(f"{h} {int(sum(Y[rid == r, j].max() > 0 for r in rides))}" for j, h in enumerate(HEADS)))
    for j, h in enumerate(HEADS):
        k = te & (W[:, j] > 0)
        log(f"  사전 점검 Oman 규칙 ROC [{h}] = {roc_auc_score(Y[k, j], np.nan_to_num(X[k, OMAN_POS])):.3f}  (0.6~0.75 권장)")

    # ---- 출발점: 베이스 세계 + 합성 대리 라벨 3헤드 (GBM·FT 동일). 표준화도 베이스 세계 통계
    mu = np.nan_to_num(np.nanmean(XB, 0)); sd = np.nanstd(XB, 0); sd[~np.isfinite(sd) | (sd < 1e-6)] = 1.0
    ZB, PB = standardize(XB, mu, sd), np.isfinite(XB)
    Z, P = standardize(X, mu, sd), np.isfinite(X)
    if a.gbm == "stacked":
        gb_s = gbm_base_stacked(XB, YBb, ridB, rng, out, WBb, tag=a.base_label); gb = None; log("GBM 베이스: 3헤드 한 모델(쌓기)")
    else:
        gb = gbm_base(XB, YBb, ridB, rng, out, wb=WBb, tag=a.base_label); gb_s = None
    WB = WBb
    tag = pathlib.Path(a.log_dataset).stem
    fb = out / f"ft_base_{a.base_label}_{a.base_steps}.pt"
    if fb.exists():
        ft_base_state = torch.load(fb); log("FT 베이스 저장본 재사용")
    else:
        ft_base_state = Runner(torch, FT, 0).supervised(ZB, PB, YBb, WB, a.base_steps).state(); torch.save(ft_base_state, fb)
    log(f"베이스 준비 ({time.time() - T0:.0f}s)")

    # ---- 덩어리 사전학습: 로그 세계 입력 (라벨 없음) + 베이스 세계 지도손실 replay
    if a.no_bulk:
        a.bulk_sizes = "none"
    bulk_pool = np.flatnonzero(lg) if a.exclude_test_from_bulk else np.arange(len(X))
    bulk_states = {}
    for bs in ([] if a.no_bulk else a.bulk_sizes.split(",")):
        n_b = len(bulk_pool) if bs == "all" else min(int(bs), len(bulk_pool))
        fbk = out / f"bulk_{a.base_label}_{tag}_{bs}_{a.bulk_steps}{'_notest' if a.exclude_test_from_bulk else ''}.pt"
        if fbk.exists():
            bulk_states[bs] = torch.load(fbk); log(f"덩어리 {bs} 저장본 재사용"); continue
        bidx = np.random.default_rng(2).choice(bulk_pool, size=n_b, replace=False)
        r = Runner(torch, FT, 0).load(ft_base_state)
        r.supervised(ZB, PB, YBb, WB, a.bulk_steps, lr=5e-4, replay=(ZB, PB, YBb, WB, Z[bidx], P[bidx], 0.3))
        bulk_states[bs] = r.state(); torch.save(bulk_states[bs], fbk); log(f"덩어리 {bs} ({n_b}행) 사전학습 끝 ({time.time() - T0:.0f}s)")

    # ---- ②′ 대조: 덩어리 없이 같은 스텝만큼 베이스 지도학습만 더 (③의 추가 학습량 교란 제거)
    fb2 = out / f"ft_base_plus_{a.base_label}_{a.base_steps}_{a.bulk_steps}.pt"
    if a.no_bulk:
        ft_plus_state = None
    elif fb2.exists():
        ft_plus_state = torch.load(fb2)
    else:
        ft_plus_state = Runner(torch, FT, 0).load(ft_base_state).supervised(ZB, PB, YBb, WB, a.bulk_steps, lr=5e-4).state(); torch.save(ft_plus_state, fb2)
    log(f"②′ 추가학습 대조 준비 ({time.time() - T0:.0f}s)")

    # ---- 공개 사건 → 세 계보 → 채점
    rows = []
    for sp, test_p in enumerate(splits):
      te = np.isin(pid, test_p); lg = ~te; log_rides = np.unique(rid[lg])
      def rec(bs, ev, seed, lineage, pred, te=te, sp=sp):
        for j, h in enumerate(HEADS):
            k = te & (W[:, j] > 0)
            if Y[k, j].min() == Y[k, j].max():
                continue
            rows.append({"split": sp, "bulk": bs, "events": ev, "seed": seed, "lineage": lineage, "head": h,
                         "roc_auc": float(roc_auc_score(Y[k, j], pred[j][k[te]])),
                         "pr_auc": float(average_precision_score(Y[k, j], pred[j][k[te]]))})
      for ev in a.events.split(","):
        for seed in range(a.seeds):
            srng = np.random.default_rng([3, sp, seed])
            if ev == "all":
                open_r = log_rides
            else:                                   # 탑승 단위 공개. 헤드별 목표 = min(ev, 그 헤드 가용 사건의 60%)
                avail = np.array([sum(Y[rid == r, j].max() > 0 for r in log_rides) for j in range(3)])
                need = np.minimum(int(ev), np.maximum(1, (avail * 0.6).astype(int)))
                perm = srng.permutation(log_rides); cnt = np.zeros(3, int); open_r = []
                for r in perm:
                    m = rid == r; open_r.append(r); cnt += (Y[m].max(0) > 0)
                    if (cnt >= need).all():
                        break
                open_r = np.array(open_r)
                if seed == 0:
                    log(f"  공개 사건 목표 {need.tolist()} (가용 {avail.tolist()}) → 탑승 {len(open_r)}개, 사건 {cnt.tolist()}")
            op = np.isin(rid, open_r)
            # ① GBM / ①′ 풀어 준 이어학습 / ①″ 잎 재적합
            if a.gbm == "stacked":                   # 세 헤드 라벨이 한 모델에 한꺼번에 들어간다
                Xs, ys, ws, rs = stack3(X[op], Y[op], W[op], rid[op])
                b1 = gbm_continue(gb_s, Xs, ys, ws, rs, seed); bb = gbm_continue_big(gb_s, Xs, ys, ws, rs, seed); br = gbm_refit(gb_s, Xs, ys, ws)
                pred = [b1.predict(head_X(X[te], j)) for j in range(3)]
                pred_big = [bb.predict(head_X(X[te], j)) for j in range(3)]; pred_ref = [br.predict(head_X(X[te], j)) for j in range(3)]
            else:
                pred, pred_big, pred_ref = [], [], []
                for j, h in enumerate(HEADS):
                    pred.append(gbm_continue(gb[h], X[op], Y[op, j], W[op, j], rid[op], seed).predict(X[te]))
                    pred_big.append(gbm_continue_big(gb[h], X[op], Y[op, j], W[op, j], rid[op], seed).predict(X[te]))
                    pred_ref.append(gbm_refit(gb[h], X[op], Y[op, j], W[op, j]).predict(X[te]))
            for bs in a.bulk_sizes.split(","):
                rec(bs, ev, seed, "① GBM 베이스→사건", pred)
            for bs in a.bulk_sizes.split(","):
                rec(bs, ev, seed, "①′ GBM 큰 이어학습", pred_big); rec(bs, ev, seed, "①″ GBM 잎 재적합", pred_ref)
            # ② FT 베이스 → 사건
            p2 = Runner(torch, FT, seed).load(ft_base_state).finetune_predict(Z[op], P[op], Y[op], W[op], a.ft_steps, Z[te], P[te], seed)
            for bs in a.bulk_sizes.split(","):
                rec(bs, ev, seed, "② FT 베이스→사건", [p2[:, j] for j in range(3)])
            if not a.no_bulk:
                # ②′ FT 베이스 → 추가학습(덩어리 없음) → 사건
                p2b = Runner(torch, FT, seed).load(ft_plus_state).finetune_predict(Z[op], P[op], Y[op], W[op], a.ft_steps, Z[te], P[te], seed)
                for bs in a.bulk_sizes.split(","):
                    rec(bs, ev, seed, "②′ FT 베이스+추가학습→사건", [p2b[:, j] for j in range(3)])
                # ③ FT 베이스 → 덩어리 → 사건
                for bs in a.bulk_sizes.split(","):
                    p3 = Runner(torch, FT, seed).load(bulk_states[bs]).finetune_predict(Z[op], P[op], Y[op], W[op], a.ft_steps, Z[te], P[te], seed)
                    rec(bs, ev, seed, "③ FT 베이스→덩어리→사건", [p3[:, j] for j in range(3)])
            log(f"  분할 {sp} · 사건 {ev} · 시드 {seed} 끝 (공개 탑승 {len(open_r)}) ({time.time() - T0:.0f}s)")

    # ---- 정리
    with open(out / "results.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    bss = a.bulk_sizes.split(","); evs = a.events.split(",")
    lins_all = ["① GBM 베이스→사건", "①′ GBM 큰 이어학습", "①″ GBM 잎 재적합", "② FT 베이스→사건", "②′ FT 베이스+추가학습→사건", "③ FT 베이스→덩어리→사건"]
    present = {r["lineage"] for r in rows}; lins = [l for l in lins_all if l in present]
    def agg(h, bs, ev, lin, key="roc_auc"):
        x = np.array([r[key] for r in rows if r["head"] == h and r["bulk"] == bs and r["events"] == ev and r["lineage"] == lin]); return (x.mean(), x.std()) if len(x) else (np.nan, np.nan)
    def wins(h, bs, ev, l1, l2):
        dd = {}
        for r in rows:
            if r["head"] == h and r["bulk"] == bs and r["events"] == ev and r["lineage"] in (l1, l2):
                dd.setdefault((r["split"], r["seed"]), {})[r["lineage"]] = r["roc_auc"]
        pr = [v for v in dd.values() if len(v) == 2]; return sum(v[l1] > v[l2] for v in pr), len(pr)
    L = [f"# 덩어리 사전학습 실험 — 3헤드 반응 라벨, 베이스 세계 → 로그 세계 (베이스 라벨: {a.base_label}, GBM: {a.gbm})", "",
         f"베이스 세계 {len(XB)}행으로 베이스. 로그 세계 {len(X)}행, 사람 {len(persons)}(시험 {n_te}명 × 분할 {a.splits}). "
         f"덩어리 {a.bulk_steps}스텝, 미세조정 {a.ft_steps}스텝, 시드 {a.seeds}. 값은 ROC-AUC 평균 ± 표준편차 (시험 사람).", ""]
    for h in HEADS:
        L += [f"## {h}", "", "| 덩어리 | 사건 | " + " | ".join(lins) + " | ②>①′ | ③>②′ |", "|---|---|" + "---|" * (len(lins) + 2)]
        for bs in bss:
            for ev in evs:
                cells = [f"{agg(h, bs, ev, l)[0]:.3f} ± {agg(h, bs, ev, l)[1]:.3f}" for l in lins]
                w1 = wins(h, bs, ev, "② FT 베이스→사건", "①′ GBM 큰 이어학습")
                w2 = wins(h, bs, ev, "③ FT 베이스→덩어리→사건", "②′ FT 베이스+추가학습→사건") if not a.no_bulk else (0, 0)
                L.append(f"| {bs} | {ev} | " + " | ".join(cells) + f" | {w1[0]}/{w1[1]} | {w2[0]}/{w2[1]} |")
        L.append("")
    L += ["## 읽는 법", "- '사건 50'은 헤드별 목표 min(50, 가용의 60%) 만큼만 공개 (log.txt 에 실제 수).", "- 핵심 칸은 '덩어리 all · 사건 50'. ③ > ②′ 이면 덩어리가 라벨을 보충한다. ② > ①′ 이면 GBM을 풀어 줘도 FT 미세조정이 더 빨리 따라간다.",
          "- ①′는 GBM 이어학습을 라벨 양과 무관하게 조기종료·최대 300트리로 풀어 준 것, ①″는 트리 구조를 두고 잎 값만 다시 채운 것.",
          "- ②′는 ③과 같은 스텝만큼 베이스 라벨로만 더 배운 대조. ③ ≈ ②′ 이면 덩어리 효과 없음(추가 학습 덕).",
          f"- 값의 ±는 시험 분할 {a.splits}개 × 시드 {a.seeds}에 걸친 표준편차.", "- 사건 all 에서 셋이 비슷하면 정상.",
          "- 사전 점검 Oman 규칙 ROC 가 0.85 를 넘으면 숨은 성질이 약한 것 → make_response_labels 의 theta_sd 를 키워 다시.",
          f"", f"총 {time.time() - T0:.0f}초"]
    (out / "summary.md").write_text("\n".join(L), encoding="utf-8"); log("\n".join(L))

    import logging, matplotlib; matplotlib.use("Agg"); logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)
    import matplotlib.pyplot as plt
    plt.rcParams["font.family"] = ["NanumGothic", "Noto Sans CJK KR", "DejaVu Sans"]
    fig, axes = plt.subplots(1, 3, figsize=(13, 4), sharey=True)
    cells = [(bs, ev) for bs in bss for ev in evs]; xl = [f"덩어리 {bs}\n사건 {ev}" for bs, ev in cells]
    for ax, h in zip(axes, HEADS):
        for lin, c in zip(lins, [dict(zip(lins_all, ["#9B9A97", "#B5B3AE", "#D0CEC9", "#C9C7C1", "#6F6E6A", "#37352F"]))[l] for l in lins]):
            ax.errorbar(range(len(cells)), [agg(h, bs, ev, lin)[0] for bs, ev in cells], yerr=[agg(h, bs, ev, lin)[1] for bs, ev in cells], marker="o", capsize=3, color=c, label=lin)
        ax.set_xticks(range(len(cells))); ax.set_xticklabels(xl, fontsize=8); ax.set_title(h); ax.grid(alpha=.3)
    axes[0].set_ylabel("ROC-AUC"); axes[0].legend(fontsize=7); fig.tight_layout(); fig.savefig(out / "fig.png", dpi=150)
    log(f"저장: {out}")


if __name__ == "__main__":
    main()
