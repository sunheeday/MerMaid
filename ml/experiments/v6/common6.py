"""5차 실험 공용 — 세계 읽기, 입력 집합(v6/v5/그룹 제거), 표준화, 공통 모델 3종(GBM 쌓기 / MLP / FT), 채점."""
import pathlib
import sys
import time

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parents[1] / "train"))
import world6 as W6  # noqa: E402

HEADS = W6.HEADS; NH = 3
ENABLED_IDX = [i for i in range(W6.NF) if W6.ENABLED[i]]                    # 65 (svc_conflict 제외)
NEW_GROUPS = ["G11", "G12", "G13", "G14", "G15"]


def input_cols(name):
    """입력 집합 이름 → 열 번호 목록. v6 = 전부, v5 = 새 피처 없이, -G1x = 그 그룹만 뺌, G1x = v5 + 그 그룹만."""
    if name == "v6":
        return ENABLED_IDX
    if name == "v5":
        return [i for i in ENABLED_IDX if W6.GROUPS[i] not in NEW_GROUPS]
    if name.startswith("-") and name[1:] in NEW_GROUPS:
        return [i for i in ENABLED_IDX if W6.GROUPS[i] != name[1:]]
    if name in NEW_GROUPS:
        return [i for i in ENABLED_IDX if W6.GROUPS[i] not in NEW_GROUPS or W6.GROUPS[i] == name]
    raise ValueError(name)


def load_world(path, stride=1):
    d = np.load(path, allow_pickle=False)
    assert str(d["feature_version"]) == "6exp" and list(d["feature_names"]) == W6.NAMES, "v6 세계 파일이 아니다"
    out = {k: d[k] for k in d.files if k not in ("feature_names", "feature_groups", "feature_version", "heads", "k", "veh_cfg", "world", "habit")}
    out["X"] = out["X"].astype(np.float32); out["Y"] = out["Y"].astype(np.int8); out["W"] = out["W"].astype(np.float32)
    out["t"] = out["t"].astype(float)
    for k in ("ride_id", "trip_id", "person_id", "ride_order", "vehicle_id", "trip_order"):
        if k in out:
            out[k] = out[k].astype(np.int64)
    if stride > 1:
        keep = np.zeros(len(out["t"]), bool)
        for r in np.unique(out["ride_id"]):
            i = np.flatnonzero(out["ride_id"] == r); keep[i[::stride]] = True
        out = {k: (v[keep] if isinstance(v, np.ndarray) and len(v) == len(keep) else v) for k, v in out.items()}
    out["world"] = str(d["world"]); out["habit"] = str(d["habit"])
    return out


def standardize(X, mu, sd):
    return np.clip(np.nan_to_num((X - mu) / sd, nan=0.0), -5, 5).astype(np.float32)


def stats(X):
    mu = np.nan_to_num(np.nanmean(X, 0)); sd = np.nanstd(X, 0); sd[~np.isfinite(sd) | (sd < 1e-6)] = 1.0
    return mu.astype(np.float32), sd.astype(np.float32)


def pos_weight(torch, Y, W):
    pos = (Y * W).sum(0) / np.maximum(W.sum(0), 1); return torch.tensor(((1 - pos) / np.maximum(pos, 1e-3)).astype(np.float32))


# ---------------------------------------------------------------- 신경망 몸통 2종 (호출 규약 m(Z, P) → (요약 d, 보조))
def make_torch():
    import torch, torch.nn as nn

    class FT(nn.Module):                      # 피처 토큰 트랜스포머: 피처 간 어텐션, 결측은 [MISS] 토큰
        def __init__(self, nf, d=32, layers=2, heads=4, n_out=NH):
            super().__init__()
            self.w = nn.Parameter(torch.randn(nf, d) * 0.1); self.b = nn.Parameter(torch.randn(nf, d) * 0.1)
            self.miss = nn.Parameter(torch.randn(nf, d) * 0.1); self.cls = nn.Parameter(torch.zeros(1, 1, d))
            self.enc = nn.TransformerEncoder(nn.TransformerEncoderLayer(d, heads, dim_feedforward=2 * d, dropout=0.1, batch_first=True), layers)
            self.head = nn.Linear(d, n_out); self.dim = d

        def forward(self, x, present):
            val = x.unsqueeze(-1) * self.w + self.b
            tok = torch.where(present.unsqueeze(-1), val, self.miss.expand_as(val))
            h = self.enc(torch.cat([self.cls.expand(x.shape[0], -1, -1), tok], 1))
            return h[:, 0], h[:, 1:]

    class MLP(nn.Module):                     # [표준화 nf ‖ 결측 마스크 nf] → 128 → 64 → d. 입력에 따라 보는 방식이 안 바뀜
        def __init__(self, nf, d=32, n_out=NH):
            super().__init__()
            self.body = nn.Sequential(nn.Linear(2 * nf, 128), nn.GELU(), nn.Dropout(0.1), nn.Linear(128, 64), nn.GELU(), nn.Linear(64, d))
            self.head = nn.Linear(d, n_out); self.dim = d

        def forward(self, x, present):
            return self.body(torch.cat([x, present.float()], 1)), None
    return torch, {"ft": FT, "mlp": MLP}


def net_predict(torch, m, Z, P, chunk=8192):
    m.eval(); out = []
    with torch.no_grad():
        for i in range(0, len(Z), chunk):
            out.append(torch.sigmoid(m.head(m(torch.from_numpy(Z[i:i + chunk]), torch.from_numpy(P[i:i + chunk]))[0])).numpy())
    return np.concatenate(out) if out else np.zeros((0, NH), np.float32)


def net_embed(torch, m, Z, P, chunk=8192):
    m.eval(); out = []
    with torch.no_grad():
        for i in range(0, len(Z), chunk):
            out.append(m(torch.from_numpy(Z[i:i + chunk]), torch.from_numpy(P[i:i + chunk]))[0].numpy())
    return np.concatenate(out).astype(np.float32)


def train_net(torch, m, Z, P, Y, W, steps=4000, batch=256, lr=1e-3, seed=0, val=None, every=500, log=print):
    """가중 BCE(pos_weight), AdamW. val=(Zv,Pv,Yv,Wv) 를 주면 every 스텝마다 ROC 평균으로 최고 가중치를 고른다."""
    from sklearn.metrics import roc_auc_score
    rng = np.random.default_rng(seed); torch.manual_seed(seed)
    opt = torch.optim.AdamW(m.parameters(), lr=lr, weight_decay=1e-4); pw = pos_weight(torch, Y, W)
    elig = np.flatnonzero(W.max(1) > 0); best = (-1.0, 0, None); m.train()
    for s in range(steps):
        idx = rng.choice(elig, batch)
        cls, _ = m(torch.from_numpy(Z[idx]), torch.from_numpy(P[idx])); logit = m.head(cls)
        y = torch.from_numpy(Y[idx].astype(np.float32)); w = torch.from_numpy(W[idx])
        bce = torch.nn.functional.binary_cross_entropy_with_logits(logit, y, pos_weight=pw, reduction="none")
        loss = (bce * w).sum() / w.sum().clamp(min=1.0); opt.zero_grad(); loss.backward(); opt.step()
        if val is not None and ((s + 1) % every == 0 or s + 1 == steps):
            Zv, Pv, Yv, Wv = val; pv = net_predict(torch, m, Zv, Pv); m.train()
            sc = np.mean([roc_auc_score(Yv[Wv[:, j] > 0, j], pv[Wv[:, j] > 0, j]) for j in range(NH)])
            if sc > best[0]:
                best = (sc, s + 1, {k: v.clone() for k, v in m.state_dict().items()})
            log(f"    스텝 {s + 1} 손실 {loss.item():.3f} 검증 ROC {sc:.3f} (최고 {best[0]:.3f} @ {best[1]})")
    if best[2] is not None:
        m.load_state_dict(best[2])
    m.eval(); return m, best[1]


# ---------------------------------------------------------------- GBM (3헤드 쌓기: 헤드 번호를 마지막 피처로, 행 3배)
def gbm_params(cols, extra=None):
    import common as C
    mono = [W6.MONOTONE[i] for i in cols] + [0]
    p = dict(C.PARAMS, num_threads=max(1, extra or 4), monotone_constraints=mono); return p


def stack3(X, Y, W):
    Xs = np.vstack([np.hstack([X, np.full((len(X), 1), j, np.float32)]) for j in range(NH)])
    return Xs, np.concatenate([Y[:, j] for j in range(NH)]).astype(np.float32), np.concatenate([W[:, j] for j in range(NH)])


def train_gbm(Xtr, Ytr, Wtr, Xva, Yva, Wva, cols, threads=4, max_rounds=1500, log=print):
    import lightgbm as lgb
    import common as C
    Xs, ys, ws = stack3(Xtr, Ytr, Wtr); k = ws > 0
    Xv, yv, wv = stack3(Xva, Yva, Wva); kv = wv > 0
    ds = lgb.Dataset(Xs[k], ys[k], free_raw_data=True); dv = lgb.Dataset(Xv[kv], yv[kv], reference=ds)
    stop = C.early_stop_after(C.MIN_ROUNDS, C.EARLY_STOP)
    b = lgb.train(gbm_params(cols, threads), ds, max_rounds, valid_sets=[dv], callbacks=[stop])
    if not b.best_iteration:
        b.best_iteration = stop.best["it"]
    log(f"    GBM 트리 {b.best_iteration}개"); return b


def gbm_predict(b, X):
    return np.column_stack([b.predict(np.hstack([X, np.full((len(X), 1), j, np.float32)]), num_iteration=b.best_iteration) for j in range(NH)]).astype(np.float32)


# ---------------------------------------------------------------- 채점
def roc_cells(p, Y, W, veh=None):
    """헤드별 플릿 ROC, 차별 ROC(dict vehicle → [3])."""
    from sklearn.metrics import roc_auc_score
    fl = []; per = {}
    for j in range(NH):
        k = W[:, j] > 0; fl.append(roc_auc_score(Y[k, j], p[k, j]) if Y[k, j].min() != Y[k, j].max() else np.nan)
    if veh is not None:
        for v in np.unique(veh):
            per[int(v)] = []
            for j in range(NH):
                kv = (W[:, j] > 0) & (veh == v)
                per[int(v)].append(roc_auc_score(Y[kv, j], p[kv, j]) if kv.any() and Y[kv, j].min() != Y[kv, j].max() else np.nan)
    return np.array(fl), per


def person_val_split(pid, n_val, seed):
    rng = np.random.default_rng(seed); u = np.unique(pid); vp = rng.choice(u, size=min(n_val, len(u) // 5), replace=False)
    return np.isin(pid, vp)


class Timer:
    def __init__(self): self.t0 = time.time()
    def __call__(self): return time.time() - self.t0
