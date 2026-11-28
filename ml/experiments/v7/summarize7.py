"""6차 실험 요약 — ml/out/v7/<world>/ctx7_*/oneline.csv 를 모아 세계 × 2단계 × k 표로.  python3 ml/experiments/v7/summarize7.py [--out ml/out/v7]"""
import argparse
import glob
import pathlib

import numpy as np
import pandas as pd


def fmt(g, col):
    v = pd.to_numeric(g[col], errors="coerce").dropna()
    if not len(v):
        return "-"
    return f"{v.mean():.3f}" + (f" ±{(v.max() - v.min()) / 2:.3f}" if len(v) > 1 else "")


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--out", default="ml/out/v7"); a = ap.parse_args()
    files = sorted(glob.glob(f"{a.out}/*/ctx7*/oneline.csv"))
    if not files:
        raise SystemExit("oneline.csv 없음")
    df = pd.concat([pd.read_csv(f) for f in files], ignore_index=True); df.to_csv(f"{a.out}/all_runs.csv", index=False)
    key = ["world", "input", "hide", "stage1", "stage2", "k", "select", "time", "neg", "tag"]
    M = [f"# 6차 실험 요약 — 실행 {len(df)}개", "", "값 = 시드 평균 (±반 범위). ROC 는 3장치 평균, 시험 사람 자격 행.  "
         "먼 = 같은 모드 마지막 참고가 20탑승 이상 전 / 없음 = 그 모드 과거 없음 / 전환 = 모드 전환 직후.  비율 = 어텐션이 같은 모드 토큰에 준 가중치 ÷ 그 비중 (1 = 무작위).", ""]
    for w, dw in df.groupby("world", sort=True):
        M += [f"## 세계 {w}", "", "| 입력 | 1단계 | 2단계 | k | 선택 | 시각 | 음성 | 공통 | 문맥0 | **문맥** | 오라클 | 먼 20~99 | 먼 100+ | 없음 | 전환 | 비율 | 탐침E | 학습분 | n |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for kv, g in dw.groupby(key, dropna=False, sort=True):
            kd = dict(zip(key, kv))
            M.append(f"| {kd['input']}{('-' + str(kd['hide'])) if isinstance(kd['hide'], str) and kd['hide'] else ''} | {kd['stage1']} | {kd['stage2']} | {kd['k']} | {kd['select']} | {kd['time']} | {kd['neg']} | "
                     f"{fmt(g, 'roc_common')} | {fmt(g, 'roc_empty')} | **{fmt(g, 'roc_ctx')}** | {fmt(g, 'roc_samemode')} | {fmt(g, 'far_20~99_ctx')} | {fmt(g, 'far_100+_ctx')} | {fmt(g, 'far_없음_ctx')} | "
                     f"{fmt(g, 'sw_전환 직후_ctx')} | {fmt(g, 'same_mode_ratio')} | {fmt(g, 'probe_E')} | {fmt(g, 'train_min')} | {len(g)} |")
        M.append("")
    # gru 대 attn 짝지은 차이 (같은 세계·k·시드)
    for c in ("s_hw_고속_ctx", "s_hw_d1_ctx", "s_hw_5~19_ctx", "s_hw_없음_ctx", "ratio_hw"):
        if c not in df.columns:
            df[c] = np.nan
    M += ["## 상황별 (기존 세계용): 고속 행 ROC · 직전 고속 탑승 거리 1 / 5~19 / 없음 · 고속 가중치 비율", "", "| 세계 | 2단계 | k | 시각 | 태그 | 전체 | 고속 행 | d1 | 5~19 | 없음 | 비율 | n |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for kv, g in df.groupby(["world", "stage2", "k", "time", "tag"], dropna=False, sort=True):
        if pd.to_numeric(g["s_hw_고속_ctx"], errors="coerce").notna().any():
            M.append(f"| {kv[0]} | {kv[1]} | {kv[2]} | {kv[3]} | {kv[4] if isinstance(kv[4], str) else ''} | {fmt(g, 'roc_ctx')} | {fmt(g, 's_hw_고속_ctx')} | {fmt(g, 's_hw_d1_ctx')} | {fmt(g, 's_hw_5~19_ctx')} | {fmt(g, 's_hw_없음_ctx')} | {fmt(g, 'ratio_hw')} | {len(g)} |")
    M.append("")
    piv = df[df.stage2.isin(["gru", "attn"])].pivot_table(index=["world", "k", "seed", "input", "hide", "stage1", "select", "time", "neg", "tag"], columns="stage2", values="roc_ctx", aggfunc="first", dropna=False)
    if {"gru", "attn"} <= set(piv.columns):
        piv = piv.dropna(subset=["gru", "attn"]); piv["attn-gru"] = piv["attn"] - piv["gru"]
        M += ["## attn − gru (같은 세계·k·시드 짝)", "", "| 세계 | k | 짝 수 | attn−gru 평균 | 최소 | 최대 |", "|---|---|---|---|---|---|"]
        for (w, k), g in piv.groupby(level=["world", "k"]):
            M.append(f"| {w} | {k} | {len(g)} | {g['attn-gru'].mean():+.4f} | {g['attn-gru'].min():+.4f} | {g['attn-gru'].max():+.4f} |")
    txt = "\n".join(M); pathlib.Path(f"{a.out}/summary7.md").write_text(txt + "\n"); print(txt)


if __name__ == "__main__":
    main()
