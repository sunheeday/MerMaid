#!/usr/bin/env python3
"""개인 보정 B (docs/controller/DATA_LOGGING.md §12): 주행 파일로 사람(폰)마다 a, b를 맞춘다.

    python3 ml/personal/fit_calib.py --root server/data/rides [--rides id1,id2] [--note "..."]
    → ml/out/runs/vNNN/ (kind "calib")
        calib.json             사람마다 a, b, 주행 수, 라벨 수, leave-one-ride-out 이득, 채택 여부
        calib/<phone>.json     Pi state/calib/에 넣을 파일 (채택 안 된 사람은 a=1, b=0으로, 예전 값을 지우게)
    내려보내기: python3 server/tools/collect_logs.py --push-calib vNNN --state <Pi의 state 폴더>

식: p' = σ(a·logit(p) + b). k = 2..6에 같은 a, b → 순서 유지. C++은 controller/common/calib.hpp (같은 식).
입력 p는 항상 보정 전 모델 출력 (주행 파일 infer.p2..p6, P3가 보정 전 값을 남긴다).

학습 신호 (§12-3)
  MISC (label 표)       주기 t의 y_k = (t, t+300] 안에 MISC >= k. 그 구간에 그 사람의 라벨이 없으면 안 씀.
                         가중치: 라벨 창 하나가 1. 300초 창이 주기(10초)마다 겹쳐 한 라벨이 약 30주기에 들어가므로
                         주기 관측 하나는 1 × 주기/300 (안 그러면 독립 관측 수를 30배로 착각해 사전분포가 안 듣는다)
  빠른 끄기             자동 ON(decision changed=1, on=1) 뒤 30초 안에 그 기기를 OVR off한 사람이, 그 뒤 300초 안에
                         라벨이 있고 모두 MISC < input_k → 끈 시점 주기의 y_{input_k} = 0. 가중치 0.5
  자기 폰 직접 켜기     자기 폰(Tier 3)이 꺼져 있을 때 OVR on, 그 뒤 300초 안에 라벨이 없으면 y_{input_k} = 1. 가중치 0.5
추정: 계층 MAP. log a ~ N(0, σa²), b ~ N(0, σb²). σa, σb는 주행 2개 이상인 사람이 3명 이상이면 그 사람들의
      MAP 값으로 몇 번 다시 맞추고(경험적 베이즈), 아니면 기본값. 기록이 적은 사람은 a=1, b=0 가까이 머문다.
안전 (§12-4): a ∈ [0.5, 2.0], |b| <= 4, 각 Tier 문턱 ± 0.05의 p에서 p − p' <= 0.10 (넘으면 b를 올림).
채택: 주행 2개 이상이고, 한 주행씩 빼고 맞춘 가중 log loss가 a=1, b=0보다 좋을 때만.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import minimize

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "ml"))
from server.ridefile import PROBS, RideFile  # noqa: E402

CONFIG = REPO / "controller" / "config"
K = (2, 3, 4, 5, 6)
A_MIN, A_MAX, B_MAX, P_CLIP = 0.5, 2.0, 4.0, 1e-6  # calib.hpp와 같음
HORIZON_S = 300.0
W_MISC, W_OVR = 1.0, 0.5
SIGMA_A0, SIGMA_B0 = 0.25, 0.5  # 사람이 적을 때 사전분포 (log a, b)
SIGMA_A_RANGE, SIGMA_B_RANGE = (0.05, 0.5), (0.1, 1.0)
MAX_DROP, NEAR_TH = 0.10, 0.05
MIN_RIDES = 2


# ---------------- 식 (calib.hpp와 같음) ----------------
def logit(p):
    q = np.clip(np.asarray(p, dtype=float), P_CLIP, 1 - P_CLIP)
    return np.log(q / (1 - q))


def apply_calib(P, a, b):
    """P: (..., 5) 또는 스칼라. a=1, b=0이면 그대로, 범위 밖이면 그대로 (P3와 같음)"""
    P = np.asarray(P, dtype=float)
    if (a == 1.0 and b == 0.0) or not valid(a, b):
        return P.copy()
    return 1 / (1 + np.exp(-(a * logit(P) + b)))


def valid(a, b):
    return bool(np.isfinite(a) and np.isfinite(b) and A_MIN <= a <= A_MAX and abs(b) <= B_MAX)


# ---------------- 설정 ----------------
def load_config(config_dir=CONFIG):
    pol = json.loads((pathlib.Path(config_dir) / "policy.json").read_text())
    th = json.loads((pathlib.Path(config_dir) / "thresholds.json").read_text())
    tiers = {int(t): {"k": int(v["input_k"]), "th": float(th["base_th"][t])} for t, v in pol["tiers"].items()}
    return {"tiers": tiers, "quick_off_s": float(pol["quick_off_s"])}


# ---------------- 주행 파일 → 관측 ----------------
@dataclass
class Obs:
    """한 사람의 관측: z = logit(보정 전 p_k), y, 가중치, 주행 번호"""
    z: list = field(default_factory=list)
    y: list = field(default_factory=list)
    w: list = field(default_factory=list)
    ride: list = field(default_factory=list)
    n_misc: int = 0  # MISC 라벨이 붙은 주기 수
    n_ovr: int = 0   # 끄기·켜기 신호 수

    def add(self, p, y, w, ride):
        self.z.append(float(logit(p)))
        self.y.append(float(y))
        self.w.append(w)
        self.ride.append(ride)

    def arrays(self):
        return np.array(self.z), np.array(self.y), np.array(self.w), np.array(self.ride)


def _cycle_at(rows, t):
    """t 이전(같은 시각 포함) 마지막 주기 행. 없으면 None"""
    best = None
    for r in rows:
        if r["t"] <= t:
            best = r
        else:
            break
    return best


def _labels_in(labels, t0, t1):
    return [m for (t, m) in labels if t0 < t <= t1]


def ride_obs(path, cfg, out: dict[str, Obs]):
    """주행 파일 하나의 관측을 사람별로 out에 더한다. 반환: 이 주행에 관측이 있는 사람들"""
    with RideFile(path) as rf:
        rid = rf.ride_id
        infer = [r for r in rf.infer_rows() if r["phone"] != "default" and r["t"] is not None]
        labels_all = [r for r in rf.label_rows() if r["t"] is not None]
        decisions = rf.decision_rows()
        ctx = [r for r in rf.ctx_rows() if r["t"] is not None and r["kind"] == "OVR"]
    by_phone: dict[str, list] = {}
    for r in infer:
        if all(r[p] is not None for p in PROBS):
            by_phone.setdefault(r["phone"], []).append(r)
    for rows in by_phone.values():
        rows.sort(key=lambda r: r["t"])
    labels = {}
    for r in labels_all:
        labels.setdefault(r["phone"], []).append((r["t"], r["misc"]))
    seen = set()

    # MISC
    for phone, rows in by_phone.items():
        lab = labels.get(phone, [])
        if not lab:
            continue
        o = out.setdefault(phone, Obs())
        ts = np.array([r["t"] for r in rows])
        cycle = float(np.median(np.diff(ts))) if len(ts) > 1 else 10.0
        w_misc = W_MISC * min(cycle, HORIZON_S) / HORIZON_S
        for r in rows:
            ms = _labels_in(lab, r["t"], r["t"] + HORIZON_S)
            if not ms:
                continue
            o.n_misc += 1
            for k, p in zip(K, PROBS):
                o.add(r[p], max(ms) >= k, w_misc, rid)
            seen.add(phone)

    # 자동 ON과 그 직전 상태 (기기별, 시간순)
    auto_on: dict[str, list] = {}
    state: dict[str, list] = {}
    for d in sorted(decisions, key=lambda d: d["t"]):
        state.setdefault(d["device"], []).append((d["t"], d["on"], d["tier"]))
        if d["changed"] and d["on"]:
            auto_on.setdefault(d["device"], []).append((d["t"], d["tier"]))

    for c in ctx:
        try:
            v = json.loads(c["value"] or "{}")
            device, on = str(v["device"]), int(v["on"])
        except (ValueError, KeyError, TypeError):
            continue
        sender, t = c["sender"], c["t"]
        rows = by_phone.get(sender)
        if not rows:
            continue
        lab = labels.get(sender, [])
        after = _labels_in(lab, t, t + HORIZON_S)
        row = _cycle_at(rows, t)
        if row is None:
            continue
        if on == 0:
            ons = [(ton, tier) for (ton, tier) in auto_on.get(device, []) if ton <= t]
            if not ons or t - ons[-1][0] > cfg["quick_off_s"]:
                continue
            k = cfg["tiers"].get(ons[-1][1], {}).get("k")
            if k is None or not after or max(after) >= k:
                continue
            out.setdefault(sender, Obs()).add(row[PROBS[K.index(k)]], 0, W_OVR, rid)
        else:
            if device != sender or after:
                continue
            prev = [s for s in state.get(device, []) if s[0] <= t]
            if prev and prev[-1][1]:
                continue  # 이미 켜져 있었음
            k = cfg["tiers"][3]["k"]
            out.setdefault(sender, Obs()).add(row[PROBS[K.index(k)]], 1, W_OVR, rid)
        out[sender].n_ovr += 1
        seen.add(sender)
    return seen


# ---------------- 맞추기 ----------------
def nll(a, b, z, y, w):
    s = a * z + b
    return float(np.sum(w * (np.logaddexp(0, s) - y * s)))


def safe_b_floor(a, tiers):
    """문턱 ± 0.05의 p에서 p − σ(a·logit(p) + b) <= 0.10이 되는 가장 작은 b"""
    lo = -B_MAX
    for t in tiers.values():
        p = np.linspace(t["th"] - NEAR_TH, t["th"] + NEAR_TH, 201)
        p = p[(p - MAX_DROP > 0) & (p < 1)]
        if len(p):
            lo = max(lo, float(np.max(logit(p - MAX_DROP) - a * logit(p))))
    return lo


def fit_one(z, y, w, sa, sb, tiers):
    """MAP (log a, b) + 안전 한계. 반환 (a, b)"""
    if len(z) == 0:
        return 1.0, 0.0

    def f(x):
        u, b = x
        a = np.exp(u)
        s = a * z + b
        sig = 1 / (1 + np.exp(-s))
        g = w * (sig - y)
        val = np.sum(w * (np.logaddexp(0, s) - y * s)) + u * u / (2 * sa * sa) + b * b / (2 * sb * sb)
        return val, np.array([np.sum(g * a * z) + u / (sa * sa), np.sum(g) + b / (sb * sb)])

    r = minimize(f, np.zeros(2), jac=True, method="L-BFGS-B",
                 bounds=[(np.log(A_MIN), np.log(A_MAX)), (-B_MAX, B_MAX)])
    a, b = float(np.exp(r.x[0])), float(r.x[1])
    b = min(max(b, safe_b_floor(a, tiers)), B_MAX)
    return a, b


def fit_all(obs: dict[str, Obs], cfg):
    """사람마다 a, b, 채택 여부. 반환 (결과 dict, σa, σb)"""
    tiers = cfg["tiers"]
    data = {ph: o.arrays() for ph, o in obs.items()}
    rides = {ph: sorted(set(d[3].tolist())) for ph, d in data.items()}
    pool = [ph for ph in data if len(rides[ph]) >= MIN_RIDES]
    sa, sb = SIGMA_A0, SIGMA_B0
    if len(pool) >= 3:
        for _ in range(5):
            ab = [fit_one(*data[ph][:3], sa, sb, tiers) for ph in pool]
            u = np.log([a for a, _ in ab])
            bb = np.array([b for _, b in ab])
            sa = float(np.clip(np.sqrt(np.mean(u * u)), *SIGMA_A_RANGE))
            sb = float(np.clip(np.sqrt(np.mean(bb * bb)), *SIGMA_B_RANGE))
    out = {}
    for ph, (z, y, w, rd) in data.items():
        o = obs[ph]
        res = {"a": 1.0, "b": 0.0, "n_rides": len(rides[ph]), "n_labels": o.n_misc, "n_ovr": o.n_ovr,
               "loo_gain": None, "adopted": False}
        if len(rides[ph]) < MIN_RIDES:
            res["status"] = "few_rides"
            out[ph] = res
            continue
        loss_cal = loss_id = 0.0
        for r in rides[ph]:
            tr, te = rd != r, rd == r
            a, b = fit_one(z[tr], y[tr], w[tr], sa, sb, tiers)
            loss_cal += nll(a, b, z[te], y[te], w[te])
            loss_id += nll(1.0, 0.0, z[te], y[te], w[te])
        gain = (loss_id - loss_cal) / float(np.sum(w))
        res["loo_gain"] = gain
        if gain > 0:
            a, b = fit_one(z, y, w, sa, sb, tiers)
            res.update(a=a, b=b, adopted=True, status="adopted")
        else:
            res["status"] = "no_gain"
        out[ph] = res
    return out, sa, sb


def ride_paths(root, ids=None):
    root = pathlib.Path(root)
    if ids:
        return [root / f"{i}.sqlite" for i in ids]
    return sorted(root.glob("*.sqlite"))


def fit_paths(paths, cfg=None):
    cfg = cfg or load_config()
    obs: dict[str, Obs] = {}
    for p in paths:
        ride_obs(p, cfg, obs)
    res, sa, sb = fit_all(obs, cfg)
    return res, sa, sb


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def write_run(res, sa, sb, paths, note=""):
    import runs  # ml/runs.py (덮어쓰지 않는 새 버전 폴더)
    run = runs.new("calib", note=note, params={
        "sigma_a": sa, "sigma_b": sb, "horizon_s": HORIZON_S, "weights": {"misc": W_MISC, "ovr": W_OVR},
        "rides": [{"id": pathlib.Path(p).stem, "sha256": sha256_of(p)} for p in paths]})
    (run / "calib.json").write_text(json.dumps({"version": run.name, "sigma_a": sa, "sigma_b": sb, "phones": res},
                                               indent=1) + "\n")
    d = run / "calib"
    d.mkdir()
    for ph, r in res.items():
        (d / f"{ph}.json").write_text(json.dumps({"a": r["a"], "b": r["b"], "run": run.name,
                                                  "adopted": r["adopted"]}) + "\n")
    return run


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", default=str(REPO / "server" / "data" / "rides"), help="주행 파일 폴더")
    ap.add_argument("--rides", help="쉼표로 구분한 ride_id (기본: 폴더 전부)")
    ap.add_argument("--note", default="")
    a = ap.parse_args(argv)
    paths = ride_paths(a.root, a.rides.split(",") if a.rides else None)
    missing = [str(p) for p in paths if not p.is_file()]
    if missing or not paths:
        print(f"주행 파일 없음: {', '.join(missing) or a.root}", file=sys.stderr)
        return 2
    res, sa, sb = fit_paths(paths)
    run = write_run(res, sa, sb, paths, a.note)
    for ph, r in sorted(res.items()):
        gain = "-" if r["loo_gain"] is None else f"{r['loo_gain']:+.4f}"
        print(f"{ph:40s} a={r['a']:.3f} b={r['b']:+.3f} rides={r['n_rides']} labels={r['n_labels']} "
              f"ovr={r['n_ovr']} loo={gain} {r['status']}")
    print(f"σa={sa:.3f} σb={sb:.3f} -> {run}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
