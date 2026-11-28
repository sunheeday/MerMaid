"""축 A — 1단계(피처 간 어텐션)가 정당한가: 공통 모델 3종 × 입력 집합, 베이스 세계 학습 → 플릿 세계 뒤 24탑승 채점.

    python3 ml/experiments/v6/axis_a.py --base ml/out/v6/base_weak.npz --fleet ml/out/v6/fleet_weak.npz --out ml/out/v6/axis_a_weak \
        [--models gbm,mlp,ft] [--inputs v6,v5,-G11,-G12,-G13,-G14,-G15] [--seeds 0] [--steps 4000] [--gbm-stride 3] [--adapt-trips 20]

모델  gbm  LightGBM 3헤드 쌓기 (저장소 규제, 검증 사람으로 조기 종료) — 트리는 조합을 분기로 잡는다
      mlp  [표준화 ‖ 마스크] → 128 → 64 → 32 → 3  — 신경망이지만 입력에 따라 보는 방식이 안 바뀜
      ft   피처 토큰 트랜스포머 d=32 2층 → 3     — 피처 간 어텐션
입력  v6 전부(65) / v5 새 피처 없이(49) / -G1x 새 그룹 하나 뺌 / G1x v5 + 그 그룹만
학습  검증 사람 60명 떼고, 그룹 드롭아웃 복사본을 원본과 함께(행 2배). 같은 시드면 세 모델이 같은 행·같은 라벨을 본다.
판정  FT − MLP ≥ 0.02 이고 차 20대 짝지은 윌콕슨 p < 0.05 → 1단계 어텐션 정당. GBM ≥ FT 면 신경망 1단계 자체가 불필요.
출력  out/results.csv, out/per_car.csv, out/summary.md, 모델 파일 (있으면 다시 학습하지 않음)
"""
import argparse
import csv
import json
import pathlib
import sys

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import common6 as C6  # noqa: E402
import world6 as W6  # noqa: E402
HEADS = C6.HEADS


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True); ap.add_argument("--fleet", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--models", default="gbm,mlp,ft"); ap.add_argument("--inputs", default="v6,v5,-G11,-G12,-G13,-G14,-G15")
    ap.add_argument("--seeds", default="0"); ap.add_argument("--steps", type=int, default=4000); ap.add_argument("--batch", type=int, default=256)
    ap.add_argument("--gbm-stride", type=int, default=3, help="GBM 학습 행 간격 (10초 행이 겹치므로 3 = 30초마다)")
    ap.add_argument("--adapt-trips", type=int, default=24); ap.add_argument("--val-persons", type=int, default=60); ap.add_argument("--threads", type=int, default=4)
    a = ap.parse_args()
    out = pathlib.Path(a.out); out.mkdir(parents=True, exist_ok=True); T = C6.Timer()
    logf = open(out / "log.txt", "a")
    def log(s): print(s, flush=True); logf.write(s + "\n"); logf.flush()
    models = a.models.split(","); inputs = a.inputs.split(","); seeds = [int(s) for s in a.seeds.split(",")]
    torch, NETS = C6.make_torch(); torch.set_num_threads(a.threads)

    base = C6.load_world(a.base); fleet = C6.load_world(a.fleet)
    log(f"베이스 {base['world']}/{base['habit']} {len(base['t'])}행 사람 {len(np.unique(base['person_id']))} · 플릿 {len(fleet['t'])}행 차 {len(np.unique(fleet['vehicle_id']))} ({T():.0f}s)")
    is_val = C6.person_val_split(base["person_id"], a.val_persons, 0)
    te = fleet["trip_order"] >= a.adapt_trips; Yt, Wt, Vt = fleet["Y"][te], fleet["W"][te], fleet["vehicle_id"][te]
    Xd_full = W6.group_dropout(base["X"], base["ride_id"], np.random.default_rng(1))          # 드롭아웃 복사본 (모든 입력·모델 공통)

    res_p = out / "results.csv"; car_p = out / "per_car.csv"
    done = set()
    if res_p.exists():
        with open(res_p) as f:
            done = {(r["model"], r["input"], int(r["seed"])) for r in csv.DictReader(f)}
    else:
        res_p.write_text("model,input,seed,n_features,params_or_trees,train_s,best_step," + ",".join(f"roc_{h}" for h in HEADS) + ",avg\n")
        car_p.write_text("model,input,seed,vehicle," + ",".join(HEADS) + "\n")

    for inp in inputs:
        cols = C6.input_cols(inp)
        Xb = base["X"][:, cols]; Xd = Xd_full[:, cols]
        Xtr = np.vstack([Xb[~is_val], Xd[~is_val]]); Ytr = np.vstack([base["Y"][~is_val]] * 2); Wtr = np.vstack([base["W"][~is_val]] * 2)
        Xva, Yva, Wva = Xb[is_val], base["Y"][is_val], base["W"][is_val]
        Xte = fleet["X"][te][:, cols]
        mu, sd = C6.stats(Xb[~is_val]); Z, P = C6.standardize(Xtr, mu, sd), np.isfinite(Xtr); Zv, Pv = C6.standardize(Xva, mu, sd), np.isfinite(Xva)
        Zte, Pte = C6.standardize(Xte, mu, sd), np.isfinite(Xte)
        for model in models:
            for seed in seeds:
                if (model, inp, seed) in done:
                    continue
                log(f"[{inp} · {model} · 시드 {seed}] 피처 {len(cols)}개 ({T():.0f}s)"); t0 = T(); best = 0
                if model == "gbm":
                    import lightgbm as lgb
                    fp = out / f"gbm_{inp}_s{seed}.txt"
                    if fp.exists():
                        b = lgb.Booster(model_file=str(fp)); b.best_iteration = json.loads(fp.with_suffix(".json").read_text())["best_iteration"]
                    else:
                        st = a.gbm_stride; rng = np.random.default_rng(seed)
                        sel = np.zeros(len(Xtr), bool); sel[rng.integers(0, st)::st] = True                 # 행 간격
                        b = C6.train_gbm(Xtr[sel], Ytr[sel], Wtr[sel], Xva[::st], Yva[::st], Wva[::st], cols, a.threads, log=log)
                        b.save_model(str(fp)); fp.with_suffix(".json").write_text(json.dumps({"best_iteration": b.best_iteration}))
                    p = C6.gbm_predict(b, Xte); size = b.best_iteration; best = b.best_iteration
                else:
                    fp = out / f"{model}_{inp}_s{seed}.pt"; m = NETS[model](len(cols))
                    if fp.exists():
                        m.load_state_dict(torch.load(fp)); m.eval(); best = -1
                    else:
                        m, best = C6.train_net(torch, m, Z, P, Ytr, Wtr, steps=a.steps, batch=a.batch, seed=seed, val=(Zv, Pv, Yva, Wva), log=log)
                        torch.save(m.state_dict(), fp)
                    p = C6.net_predict(torch, m, Zte, Pte); size = sum(q.numel() for q in m.parameters())
                fl, per = C6.roc_cells(p, Yt, Wt, Vt)
                log(f"  → 플릿 ROC " + " / ".join(f"{h} {v:.3f}" for h, v in zip(HEADS, fl)) + f" · 평균 {np.nanmean(fl):.3f} · {T() - t0:.0f}s")
                with open(res_p, "a") as f:
                    f.write(f"{model},{inp},{seed},{len(cols)},{size},{T() - t0:.0f},{best}," + ",".join(f"{v:.4f}" for v in fl) + f",{np.nanmean(fl):.4f}\n")
                with open(car_p, "a") as f:
                    for v, vals in per.items():
                        f.write(f"{model},{inp},{seed},{v}," + ",".join("" if np.isnan(x) else f"{x:.4f}" for x in vals) + "\n")
    summarize(out, a); log(f"끝 ({T():.0f}s)")


def summarize(out, a=None):
    import pandas as pd
    R = pd.read_csv(out / "results.csv"); Cc = pd.read_csv(out / "per_car.csv")
    try:
        from scipy.stats import wilcoxon
    except Exception:
        wilcoxon = None
    M = [f"# 축 A — 공통 모델 × 입력 집합 (플릿 뒤 {a.adapt_trips if a else 24}탑승, 플릿 ROC)", "",
         "## 1. 요인표 (시드 평균, n = 시드 수)", "", "| 입력 | 피처 수 | 모델 | n | " + " | ".join(HEADS) + " | 평균 | 크기 |", "|---|---|---|---|---|---|---|---|---|"]
    for (inp, model), d in R.groupby(["input", "model"], sort=False):
        m = d[[f"roc_{h}" for h in HEADS]].mean(); sd = d[[f"roc_{h}" for h in HEADS]].std(ddof=0) if len(d) > 1 else None
        cells = [f"{m[f'roc_{h}']:.3f}" + (f" ± {sd[f'roc_{h}']:.3f}" if sd is not None else "") for h in HEADS]
        M.append(f"| {inp} | {int(d['n_features'].iloc[0])} | {model} | {len(d)} | " + " | ".join(cells) + f" | {d['avg'].mean():.3f} | {int(d['params_or_trees'].iloc[0]):,} |")
    M += ["", "크기: GBM 은 트리 수, 신경망은 파라미터 수.", ""]

    def paired(m1, i1, m2, i2, seed=0):
        A = Cc[(Cc.model == m1) & (Cc.input == i1) & (Cc.seed == seed)].set_index("vehicle"); B = Cc[(Cc.model == m2) & (Cc.input == i2) & (Cc.seed == seed)].set_index("vehicle")
        if not len(A) or not len(B):
            return None
        B = B.reindex(A.index); cells = []
        for h in HEADS:
            diff = (A[h] - B[h]).dropna(); p = ""
            if wilcoxon is not None and len(diff) >= 6 and (diff != 0).any():
                try:
                    p = f", p={wilcoxon(diff).pvalue:.3f}"
                except Exception:
                    pass
            cells.append(f"{diff.mean():+.3f} ({int((diff > 0).sum())}/{len(diff)}{p})")
        return cells
    M += ["## 2. 짝지은 비교 (시드 0, 같은 20대, 차 단위 ROC 차이 평균 · 이긴 차 수 · 윌콕슨 p)", "",
          "| 비교 | " + " | ".join(HEADS) + " |", "|---|---|---|---|"]
    inputs = list(dict.fromkeys(R.input)); models = list(dict.fromkeys(R.model))
    for inp in inputs:
        if "ft" in models and "mlp" in models and (c := paired("ft", inp, "mlp", inp)):
            M.append(f"| {inp}: FT − MLP (1단계 어텐션의 몫) | " + " | ".join(c) + " |")
        if "ft" in models and "gbm" in models and (c := paired("ft", inp, "gbm", inp)):
            M.append(f"| {inp}: FT − GBM | " + " | ".join(c) + " |")
    for model in models:
        if "v5" in inputs and "v6" in inputs and (c := paired(model, "v6", model, "v5")):
            M.append(f"| {model}: v6 − v5 (새 피처의 가치) | " + " | ".join(c) + " |")
        for g in C6.NEW_GROUPS:
            if f"-{g}" in inputs and (c := paired(model, "v6", model, f"-{g}")):
                M.append(f"| {model}: v6 − (v6 − {g}) ({W6.GROUP_DESC[g]}) | " + " | ".join(c) + " |")
    M += ["", "판정 기준: FT − MLP ≥ +0.02 이고 p < 0.05 (세 장치 중 둘 이상) → 1단계 어텐션 정당. 그 외 → 정당하지 않음 (MLP 또는 GBM 으로 충분).", ""]
    (out / "summary.md").write_text("\n".join(M) + "\n"); print("\n".join(M))


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--summarize":
        summarize(pathlib.Path(sys.argv[2]))
    else:
        main()
