"""6차 실험 — 문맥 모델 2단계 비교 (GRU vs 어텐션), v7 세계판.  긴 이력(k ≤ 4096)·GPU 용으로 문맥 조립을 전부 배열 연산으로 바꿨다.

    python3 ml/experiments/v7/context7.py --world ml/out/v7/m4_iid/base.npz --stage2 attn --k 512 --seed 0 --device cuda \
        [--input v5] [--hide G2,G5] [--select recent|uniform] [--time none|rides] [--neg-per-ride 2] [--d1 32] [--d2 64] [--steps 10000]

한 세계 안에서 사람을 학습/검증/시험으로 나눈다 (분할은 시드와 무관하게 고정: 시험 --test-persons 명, 검증 --val-persons 명, 나머지 학습).
1단계  mlp: [표준화 ‖ 마스크] → 128 → 64 → d1  (학습 사람 행으로 학습해 동결, 캐시에 저장)   linear: 몸통 없이 그대로
2단계  gru      과거 토큰을 순서대로 상태 하나에 (v6 의 권고안)
       attn     지금(질의)이 과거 토큰에 교차 어텐션 (층 --layers, 머리 4, 항상 보이는 빈 토큰 1개) — 마지막 층 가중치로 '같은 모드를 골랐나' 진단
       enc      v6 방식: [질의 ‖ 토큰] 인코더 (토큰끼리도 어텐션)
       hybrid   gru 상태 ‖ attn 요약 → 머리
       mean_dev 장치별 마스크 평균 (질의 X, 순서 X)
토큰   [E(d1) ‖ 장치 원핫 3 ‖ 라벨 1 ‖ (시각 2: log1p(나이)/log(401), 1/(1+나이); --time rides)]  나이 = 지금 탑승 번호 − 토큰 탑승 번호
       양성 = 켠 순간, 음성 = 자격 있는데 안 켠 순간 --neg-per-ride 개 (탑승·장치마다)
선택   recent: 최근 k개   uniform: 이력 전체에서 고르게 k개 (k 보다 적으면 전부)
채점   시험 사람의 자격 행 전부.  전체 ROC · 이력 길이별 · 같은 모드 마지막 참고 거리별(먼 참고) · 모드 전환 직후 ·
       진단 empty/swap/nolabel/nosit + samemode(같은 모드 토큰만 주는 오라클 문맥) + 어텐션 같은 모드 가중치 비율
출력   <out>/{summary.md, metrics.json, oneline.csv, log.txt, ctx_<stage2>_k<k>.pt}
"""
import argparse
import json
import pathlib
import sys
import time

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "v6"))
import common6 as C6  # noqa: E402
import world6 as W6  # noqa: E402
HEADS = C6.HEADS; NH = 3
ORD_MUL = 100000                          # 사람 × 탑승 번호 합성 키
KEY_AUTO = ["speed_mean_1m", "speed_mean_5m", "speed_max_5m", "stop_ratio_5m", "rms_x_1m", "rms_y_1m", "jerk_rms_y_1m",
            "hard_brake_count_5m", "hard_turn_count_5m", "cabin_temp", "seat_row"]          # v5 로 보이는 상황 열쇠 (G2·G1·G5·G6)


# ---------------------------------------------------------------- 토큰 (배열 연산)
def build_tokens(Y, W, t, ride_id, person_id, ride_order, mode, rng, neg_per_ride=2):
    """→ 토큰 배열 (N×5: person, order, row, device, label) 과 mode (N). 탑승마다 장치별 양성 ≤1 + 음성 ≤ neg_per_ride."""
    o = np.lexsort((t, ride_id)); rid = ride_id[o]
    starts = np.flatnonzero(np.concatenate([[True], rid[1:] != rid[:-1]])); ends = np.concatenate([starts[1:], [len(rid)]])
    out = []
    for s, e in zip(starts, ends):
        idx = o[s:e]; p = person_id[idx[0]]; od = ride_order[idx[0]]; mo = mode[idx[0]]
        for j in range(NH):
            yj = Y[idx, j]; wj = W[idx, j]
            pos = idx[yj == 1]
            if len(pos):
                out.append((p, od, pos[-1], j, 1, mo))
            neg = idx[(wj > 0) & (yj == 0)]
            if len(neg):
                for i in rng.choice(neg, size=min(neg_per_ride, len(neg)), replace=False):
                    out.append((p, od, i, j, 0, mo))
    T = np.array(out, np.int64).reshape(-1, 6)
    T = T[np.lexsort((T[:, 2], T[:, 1], T[:, 0]))]
    return T[:, :5], T[:, 5]


class TokIndex:
    """그룹(사람 또는 사람×모드) 안에서 탑승 순서로 정렬된 토큰 → 질의 행마다 '내 앞 토큰' 구간 [lo, c)."""
    def __init__(self, tok, tok_group, row_group, row_order):
        o = np.lexsort((tok[:, 1], tok_group)); self.perm = o
        g = tok_group[o]; od = tok[o, 1]
        key_t = g.astype(np.int64) * ORD_MUL + od
        self.lo = np.searchsorted(key_t, row_group.astype(np.int64) * ORD_MUL)                   # 그룹 시작
        self.c = np.searchsorted(key_t, row_group.astype(np.int64) * ORD_MUL + row_order, "left")  # 내 탑승 앞까지
        self.hi = np.searchsorted(key_t, (row_group.astype(np.int64) + 1) * ORD_MUL)             # 그룹 끝 (swap 용)


class Ctx:
    """GPU 상의 토큰 표와 질의 배치 → (C, mask) 조립."""
    def __init__(self, torch, dev, E, tok, idx, k, select, use_time, n_time):
        self.torch, self.dev, self.k, self.select, self.use_time = torch, dev, k, select, use_time
        de = E.shape[1]; self.de = de; perm = idx.perm; tk = tok[perm]
        F = np.zeros((len(tk) + 1, de + NH + 1 + n_time), np.float32)                              # 마지막 행 = 빈 토큰(0)
        F[:-1, :de] = E[tk[:, 2]]; F[np.arange(len(tk)), de + tk[:, 3]] = 1.0; F[:-1, de + NH] = tk[:, 4]
        self.F = torch.from_numpy(F).to(dev); self.order = torch.from_numpy(np.concatenate([tk[:, 1], [0]])).to(dev)
        self.null = len(tk); self.ar = torch.arange(k, device=dev)
        self.lo = torch.from_numpy(idx.lo).to(dev); self.c = torch.from_numpy(idx.c).to(dev); self.hi = torch.from_numpy(idx.hi).to(dev)

    def gather(self, ii, order_now, lo=None, c=None):
        """ii: 질의 행 번호 (torch long, dev). → C (B,k,F), mask (B,k) True=빈 칸."""
        torch = self.torch; lo = self.lo[ii] if lo is None else lo; c = self.c[ii] if c is None else c
        n = c - lo; B = len(ii)
        rec = c[:, None] - self.k + self.ar[None, :]
        if self.select == "uniform":
            uni = lo[:, None] + (self.ar[None, :] * n[:, None]) // self.k
            idx = torch.where((n > self.k)[:, None], uni, rec)
        else:
            idx = rec
        valid = idx >= lo[:, None]
        idx = torch.where(valid, idx, torch.full_like(idx, self.null))
        C = self.F[idx]
        if self.use_time:
            age = (order_now[:, None] - self.order[idx]).clamp(min=0).float()
            C[:, :, -2] = torch.log1p(age) / np.log(401.0); C[:, :, -1] = 1.0 / (1.0 + age)
            C[:, :, -2:] *= valid.unsqueeze(-1).float()
        return C, ~valid


# ---------------------------------------------------------------- 1단계: 구간 선형 부호화(PLE) MLP
def make_ple_mlp(torch, nf, d, edges):
    """Gorishniy 2022 의 piecewise-linear encoding: 표준화된 x 를 분위수 구간 B개의 [0,1] 채움 벡터로 (NaN → 0, 마스크는 따로 붙는다)."""
    nn = torch.nn; B = edges.shape[1] - 1

    class PleMLP(nn.Module):
        def __init__(self):
            super().__init__()
            self.register_buffer("lo", torch.tensor(edges[:, :-1], dtype=torch.float32)); self.register_buffer("hi", torch.tensor(edges[:, 1:], dtype=torch.float32))
            self.body = nn.Sequential(nn.Linear(nf * B + nf, 128), nn.GELU(), nn.Dropout(0.1), nn.Linear(128, 64), nn.GELU(), nn.Linear(64, d))
            self.head = nn.Linear(d, NH); self.dim = d

        def forward(self, x, present):
            enc = ((x.unsqueeze(-1) - self.lo) / (self.hi - self.lo).clamp(min=1e-6)).clamp(0, 1) * present.unsqueeze(-1).float()
            return self.body(torch.cat([enc.flatten(1), present.float()], 1)), None
    return PleMLP()


# ---------------------------------------------------------------- 2단계 망
def make_net(torch, de, nf_tok, d=64, layers=2, heads=4, dropout=0.1, stage2="attn"):
    nn = torch.nn

    class Enc(nn.Module):                                  # v6 attn: [질의 ‖ 토큰] 인코더
        def __init__(self):
            super().__init__()
            self.pq = nn.Linear(de, d); self.pc = nn.Linear(nf_tok, d); self.tok_type = nn.Parameter(torch.zeros(2, d))
            self.enc = nn.TransformerEncoder(nn.TransformerEncoderLayer(d, heads, dim_feedforward=2 * d, dropout=dropout, batch_first=True), layers,
                                             enable_nested_tensor=False)                 # 빠른 경로(nested tensor)는 GPU 동시 실행에서 불법 메모리 접근을 냈다
            self.head = nn.Linear(d, NH)

        def forward(self, q, c, mask):
            tq = self.pq(q).unsqueeze(1) + self.tok_type[0]; tc = self.pc(c) + self.tok_type[1]
            x = torch.cat([tq, tc], 1); pad = torch.cat([torch.zeros(mask.shape[0], 1, dtype=torch.bool, device=mask.device), mask], 1)
            return self.head(self.enc(x, src_key_padding_mask=pad)[:, 0])

    class XAttn(nn.Module):                                # 질의 → 토큰 교차 어텐션 (가중치 진단 가능)
        def __init__(self):
            super().__init__()
            self.pq = nn.Linear(de, d); self.pc = nn.Linear(nf_tok, d); self.null = nn.Parameter(torch.zeros(1, 1, d))
            self.ln_q = nn.ModuleList([nn.LayerNorm(d) for _ in range(layers)]); self.ln_c = nn.LayerNorm(d)
            self.att = nn.ModuleList([nn.MultiheadAttention(d, heads, dropout=dropout, batch_first=True) for _ in range(layers)])
            self.ff = nn.ModuleList([nn.Sequential(nn.LayerNorm(d), nn.Linear(d, 2 * d), nn.GELU(), nn.Dropout(dropout), nn.Linear(2 * d, d)) for _ in range(layers)])
            self.head = nn.Sequential(nn.LayerNorm(d), nn.Linear(d, NH)); self.last_w = None

        def pooled(self, q, c, mask, need_w=False):
            B = q.shape[0]; tq = self.pq(q).unsqueeze(1); tc = self.ln_c(torch.cat([self.null.expand(B, -1, -1), self.pc(c)], 1))
            pad = torch.cat([torch.zeros(B, 1, dtype=torch.bool, device=mask.device), mask], 1)
            for i in range(len(self.att)):
                a, w = self.att[i](self.ln_q[i](tq), tc, tc, key_padding_mask=pad, need_weights=need_w or i == len(self.att) - 1)
                tq = tq + a; tq = tq + self.ff[i](tq)
            self.last_w = w[:, 0, 1:] if w is not None else None                                 # (B, k) 빈 토큰 제외
            return tq[:, 0]

        def forward(self, q, c, mask):
            return self.head(self.pooled(q, c, mask))

    class Gru(nn.Module):
        def __init__(self):
            super().__init__()
            self.pq = nn.Linear(de, d); self.pc = nn.Linear(nf_tok, d); self.drop = nn.Dropout(dropout)
            self.gru = nn.GRU(d, d, num_layers=1, batch_first=True)
            self.head = nn.Sequential(nn.Linear(2 * d, d), nn.GELU(), nn.Dropout(dropout), nn.Linear(d, NH))

        def state(self, q, c, mask):
            tq = self.pq(q); keep = ~mask; n = keep.sum(1)
            tc = self.drop(self.pc(c)) * keep.unsqueeze(-1).float()
            # 빈 칸이 앞·중간에 섞여 있으므로(왼쪽 패딩 + 학습 때 무작위 제거) 유효 토큰만 순서대로 앞으로 당긴 뒤 n번째 출력을 읽는다
            pos = torch.cumsum(keep.long(), 1) - 1; src = torch.where(keep, pos, torch.full_like(pos, c.shape[1] - 1))
            left = torch.zeros_like(tc).scatter(1, src.unsqueeze(-1).expand_as(tc), tc)
            out, _ = self.gru(left)
            h = out[torch.arange(len(n), device=q.device), (n - 1).clamp(min=0)]
            return tq, torch.where((n > 0).unsqueeze(-1), h, torch.zeros_like(h))

        def forward(self, q, c, mask):
            tq, h = self.state(q, c, mask); return self.head(torch.cat([tq, h], 1))

    class Hybrid(nn.Module):
        def __init__(self):
            super().__init__(); self.g = Gru(); self.x = XAttn(); self.head = nn.Sequential(nn.Linear(3 * d, d), nn.GELU(), nn.Dropout(dropout), nn.Linear(d, NH))

        @property
        def last_w(self): return self.x.last_w

        def forward(self, q, c, mask):
            tq, h = self.g.state(q, c, mask); return self.head(torch.cat([tq, h, self.x.pooled(q, c, mask)], 1))

    class MeanDev(nn.Module):
        def __init__(self):
            super().__init__()
            self.pq = nn.Linear(de, d); self.pc = nn.Linear(nf_tok, d); self.drop = nn.Dropout(dropout)
            self.head = nn.Sequential(nn.Linear((1 + NH) * d, d), nn.GELU(), nn.Dropout(dropout), nn.Linear(d, NH))

        def forward(self, q, c, mask):
            keep = (~mask).unsqueeze(-1).float(); tc = self.drop(self.pc(c)) * keep; hs = []
            for j in range(NH):
                kj = keep * c[:, :, de + j:de + j + 1]; hs.append((tc * kj).sum(1) / kj.sum(1).clamp(min=1.0))
            return self.head(torch.cat([self.pq(q)] + hs, 1))

    return {"attn": XAttn, "enc": Enc, "gru": Gru, "hybrid": Hybrid, "mean_dev": MeanDev}[stage2]()


# ---------------------------------------------------------------- 예측
PRED_CHUNK = 4096


def predict(torch, net, ctx, Eq, rows, order, variant="ctx", tok_mode=None, mode_rows=None, swap_lo=None, swap_c=None, chunk=None):
    """variant: ctx | empty | swap | nolabel | nosit.  tok_mode/mode_rows 를 주면 어텐션 같은 모드 가중치 비율도 돌려준다."""
    chunk = chunk or PRED_CHUNK
    net.eval(); dev = ctx.dev; out = np.zeros((len(rows), NH), np.float32); ratio = []; de = ctx.de
    with torch.no_grad():
        for s in range(0, len(rows), chunk):
            ii = torch.from_numpy(rows[s:s + chunk]).to(dev); on = torch.from_numpy(order[rows[s:s + chunk]]).to(dev)
            if variant == "swap":
                C, M = ctx.gather(ii, on, lo=swap_lo[s:s + chunk], c=swap_c[s:s + chunk])
            else:
                C, M = ctx.gather(ii, on)
            if variant == "empty":
                M[:] = True; C[:] = 0
            elif variant == "nolabel":
                C[:, :, de + NH] = 0.0
            elif variant == "nosit":
                C[:, :, :de] = 0.0
            q = torch.from_numpy(Eq[rows[s:s + chunk]]).to(dev)
            out[s:s + len(ii)] = torch.sigmoid(net(q, C, M)).cpu().numpy()
            if tok_mode is not None and getattr(net, "last_w", None) is not None and variant == "ctx":
                w = net.last_w; same = (tok_mode[ctx_last_idx(ctx, ii, on)] == mode_rows[ii][:, None]) & ~M
                nv = (~M).sum(1).float(); fs = same.sum(1).float()
                ok = (fs > 0) & (fs < nv)
                if ok.any():
                    ws = (w * same.float()).sum(1) / w.sum(1).clamp(min=1e-6)
                    ratio.append(((ws / (fs / nv)).clamp(max=50))[ok].cpu().numpy())
    net.train()
    return out, (float(np.mean(np.concatenate(ratio))) if ratio else float("nan"))


def ctx_last_idx(ctx, ii, on):
    """gather 가 쓴 토큰 번호 (같은 모드 진단용, select 규칙 재현)."""
    lo = ctx.lo[ii]; c = ctx.c[ii]; n = c - lo; rec = c[:, None] - ctx.k + ctx.ar[None, :]
    if ctx.select == "uniform":
        uni = lo[:, None] + (ctx.ar[None, :] * n[:, None]) // ctx.k; idx = ctx.torch.where((n > ctx.k)[:, None], uni, rec)
    else:
        idx = rec
    return ctx.torch.where(idx >= lo[:, None], idx, ctx.torch.full_like(idx, ctx.null))


def roc_mean(Y, W, p, rows=None):
    from sklearn.metrics import roc_auc_score
    r = np.arange(len(Y)) if rows is None else rows; vals = []
    for j in range(NH):
        k = r[W[r, j] > 0]
        vals.append(roc_auc_score(Y[k, j], p[k, j]) if len(k) > 20 and Y[k, j].min() != Y[k, j].max() else np.nan)
    return np.array(vals)


# ---------------------------------------------------------------- 메인
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--world", required=True); ap.add_argument("--fleet", default=None, help="v6 플릿 세계 npz: 주면 베이스로 학습하고 플릿 trip_order ≥ --adapt-trips 행을 채점 (v6 비교용)")
    ap.add_argument("--adapt-trips", type=int, default=24); ap.add_argument("--cache", default=None); ap.add_argument("--out", default=None); ap.add_argument("--tag", default="")
    ap.add_argument("--input", default="v5"); ap.add_argument("--hide", default="", help="질의·토큰 모두에서 NaN 으로 가리는 피처 그룹 (예: G2 또는 G2,G5)")
    ap.add_argument("--stage1", choices=["mlp", "linear"], default="mlp"); ap.add_argument("--d1", type=int, default=32); ap.add_argument("--body-steps", type=int, default=4000)
    ap.add_argument("--scale", choices=["std", "quantile"], default="std", help="입력 스케일: 표준화(±5 클립) / 분위수→정규")
    ap.add_argument("--key-feats", default="", help="요약 E 옆에 그대로 붙일 상황 피처 (검색 키 보강): 'auto' 또는 쉼표 이름. 질의·토큰 둘 다에 붙는다")
    ap.add_argument("--embed", choices=["linear", "ple"], default="linear", help="1단계 수치 임베딩: 선형 / 구간 선형(PLE, --bins 구간)"); ap.add_argument("--bins", type=int, default=8)
    ap.add_argument("--stage2", choices=["attn", "enc", "gru", "hybrid", "mean_dev"], default="attn")
    ap.add_argument("--k", type=int, default=512); ap.add_argument("--select", choices=["recent", "uniform"], default="recent"); ap.add_argument("--time", choices=["none", "rides"], default="none")
    ap.add_argument("--neg-per-ride", type=int, default=2)
    ap.add_argument("--d2", type=int, default=64); ap.add_argument("--layers", type=int, default=2); ap.add_argument("--heads", type=int, default=4); ap.add_argument("--dropout", type=float, default=0.1)
    ap.add_argument("--steps", type=int, default=10000); ap.add_argument("--batch", type=int, default=128); ap.add_argument("--lr", type=float, default=1e-3); ap.add_argument("--wd", type=float, default=1e-3)
    ap.add_argument("--eval-every", type=int, default=1000); ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--val-persons", type=int, default=20); ap.add_argument("--test-persons", type=int, default=40)
    ap.add_argument("--threads", type=int, default=8); ap.add_argument("--device", default="cpu")
    a = ap.parse_args()
    torch, NETS = C6.make_torch(); torch.set_num_threads(a.threads); torch.manual_seed(a.seed); rng = np.random.default_rng(a.seed)
    global PRED_CHUNK
    if a.stage2 == "enc":                                            # 토큰끼리 어텐션은 메모리가 k² 라 채점 묶음을 줄이고 MHA 빠른 경로를 끈다
        PRED_CHUNK = 1024
        if hasattr(torch.backends, "mha"):
            torch.backends.mha.set_fastpath_enabled(False)
    if torch.cuda.is_available():
        torch.backends.cuda.matmul.allow_tf32 = True; torch.backends.cudnn.allow_tf32 = True
    dev = torch.device(a.device if a.device == "cpu" or torch.cuda.is_available() else "cpu")
    wp = pathlib.Path(a.world); cache = pathlib.Path(a.cache or wp.parent / "cache"); cache.mkdir(parents=True, exist_ok=True)
    hide = [g for g in a.hide.split(",") if g]; htag = ("-hide" + "".join(hide)) if hide else ""
    s1tag = f"{a.stage1}{a.d1}{'q' if a.scale == 'quantile' else ''}{('ple' + str(a.bins)) if a.embed == 'ple' else ''}{'kf' if a.key_feats else ''}"
    name = f"ctx7{'fl' if a.fleet else ''}_{a.input}{htag}_{s1tag}-{a.stage2}_k{a.k}_{a.select}{'_t' if a.time != 'none' else ''}_n{a.neg_per_ride}{('-' + a.tag) if a.tag else ''}_s{a.seed}"
    out = pathlib.Path(a.out or wp.parent / name); out.mkdir(parents=True, exist_ok=True)
    T = C6.Timer(); logf = open(out / "log.txt", "w")
    def log(s): print(s, flush=True); logf.write(s + "\n"); logf.flush()
    log(f"# {name}  장치 {dev}" + ("" if str(dev) == a.device else f" (요청 {a.device} 불가)"))

    # ---- 세계, 입력, 사람 분할
    d = C6.load_world(a.world); cols = C6.input_cols(a.input)
    X = d["X"][:, cols]; Y, W, t, rid, pid, order = d["Y"], d["W"], d["t"], d["ride_id"], d["person_id"], d["ride_order"]
    mode = d["mode"].astype(np.int64) if "mode" in d else np.zeros(len(Y), np.int64); n_modes = int(mode.max()) + 1
    is_fleet = np.zeros(len(Y), bool); trip_order = np.zeros(len(Y), np.int64)
    if a.fleet:                                                      # v6 방식: 베이스로 학습, 플릿(가족·결측) trip_order ≥ adapt 행 채점
        f = C6.load_world(a.fleet); assert "trip_order" in f, "플릿 세계가 아니다"
        X = np.concatenate([X, f["X"][:, cols]]); Y = np.concatenate([Y, f["Y"]]); W = np.concatenate([W, f["W"]]); t = np.concatenate([t, f["t"]])
        rid = np.concatenate([rid, f["ride_id"] + 10 ** 7]); pid = np.concatenate([pid, f["person_id"]]); order = np.concatenate([order, f["ride_order"]])
        mode = np.concatenate([mode, np.zeros(len(f["Y"]), np.int64)]); n_modes = 1
        is_fleet = np.concatenate([is_fleet, np.ones(len(f["Y"]), bool)]); trip_order = np.concatenate([trip_order, f["trip_order"]]); del f
    for g in hide:
        hc = [i for i, c_ in enumerate(cols) if W6.GROUPS[c_] == g]; X[:, hc] = np.nan
    # 상황 표시 (v5 로 보이는 것만; 가려졌거나 결측이면 NaN → 버킷에서 빠짐) — 분위수 변환 전 원 단위로
    def col_of(nm):
        j = [i for i, c_ in enumerate(cols) if W6.NAMES[c_] == nm]; return X[:, j[0]] if j else np.full(len(X), np.nan, np.float32)
    situ = {"고속 (속도 1분 ≥ 80 km/h)": col_of("speed_mean_1m") / W6.sim.KMH >= 80.0, "더운 실내 (≥ 27 °C)": col_of("cabin_temp") >= 27.0, "뒷좌석": col_of("seat_row") >= 0.5}
    situ_ok = {k_: np.isfinite(v_) for k_, v_ in {"고속 (속도 1분 ≥ 80 km/h)": col_of("speed_mean_1m"), "더운 실내 (≥ 27 °C)": col_of("cabin_temp"), "뒷좌석": col_of("seat_row")}.items()}
    persons = np.unique(pid[~is_fleet]); perm = np.random.default_rng(0).permutation(persons)
    if a.fleet:
        test_p = np.array([], np.int64); val_p = perm[:a.val_persons]
        is_val = np.isin(pid, val_p) & ~is_fleet; is_test = is_fleet & (trip_order >= a.adapt_trips); is_tr = ~is_fleet & ~is_val
    else:
        test_p, val_p = perm[:a.test_persons], perm[a.test_persons:a.test_persons + a.val_persons]
        is_test, is_val = np.isin(pid, test_p), np.isin(pid, val_p); is_tr = ~is_test & ~is_val
    if a.scale == "quantile":                                       # 분위수 → 정규분포 (학습 행으로 맞춤, NaN 유지)
        from sklearn.preprocessing import QuantileTransformer
        qt = QuantileTransformer(n_quantiles=1000, output_distribution="normal", subsample=200000, random_state=0).fit(X[is_tr])
        X = np.concatenate([qt.transform(X[i:i + 200000]) for i in range(0, len(X), 200000)]).astype(np.float32)
    mu, sd = C6.stats(X[is_tr])
    log(f"세계 {wp}{' + 플릿 ' + a.fleet if a.fleet else ''}: 행 {len(X):,} 사람 {len(np.unique(pid))} (학습 {len(persons) - len(test_p) - len(val_p)} / 검증 {len(val_p)} / 시험 {len(np.unique(pid[is_test]))}{' 플릿' if a.fleet else ''}) 모드 {n_modes} 입력 {len(cols)}열{' 가림 ' + ','.join(hide) if hide else ''} · 스케일 {a.scale} ({T():.0f}s)")

    # ---- 1단계 몸통 + 요약 E
    import fcntl; lockf = open(cache / ".lock", "w"); fcntl.flock(lockf, fcntl.LOCK_EX)      # 같은 세계를 동시에 돌릴 때 몸통·E 캐시는 한 번만
    body_name = f"{a.stage1}{a.d1}_{a.input}{htag}{'_q' if a.scale == 'quantile' else ''}{('_ple' + str(a.bins)) if a.embed == 'ple' else ''}{'_fl' if a.fleet else ''}"; m0 = None
    if a.stage1 == "mlp":
        fp = cache / f"body_{body_name}.pt"
        if a.embed == "ple":
            Ztr = C6.standardize(X[is_tr][rng.choice(is_tr.sum(), min(200000, int(is_tr.sum())), replace=False)], mu, sd)
            edges = np.stack([np.quantile(Ztr[:, j], np.linspace(0, 1, a.bins + 1)) for j in range(Ztr.shape[1])]); del Ztr
            m0 = make_ple_mlp(torch, len(cols), a.d1, edges)
        else:
            m0 = NETS["mlp"](len(cols), d=a.d1)
        if fp.exists():
            m0.load_state_dict(torch.load(fp, map_location="cpu")); m0.eval(); log(f"1단계 몸통 재사용 {fp}")
        else:
            Z, P = C6.standardize(X[is_tr], mu, sd), np.isfinite(X[is_tr]); Zv, Pv = C6.standardize(X[is_val], mu, sd), np.isfinite(X[is_val])
            m0, bs = C6.train_net(torch, m0, Z, P, Y[is_tr], W[is_tr], steps=a.body_steps, seed=0, val=(Zv, Pv, Y[is_val], W[is_val]), log=log)
            torch.save(m0.state_dict(), fp); log(f"1단계 몸통 학습 끝 (최고 스텝 {bs}, {T():.0f}s)"); del Z, P, Zv, Pv
    ck = cache / f"E_{body_name}.npy"
    if ck.exists():
        E = np.load(ck)
    else:
        if m0 is None:
            E = np.hstack([C6.standardize(X, mu, sd), np.isfinite(X).astype(np.float32)]).astype(np.float32)
        else:
            E = np.concatenate([C6.net_embed(torch, m0, C6.standardize(X[i:i + 100000], mu, sd), np.isfinite(X[i:i + 100000])) for i in range(0, len(X), 100000)])
        np.save(ck, E)
    fcntl.flock(lockf, fcntl.LOCK_UN); lockf.close()
    p_common = None
    if m0 is not None:
        p_common = np.concatenate([C6.net_predict(torch, m0, C6.standardize(X[i:i + 100000], mu, sd), np.isfinite(X[i:i + 100000])) for i in range(0, len(X), 100000)])
    kf_names = []
    if a.key_feats:
        want = KEY_AUTO if a.key_feats == "auto" else a.key_feats.split(",")
        kc = [(nm, i) for nm in want for i, c_ in enumerate(cols) if W6.NAMES[c_] == nm]; kf_names = [nm for nm, _ in kc]
        Kf = np.nan_to_num(C6.standardize(X[:, [i for _, i in kc]], mu[[i for _, i in kc]], sd[[i for _, i in kc]])).astype(np.float32)
        E = np.concatenate([E, Kf], 1); log(f"검색 키 보강: 요약 {E.shape[1] - len(kf_names)} + 상황 피처 {len(kf_names)}개 ({', '.join(kf_names)})")
    de = E.shape[1]
    # 모드 탐침: 요약 E (그리고 원 입력) 만으로 모드를 맞출 수 있나 — 못 맞추면 2단계가 '같은 모드'를 고를 재료가 없는 것
    probe = {"chance": 1.0 / n_modes}
    if n_modes > 1:
        from sklearn.linear_model import LogisticRegression
        tr_i = rng.choice(np.flatnonzero(is_tr), min(100000, int(is_tr.sum())), replace=False); te_i = rng.choice(np.flatnonzero(is_test), min(50000, int(is_test.sum())), replace=False)
        for nm, F in (("E", E), ("X", np.nan_to_num(C6.standardize(X, mu, sd)))):
            lr = LogisticRegression(max_iter=300, C=1.0).fit(F[tr_i], mode[tr_i]); probe[nm] = float((lr.predict(F[te_i]) == mode[te_i]).mean())
        log(f"모드 탐침 정확도 (시험 행): 요약 E {probe['E']:.3f} · 원 입력 X {probe['X']:.3f} · 우연 {probe['chance']:.3f}")
    del X

    # ---- 토큰과 색인 (학습 사람 토큰은 학습 때, 시험 사람 토큰은 채점 때 — 같은 표에 두고 사람으로 분리된다)
    tok, tok_mode = build_tokens(Y, W, t, rid, pid, order, mode, rng, a.neg_per_ride)
    n_time = 2 if a.time == "rides" else 0; nf_tok = de + NH + 1 + n_time
    ix_p = TokIndex(tok, tok[:, 0], pid, order)                                              # 사람 단위
    ix_m = TokIndex(tok, tok[:, 0] * 16 + tok_mode, pid * 16 + mode, order)                 # 사람 × 모드 (오라클)
    ctx = Ctx(torch, dev, E, tok, ix_p, a.k, a.select, a.time == "rides", n_time)
    ctx_m = Ctx(torch, dev, E, tok, ix_m, a.k, a.select, a.time == "rides", n_time) if n_modes > 1 else None
    tok_mode_dev = torch.from_numpy(np.concatenate([tok_mode[ix_p.perm], [-1]])).to(dev); mode_dev = torch.from_numpy(mode).to(dev)
    tpp = len(tok) / len(persons); log(f"토큰 {len(tok):,} (사람당 {tpp:.0f}, 탑승당 {len(tok) / len(np.unique(rid)):.1f}) · 토큰 차원 {nf_tok} · k {a.k} ({T():.0f}s)")

    # ---- 2단계 학습
    net = make_net(torch, de, nf_tok, d=a.d2, layers=a.layers, heads=a.heads, dropout=a.dropout, stage2=a.stage2).to(dev)
    n_par = sum(p_.numel() for p_ in net.parameters()); log(f"2단계 {a.stage2}: 파라미터 {n_par:,}")
    opt = torch.optim.AdamW(net.parameters(), lr=a.lr, weight_decay=a.wd); sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=a.steps, eta_min=a.lr / 20)
    elig_tr = np.flatnonzero((W.max(1) > 0) & is_tr); pw = C6.pos_weight(torch, Y, W).to(dev)
    val_rows = np.flatnonzero(is_val & (W.max(1) > 0)); val_rows = np.sort(rng.choice(val_rows, size=min(30000, len(val_rows)), replace=False))
    Yd = torch.from_numpy(Y.astype(np.float32)).to(dev); Wd = torch.from_numpy(W).to(dev); Ed = torch.from_numpy(E).to(dev); Od = torch.from_numpy(order).to(dev)
    best = (-1.0, 0, None); t0 = T(); net.train()
    for s in range(a.steps):
        ii = torch.from_numpy(rng.choice(elig_tr, a.batch)).to(dev)
        C, M = ctx.gather(ii, Od[ii])
        nv = (~M).sum(1, keepdim=True).float(); kk = torch.randint(0, a.k + 1, (a.batch, 1), device=dev).float()     # 문맥 수를 0~k 로 흔든다
        drop = (~M) & (torch.rand(a.batch, a.k, device=dev) >= (kk / nv.clamp(min=1)))
        M = M | drop; C = C * (~M).unsqueeze(-1).float()
        logit = net(Ed[ii], C, M); y = Yd[ii]; w = Wd[ii]
        bce = torch.nn.functional.binary_cross_entropy_with_logits(logit, y, pos_weight=pw, reduction="none")
        loss = (bce * w).sum() / w.sum().clamp(min=1.0); opt.zero_grad(); loss.backward(); torch.nn.utils.clip_grad_norm_(net.parameters(), 5.0); opt.step(); sched.step()
        if (s + 1) % a.eval_every == 0 or s + 1 == a.steps:
            pv, _ = predict(torch, net, ctx, E, val_rows, order)
            sc = float(np.nanmean(roc_mean(Y[val_rows], W[val_rows], pv)))
            if sc > best[0]:
                best = (sc, s + 1, {k_: v.detach().cpu().clone() for k_, v in net.state_dict().items()})
            log(f"  스텝 {s + 1} 손실 {loss.item():.3f} 검증 ROC {sc:.4f} (최고 {best[0]:.4f} @ {best[1]}) ({T():.0f}s)")
    t_train = T() - t0
    if best[2] is None:  # --steps 가 검증 주기보다 짧으면 마지막 가중치
        best = (float("nan"), a.steps, {k_: v.detach().cpu().clone() for k_, v in net.state_dict().items()})
    net.load_state_dict(best[2]); torch.save(best[2], out / f"ctx_{a.stage2}_k{a.k}.pt"); log(f"조기 종료 스텝 {best[1]} (검증 {best[0]:.4f}) 학습 {t_train / 60:.1f}분")

    # ---- 채점 (시험 사람)
    te = np.flatnonzero(is_test & (W.max(1) > 0)); Yt, Wt = Y[te], W[te]
    preds = {}
    t1 = time.time(); preds["ctx"], same_ratio = predict(torch, net, ctx, E, te, order, tok_mode=tok_mode_dev, mode_rows=mode_dev); ms_row = (time.time() - t1) / len(te) * 1e3
    preds["empty"], _ = predict(torch, net, ctx, E, te, order, "empty")
    preds["nolabel"], _ = predict(torch, net, ctx, E, te, order, "nolabel"); preds["nosit"], _ = predict(torch, net, ctx, E, te, order, "nosit")
    tp = np.unique(pid[te]); sw = {int(p_): int(tp[(i_ + 1) % len(tp)]) for i_, p_ in enumerate(tp)}       # 다른 시험 사람의 이력 전부
    pos_p = {int(p_): i_ for i_, p_ in enumerate(tp)}
    sp = np.array([sw[int(p_)] for p_ in pid[te]]); first_row = {int(p_): int(np.flatnonzero(pid == p_)[0]) for p_ in tp}
    fr = np.array([first_row[int(p_)] for p_ in sp]); lo_s = torch.from_numpy(ix_p.lo[fr]).to(dev); c_s = torch.from_numpy(ix_p.hi[fr]).to(dev)
    preds["swap"], _ = predict(torch, net, ctx, E, te, order, "swap", swap_lo=lo_s, swap_c=c_s)
    if ctx_m is not None:
        preds["samemode"], _ = predict(torch, net, ctx_m, E, te, order)
    if p_common is not None:
        preds["common"] = p_common[te]
    log(f"채점 끝 ({T():.0f}s) · 2단계 추론 {ms_row:.3f} ms/행 · 어텐션 같은 모드 가중치 비율 {same_ratio:.2f}")

    # ---- 버킷: 이력 길이, 같은 모드 마지막 참고 거리, 모드 전환
    ordt = order[te]; pidt = pid[te]; BIG = 10 ** 6
    key = pid * ORD_MUL + order; _, first = np.unique(key, return_index=True); ride_p, ride_o, ride_m = pid[first], order[first], mode[first]
    info = {}; last = {}; prev = {}                                      # 탑승마다 (같은 모드 마지막 참고까지 거리, 모드 전환 직후인가)
    for i in np.lexsort((ride_o, ride_p)):
        p_, o_, m_ = int(ride_p[i]), int(ride_o[i]), int(ride_m[i]); L = last.setdefault(p_, {})
        info[p_ * ORD_MUL + o_] = (o_ - L[m_] if m_ in L else BIG, prev.get(p_) is not None and prev[p_] != m_)
        L[m_] = o_; prev[p_] = m_
    kt = pidt * ORD_MUL + ordt
    dist_same = np.array([info[int(k_)][0] for k_ in kt]); switch = np.array([info[int(k_)][1] for k_ in kt])
    hist_b = [("0", ordt == 0), ("1~9", (ordt >= 1) & (ordt <= 9)), ("10~49", (ordt >= 10) & (ordt <= 49)), ("50~149", (ordt >= 50) & (ordt <= 149)), ("150+", ordt >= 150)]
    far_b = [("1", dist_same == 1), ("2~4", (dist_same >= 2) & (dist_same <= 4)), ("5~19", (dist_same >= 5) & (dist_same <= 19)), ("20~99", (dist_same >= 20) & (dist_same <= 99)),
             ("100+", (dist_same >= 100) & (dist_same < BIG)), ("없음", dist_same >= BIG)]
    sw_b = [("전환 직후", switch & (ordt > 0)), ("같은 모드 연속", ~switch & (ordt > 0))]
    names = {"common": "1단계 공통 (무문맥)", "empty": "문맥 0개", "ctx": f"문맥 {a.stage2} k{a.k}", "swap": "진단 · 다른 사람 이력", "nolabel": "진단 · 라벨 제거",
             "nosit": "진단 · 상황 요약 제거", "samemode": "오라클 · 같은 모드 토큰만"}
    metrics = {"name": name, "args": vars(a), "params": n_par, "best_step": best[1], "val_roc": best[0], "train_min": t_train / 60, "ms_per_row": ms_row,
               "same_mode_attn_ratio": same_ratio, "mode_probe": probe, "n_test_rows": int(len(te)), "tokens_per_person": tpp, "overall": {}, "hist": {}, "far": {}, "switch": {}}
    M = [f"# {name}", "", f"2단계 {a.stage2} 파라미터 {n_par:,} · 조기 종료 @ {best[1]} (검증 {best[0]:.4f}) · 학습 {t_train / 60:.1f}분 · 추론 {ms_row:.3f} ms/행 · "
         f"시험 행 {len(te):,} · 어텐션 같은 모드 가중치 비율 {same_ratio:.2f} (1 = 무작위) · 모드 탐침 E {probe.get('E', float('nan')):.3f} / X {probe.get('X', float('nan')):.3f} (우연 {probe['chance']:.3f})", "",
         "## 표 1 · 전체 (시험 사람, 자격 행)", "", "| 모델 | " + " | ".join(HEADS) + " | 평균 |", "|---|---|---|---|---|"]
    for n_, p_ in preds.items():
        v = roc_mean(Yt, Wt, p_); metrics["overall"][n_] = v.tolist()
        M.append(f"| {names[n_]} | " + " | ".join(f"{x:.3f}" for x in v) + f" | {np.nanmean(v):.3f} |")
    def table(title, buckets, target):
        M.extend(["", f"## {title} (ROC 평균 3장치: 공통 → 문맥 0개 → 문맥 → 같은 모드 오라클)", "", "| 버킷 | 행 수 | 공통 | 문맥 0개 | 문맥 | 오라클 |", "|---|---|---|---|---|---|"])
        for bn, bm in buckets:
            rows = np.flatnonzero(bm); vals = {}
            for n_ in ("common", "empty", "ctx", "samemode"):
                vals[n_] = float(np.nanmean(roc_mean(Yt, Wt, preds[n_], rows))) if n_ in preds and len(rows) > 50 else float("nan")
            target[bn] = {"rows": int(len(rows)), **vals}
            M.append(f"| {bn} | {len(rows):,} | " + " | ".join("-" if np.isnan(vals[n_]) else f"{vals[n_]:.3f}" for n_ in ("common", "empty", "ctx", "samemode")) + " |")
    table("표 2 · 앞선 탑승 수별", hist_b, metrics["hist"]); table("표 3 · 같은 모드 마지막 참고까지 거리 (탑승 수)별 — 먼 참고", far_b, metrics["far"]); table("표 4 · 모드 전환", sw_b, metrics["switch"])
    # ---- 상황별 (v5 로 보이는 상황: 고속·더운 실내·뒷좌석) — 기존(v6) 세계에서 '모드' 노릇을 하는 것들
    SHORT = {"고속 (속도 1분 ≥ 80 km/h)": "hw", "더운 실내 (≥ 27 °C)": "hot", "뒷좌석": "rear"}; RIDE_SHARE = {"hw": 0.2, "hot": 0.5, "rear": 0.5}
    uk, inv = np.unique(key, return_inverse=True); metrics["situ"] = {}; situ_ratio = {}
    for sname, srow in situ.items():
        sh = SHORT[sname]; ok = situ_ok[sname]
        if ok[te].sum() < 100:
            continue
        cnt = np.bincount(inv, weights=ok.astype(float), minlength=len(uk)); num = np.bincount(inv, weights=(srow & ok).astype(float), minlength=len(uk))
        ride_flag = np.where(cnt > 0, num / np.maximum(cnt, 1), np.nan) >= RIDE_SHARE[sh]                       # 탑승 단위 상황 표시
        rp, ro = pid[first], order[first]; dist = {}; lastp = {}
        for i in np.lexsort((ro, rp)):
            p_, o_ = int(rp[i]), int(ro[i]); dist[p_ * ORD_MUL + o_] = (o_ - lastp[p_]) if p_ in lastp else BIG
            if ride_flag[i]:
                lastp[p_] = o_
        dd = np.array([dist[int(k_)] for k_ in kt]); st_ = srow[te] & ok[te]; no_ = ~srow[te] & ok[te]
        b1 = [(f"{sname} 행", st_), (f"그 외 행", no_)]
        b2 = [(f"{sh} 행 · 직전 {sh} 탑승 거리 1", st_ & (dd == 1)), (f"{sh} 행 · 2~4", st_ & (dd >= 2) & (dd <= 4)), (f"{sh} 행 · 5~19", st_ & (dd >= 5) & (dd <= 19)),
              (f"{sh} 행 · 20+", st_ & (dd >= 20) & (dd < BIG)), (f"{sh} 행 · 없음 (첫 {sh} 탑승)", st_ & (dd >= BIG))]
        metrics["situ"][sh] = {}; table(f"표 5-{sh} · 상황 {sname}", b1 + b2, metrics["situ"][sh])
        if getattr(net, "last_w", None) is not None or a.stage2 in ("attn", "hybrid"):
            g_row = np.where(ok, srow.astype(np.int64), -2); tok_g = torch.from_numpy(np.concatenate([g_row[tok[ix_p.perm, 2]], [-1]])).to(dev)
            _, r_ = predict(torch, net, ctx, E, te, order, tok_mode=tok_g, mode_rows=torch.from_numpy(g_row).to(dev)); situ_ratio[sh] = r_
            M.append(f"\n어텐션 가중치 비율 (같은 {sh} 상태 토큰 ÷ 그 비중, 1 = 무작위): {r_:.2f}")
    metrics["situ_attn_ratio"] = situ_ratio
    M += ["", "읽기: 표 3 의 먼 버킷(20+)에서 문맥이 공통보다 오르고 오라클에 가까우면 '먼 같은 상황 과거를 골라 읽은 것'. GRU 와 attn 을 같은 세계·같은 k 로 비교한다. "
          "표 5 는 기존 세계에서 모드 노릇을 하는 상황별 — 평균에서 안 보이던 차이가 고속·더위 행이나 '직전 같은 상황이 먼' 행에 숨어 있는지 본다.", "", f"총 {T():.0f}초"]
    txt = "\n".join(M); log("\n" + txt); (out / "summary.md").write_text(txt + "\n")
    (out / "metrics.json").write_text(json.dumps(metrics, indent=1, ensure_ascii=False, default=float))
    world_tag = ("v6fl_" if a.fleet else "") + wp.parent.name
    flat = {"world": world_tag, "input": a.input, "hide": a.hide, "stage1": s1tag, "stage2": a.stage2, "k": a.k, "select": a.select, "time": a.time,
            "neg": a.neg_per_ride, "seed": a.seed, "tag": a.tag, "kf": len(kf_names), "d2": a.d2, "heads": a.heads, "dropout": a.dropout, "params": n_par, "best_step": best[1], "val_roc": round(best[0], 4), "train_min": round(t_train / 60, 1),
            "ms_per_row": round(ms_row, 3), "same_mode_ratio": round(same_ratio, 3) if same_ratio == same_ratio else "",
            "probe_E": round(probe.get("E", float("nan")), 3), "probe_X": round(probe.get("X", float("nan")), 3)}
    for n_, v in metrics["overall"].items():
        flat[f"roc_{n_}"] = round(float(np.nanmean(v)), 4)
    for key_, pre in (("hist", "h"), ("far", "far"), ("switch", "sw")):
        for bn, v in metrics[key_].items():
            flat[f"{pre}_{bn}_ctx"] = "" if np.isnan(v["ctx"]) else round(v["ctx"], 4); flat[f"{pre}_{bn}_common"] = "" if np.isnan(v["common"]) else round(v["common"], 4)
            flat[f"{pre}_{bn}_n"] = v["rows"]
    for sh, dct in metrics["situ"].items():
        for bn, v in dct.items():
            short = bn.replace(f"{sh} 행 · ", "").replace("직전 " + sh + " 탑승 거리 ", "d").replace(" 행", "").split(" (")[0]
            flat[f"s_{sh}_{short}_ctx"] = "" if np.isnan(v["ctx"]) else round(v["ctx"], 4); flat[f"s_{sh}_{short}_common"] = "" if np.isnan(v["common"]) else round(v["common"], 4); flat[f"s_{sh}_{short}_n"] = v["rows"]
        flat[f"ratio_{sh}"] = round(situ_ratio[sh], 3) if sh in situ_ratio and situ_ratio[sh] == situ_ratio[sh] else ""
    (out / "oneline.csv").write_text(",".join(flat) + "\n" + ",".join(str(v) for v in flat.values()) + "\n")


if __name__ == "__main__":
    main()
