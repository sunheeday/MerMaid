"""V3 조합 비교표 (v3_combos.sh finish 가 부른다).

    python3 ml/experiments/v6/compare_combos.py --dataset ml/out/runs/v028 --runs v029 v030 v031 v032 --out ml/out/runs/v028/combos.md

run 마다 train/s<seed>/fleet_preds.npz (플릿 채점 행: trip_order >= adapt) 로 장치별 ROC (자격 행만) 를 시드별로 다시 계산하고,
고른 시드(train/picked), 임계값(thresholds.json), C++ 패리티·시간(verify/ctx_check_mac.txt, 있으면 verify/pi_qnx.txt) 을 붙인다.
문맥 0개 = 같은 모델에 지난 토큰을 하나도 안 준 확률 (문맥이 얼마나 보태는지).
"""
import argparse
import json
import pathlib
import re

import numpy as np
from sklearn.metrics import roc_auc_score

HEADS = ["fan", "ambient", "dots"]


def rocs(p, Y, W):
    out = []
    for j in range(3):
        m = W[:, j] > 0
        y = Y[m, j]
        out.append(float(roc_auc_score(y, p[m, j])) if 0 < y.sum() < len(y) else float("nan"))
    return out


def timing(path):
    if not path.exists():
        return None
    t = path.read_text()
    m = re.search(r"cycle \(summarize\+predict, \d+ tokens\) median ([\d.]+) us.*ride start \(prepare, \d+ tokens\) median ([\d.]+) us", t)
    ok = "PASS" in t
    return (float(m.group(1)), float(m.group(2)), ok) if m else (None, None, ok)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True); ap.add_argument("--runs", nargs="+", required=True); ap.add_argument("--out", required=True)
    a = ap.parse_args()
    L = [f"# V3 조합 비교 — 세계 {a.dataset}", "",
         "플릿 채점 행 (trip_order ≥ 24), 자격 행만, 장치별 ROC. 굵은 시드 = 검증 ROC로 골라 models/에 내보낸 것.", "",
         "| run | 조합 | 시드 | fan | ambient | dots | 평균 | 문맥 0개 평균 | 문맥 이득 |", "|---|---|---|---|---|---|---|---|---|"]
    summary = []
    for r in a.runs:
        run = pathlib.Path("ml/out/runs") / r
        mj = json.loads((run / "models" / "models.json").read_text())
        combo = f"{mj['stage1']} + {mj['stage2']}"
        picked = (run / "train" / "picked").read_text().split()[0] if (run / "train" / "picked").exists() else ""
        means = []
        for sd in sorted((run / "train").glob("s*/fleet_preds.npz")):
            d = np.load(sd)
            v, v0 = rocs(d["p"], d["Y"], d["W"]), rocs(d["p_empty"], d["Y"], d["W"])
            s = sd.parent.name[1:]
            mean, mean0 = float(np.nanmean(v)), float(np.nanmean(v0)); means.append(mean)
            tag = f"**{s}**" if s == picked else s
            L.append(f"| {r} | {combo} | {tag} | " + " | ".join(f"{x:.3f}" for x in v) + f" | {mean:.3f} | {mean0:.3f} | {mean - mean0:+.3f} |")
        th = json.loads((run / "thresholds.json").read_text())["base_th"] if (run / "thresholds.json").exists() else {}
        mac, pi = timing(run / "verify" / "ctx_check_mac.txt"), timing(run / "verify" / "pi_qnx.txt")
        summary.append((r, combo, means, th, mac, pi))
    L += ["", "| run | 조합 | 시드 평균 ROC (범위) | 임계값 fan / ambient / dots | Mac 패리티 | Mac 주기 / 준비 | Pi 패리티 | Pi 주기 / 준비 |",
          "|---|---|---|---|---|---|---|---|"]
    for r, combo, means, th, mac, pi in summary:
        rng = f"{np.mean(means):.3f} ({min(means):.3f}–{max(means):.3f})" if means else "-"
        ths = " / ".join(f"{th.get(k, float('nan')):.2f}" for k in ("1", "2", "3")) if th else "-"
        def fmt(t):
            if not t:
                return "-", "-"
            c, p, ok = t
            return ("PASS" if ok else "FAIL"), (f"{c:.0f} µs / {p / 1000:.1f} ms" if c is not None else "-")
        mp, mt = fmt(mac); pp, pt = fmt(pi)
        L.append(f"| {r} | {combo} | {rng} | {ths} | {mp} | {mt} | {pp} | {pt} |")
    L += ["", "주기 = 승객 1명, 문맥 토큰 128개의 10초 주기 한 번 (요약 + 2단계). 준비 = 탑승 시작에 승객마다 한 번 (문맥 준비).",
          "mlp 세 조합은 context7 (같은 분할·토큰·1단계 몸통, 드롭아웃 0.1), ft + attn 은 context6 (분할·토큰 규칙이 조금 다르고 드롭아웃 0.2)."]
    pathlib.Path(a.out).write_text("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
