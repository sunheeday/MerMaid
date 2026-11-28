"""축 B/C 요약 — 세계별 context_*/oneline.csv, per_car.csv 를 모아 표로. 축 A 결과(results.csv)가 있으면 축 C 비교도 붙인다.

    python3 ml/experiments/v6/summarize6.py weak=ml/out/v6/weak strong=ml/out/v6/strong [--out ml/out/v6/summary_bc.md]

표  1. 요인표: 세계 × 입력 × 1단계 × 2단계 × k (시드 평균 ± sd), 파라미터, 학습 분, ms/행
    2. 시드 잡음 띠
    3. 짝지은 비교 (같은 20대, 시드 0): attn − gru, attn − gru_qfirst, attn − mean_dev, k128 − k32, k256 − k128 — 윌콕슨 p
    4. 진단: 자기 이력 / 문맥 0개 / 다른 사람 / '켰나' 제거 / 상황 요약 제거 / 1단계 공통
    5. 앞선 탑승 수별
    6. 축 C: 공통 모델 + v6 (축 A) vs 문맥 모델 + v5 vs 문맥 모델 + v6
"""
import argparse
import pathlib

import numpy as np
import pandas as pd

HEADS = ["fan", "ambient", "dots"]


def load(root):
    root = pathlib.Path(root)
    O = [pd.read_csv(f) for f in sorted(root.glob("context_*/oneline.csv"))]
    Cc = [pd.read_csv(f) for f in sorted(root.glob("context_*/per_car.csv"))]
    O = pd.concat(O, ignore_index=True) if O else None; Cc = pd.concat(Cc, ignore_index=True) if Cc else None
    for D in (O, Cc):
        if D is not None:
            D["tag"] = D["tag"].fillna("").astype(str)
            D["cell"] = D["input"] + "/" + D["stage1"] + "-" + D["stage2"] + np.where(D["tag"] != "", "-" + D["tag"], "") + "/k" + D["k"].astype(str)
    A = pd.read_csv(root / "axis_a" / "results.csv") if (root / "axis_a" / "results.csv").exists() else None
    Ac = pd.read_csv(root / "axis_a" / "per_car.csv") if (root / "axis_a" / "per_car.csv").exists() else None
    return O, Cc, A, Ac


def wilcox(diff):
    try:
        from scipy.stats import wilcoxon
        if len(diff) >= 6 and (diff != 0).any():
            return f", p={wilcoxon(diff).pvalue:.3f}"
    except Exception:
        pass
    return ""


def paired_cells(A, B):
    """A, B: vehicle 인덱스의 DataFrame(HEADS 열). → 셀 문자열 3개."""
    B = B.reindex(A.index); out = []
    for h in HEADS:
        d = (A[h] - B[h]).dropna()
        out.append("-" if not len(d) else f"{d.mean():+.3f} ({int((d > 0).sum())}/{len(d)}{wilcox(d)})")
    return out


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("worlds", nargs="+", help="이름=폴더"); ap.add_argument("--out", default=None); a = ap.parse_args()
    worlds = dict(w.split("=", 1) for w in a.worlds); roc = [f"roc_{h}" for h in HEADS]
    M = ["# 5차 실험 — 축 B/C 요약 (문맥 모델)", ""]
    allO = []
    for wn, root in worlds.items():
        O, Cc, A, Ac = load(root)
        if O is None:
            M += [f"## 세계 {wn}: 실행 없음", ""]; continue
        O = O.assign(world=wn); allO.append(O)
        M += [f"## 세계 {wn} — 실행 {len(O)}개, 설정 {O['cell'].nunique()}개", ""]
        # 1 요인표
        M += ["### 1. 요인표 (플릿 뒤 20탑승 플릿 ROC, 시드 평균 ± sd)", "",
              "| 입력 | 1단계 | 2단계 | k | n | " + " | ".join(HEADS) + " | 평균 | 2단계 파라미터 | 학습 분 | ms/행 |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        g = O.groupby(["input", "stage1", "stage2", "tag", "k"], sort=False)
        for (inp, s1, s2, tg, k), d in g:
            m = d[roc].mean(); sd = d[roc].std(ddof=0) if len(d) > 1 else None
            cells = [f"{m[c]:.3f}" + (f" ± {sd[c]:.3f}" if sd is not None else "") for c in roc]
            M.append(f"| {inp} | {s1} | {s2}{('-' + tg) if tg else ''} | {k} | {len(d)} | " + " | ".join(cells) + f" | {m.mean():.3f} | {int(d['params'].iloc[0]):,} | {d['train_min'].mean():.0f} | {d['ms_per_row'].mean():.2f} |")
        M.append("")
        # 2 시드 잡음
        multi = g.filter(lambda d: len(d) > 1)
        M += ["### 2. 시드 잡음 띠", ""]
        if len(multi):
            M += ["| 설정 | n | " + " | ".join(f"sd {h}" for h in HEADS) + " | 최대 sd |", "|---|---|---|---|---|---|"]; worst = 0.0
            for cell, d in multi.groupby("cell"):
                sd = d[roc].std(ddof=0); worst = max(worst, sd.max()); M.append(f"| {cell} | {len(d)} | " + " | ".join(f"{sd[c]:.3f}" for c in roc) + f" | {sd.max():.3f} |")
            M += ["", f"읽는 기준: 차이가 {2 * worst:.3f} (= 2 × 최대 sd) 보다 작으면 같은 것으로 읽는다.", ""]
        else:
            M += ["시드가 하나뿐. 0.01~0.02 차이는 읽지 않는다.", ""]
        # 3 짝지은 비교
        if Cc is not None:
            ctx = Cc[(Cc.model == "ctx") & (Cc.seed == 0)]
            def get(inp, s1, s2, k):
                d = ctx[(ctx.input == inp) & (ctx.stage1 == s1) & (ctx.stage2 == s2) & (ctx.k == k) & (ctx.tag == "")]
                return d.set_index("vehicle")[HEADS] if len(d) else None
            M += ["### 3. 짝지은 비교 (시드 0, 같은 차들, 차 단위 ROC 차이 평균 · 이긴 차 수 · 윌콕슨 p)", "", "| 비교 | " + " | ".join(HEADS) + " |", "|---|---|---|---|"]
            for inp in sorted(ctx.input.unique()):
                for s1 in sorted(ctx.stage1.unique()):
                    for k in sorted(ctx.k.unique()):
                        A_ = get(inp, s1, "attn", k)
                        if A_ is None:
                            continue
                        for other, label in (("gru", "질의의 몫 (attn − gru)"), ("gru_qfirst", "병렬 선택의 몫 (attn − gru_qfirst)"), ("mean_dev", "순서·질의 모두의 몫 (attn − mean_dev)")):
                            B_ = get(inp, s1, other, k)
                            if B_ is not None:
                                M.append(f"| {inp}/{s1} k{k}: {label} | " + " | ".join(paired_cells(A_, B_)) + " |")
                    for s2 in ("attn", "gru"):
                        ks = sorted(ctx[(ctx.input == inp) & (ctx.stage1 == s1) & (ctx.stage2 == s2)].k.unique())
                        for k1, k2 in zip(ks[:-1], ks[1:]):
                            A_, B_ = get(inp, s1, s2, k2), get(inp, s1, s2, k1)
                            if A_ is not None and B_ is not None:
                                M.append(f"| {inp}/{s1}-{s2}: 이력 길이 k{k2} − k{k1} | " + " | ".join(paired_cells(A_, B_)) + " |")
            M += ["", "판정: attn − gru ≥ +0.02 이고 p < 0.05 (장치 셋 중 둘 이상) → 그 세계에서 2단계 어텐션 정당.", ""]
        # 4 진단
        M += ["### 4. 진단 (플릿 ROC, 시드 평균) — 같은 모델에 다른 문맥", "",
              "| 설정 | 장치 | 1단계 공통 | 문맥 0개 | 자기 이력 | 다른 사람 | '켰나' 제거 | 상황 요약 제거 |", "|---|---|---|---|---|---|---|---|"]
        for (inp, s1, s2, tg, k), d in g:
            cell = f"{inp}/{s1}-{s2}{('-' + tg) if tg else ''}/k{k}"
            for h in HEADS:
                cm = d[f"common_{h}"].mean() if f"common_{h}" in d and d[f"common_{h}"].notna().any() else np.nan
                M.append(f"| {cell} | {h} | {'-' if np.isnan(cm) else f'{cm:.3f}'} | {d[f'empty_{h}'].mean():.3f} | {d[f'roc_{h}'].mean():.3f} | {d[f'swap_{h}'].mean():.3f} | {d[f'nolabel_{h}'].mean():.3f} | {d[f'nosit_{h}'].mean():.3f} |")
        M += ["", "읽기: '상황 요약 제거'로 떨어지는 몫이 과거의 상황을 읽는 몫. 어텐션이 GRU 보다 이 몫이 크면 '상황이 닮은 과거를 골라 읽는다'는 뜻.", ""]
        # 5 앞선 탑승 수별
        bk = ["0", "1~3", "4~9", "10+"]
        M += ["### 5. 앞선 탑승 수별 플릿 ROC (전체 플릿 행, 시드 평균)", "", "| 설정 | 장치 | " + " | ".join(bk) + " |", "|---|---|---|---|---|---|"]
        for (inp, s1, s2, tg, k), d in g:
            cell = f"{inp}/{s1}-{s2}{('-' + tg) if tg else ''}/k{k}"
            for h in HEADS:
                vals = []
                for b in bk:
                    v = pd.to_numeric(d[f"{b}_{h}"], errors="coerce").mean() if f"{b}_{h}" in d else np.nan; vals.append("-" if np.isnan(v) else f"{v:.3f}")
                M.append(f"| {cell} | {h} | " + " | ".join(vals) + " |")
        M.append("")
        # 6 축 C
        if A is not None:
            M += ["### 6. 축 C — 새 피처가 문맥 모델의 필요성을 줄이나 (플릿 ROC)", "", "| 모델 | 입력 | " + " | ".join(HEADS) + " | 평균 |", "|---|---|---|---|---|"]
            for model in ("gbm", "mlp", "ft"):
                for inp in ("v5", "v6"):
                    d = A[(A.model == model) & (A.input == inp)]
                    if len(d):
                        M.append(f"| 공통 {model} | {inp} | " + " | ".join(f"{d[c].mean():.3f}" for c in roc) + f" | {d['avg'].mean():.3f} |")
            for (inp, s1, s2, tg, k), d in g:
                if s2 in ("gru", "attn") and k == max(O.k):
                    M.append(f"| 문맥 {s1}-{s2} k{k} | {inp} | " + " | ".join(f"{d[c].mean():.3f}" for c in roc) + f" | {d[roc].mean().mean():.3f} |")
            M += ["", "읽기: '공통 + v6' 가 '문맥 + v5' 에 얼마나 다가가나. 가까우면 이력 피처 몇 개(조작 횟수·누적 탑승)로 충분하다는 더 싼 설계가 생긴다.", ""]
    if len(allO) > 1:                                                    # 세계 간 비교 (약 vs 강)
        AO = pd.concat(allO, ignore_index=True)
        M += ["## 세계 간 — 습관 세기에 따라 2단계 차이가 벌어지나 (시드 평균, 플릿 ROC 평균)", "", "| 입력/1단계/2단계/k | " + " | ".join(worlds) + " |", "|---|" + "---|" * len(worlds)]
        for cell, d in AO.groupby("cell", sort=False):
            M.append(f"| {cell} | " + " | ".join(f"{d[d.world == w][roc].mean().mean():.3f}" if (d.world == w).any() else "-" for w in worlds) + " |")
        M.append("")
    txt = "\n".join(M); print(txt)
    outp = pathlib.Path(a.out or pathlib.Path(list(worlds.values())[0]).parent / "summary_bc.md"); outp.write_text(txt + "\n"); print("→", outp)


if __name__ == "__main__":
    main()
