"""순서형 LightGBM 학습과 평가, 배포 파일 내보내기 (S4)

    python3 ml/train/train.py [--data ml/out/dataset.npz] [--persons N] [--no-final]

1. 사람 단위 5-fold. 학습 쪽 사람의 15%를 떼어 조기 종료와 문턱 선택에 쓴다 (학습에 쓰지 않은 행이라 과적합된 확률로 문턱을 고르지 않음)
2. 학습 데이터 = 원본 + 그룹 드롭아웃 복사본 1벌. 이진 분류기 5개 (k = 2..6), 단조 제약
3. 시험 fold: k별 PR-AUC(주), ROC-AUC, 기준선(Oman 점수, 탄 시간), 정책 재생(AC-05),
   그룹 결측 강건성(AC-04: 그룹을 지운 시험 데이터 vs G1 + G9만으로 학습한 모델)
4. 최종: 전체 사람으로 같은 설정 → ml/out/models/model_k.txt, models.json, controller/config/thresholds.json
결과: ml/out/train/report.json, report.md
"""
import argparse
import hashlib
import json
import pathlib
import sys
import time

import numpy as np
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.model_selection import GroupKFold

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import common as C  # noqa: E402
import policy  # noqa: E402

OUT = C.REPO / "ml" / "out"
# 문턱은 행 단위 재현율/정밀도가 아니라 정책 재생으로 고른다. 양성 행 대부분이 이미 멀미가 온 뒤라
# 행 규칙은 문턱을 0.99 근처로 올려 증상 전에 켜지지 않게 만든다 (PRD 규칙에서 바꾼 부분)
TARGETS = {"1": ("detection_rate", ">=", 0.85),             # 송풍: 85% 이상 미리 켜지는 가장 높은 문턱
           "2": ("false_activations_per_hour", "<=", 0.50),  # 디스플레이: 시간당 오경보 0.5회 이하인 가장 낮은 문턱
           "3": ("false_activations_per_hour", "<=", 0.25)}  # 폰: 0.25회 이하
GRID = np.round(np.arange(0.05, 0.9501, 0.01), 2)
OPTIONAL = ["G2", "G3a", "G3b", "G4", "G5", "G6", "G7", "G10"]
EVAL_K = [3, 4]  # Tier 입력. 강건성은 이 둘만 본다
DEFAULT = json.loads((C.REPO / "protocol" / "constants.json").read_text())["mssq"]["default_profile"]


def default_profile(X):
    """폰이 없을 때 P3가 넣는 승객 피처: MSSQ 12.90, 민감도 1.0, EV 경험은 모름(NaN). 차량 유형은 그대로"""
    Xd = X.copy()
    Xd[:, C.NAMES.index("mssq")] = DEFAULT["mssq"]
    Xd[:, C.NAMES.index("sensitivity")] = DEFAULT["sens"]
    Xd[:, C.NAMES.index("ev_experience")] = np.nan
    return Xd


def pick_threshold(grid, vals, op, target):
    """grid 오름차순. >=: 그 문턱과 그 아래가 모두 목표를 넘는 가장 큰 문턱 (없으면 grid[0])
    <=: 그 문턱과 그 위가 모두 목표 이하인 가장 작은 문턱 (없으면 1.0 = 켜지지 않음). 값이 None/NaN이면 실패로 본다."""
    v = np.array([np.nan if x is None else x for x in vals], float)
    if op == ">=":
        ok = np.cumprod(np.nan_to_num(v, nan=-np.inf) >= target).astype(bool)
        return float(grid[ok][-1]) if ok.any() else float(grid[0])
    ok = np.cumprod((np.nan_to_num(v, nan=np.inf) <= target)[::-1])[::-1].astype(bool)
    return float(grid[ok][0]) if ok.any() else 1.0


def thresholds_from(P, misc, future_max, t, ride):
    """문턱 고르기용 사람(학습에 안 쓴 15%)의 탑승을 GRID의 문턱마다 재생 → Tier별 문턱, 훑은 곡선"""
    rides = policy.split_rides(ride)
    th, sweep = {}, {}
    for tier, (metric, op, target) in TARGETS.items():
        cfg = policy.POLICY["tiers"][tier]
        k = cfg["input_k"]
        r = P[:, C.K.index(k)]
        res = [policy.evaluate_tier(r, misc, future_max, t, rides, g, k, cfg["hold_cycles"]) for g in GRID]
        th[tier] = pick_threshold(GRID, [x[metric] for x in res], op, target)
        sweep[tier] = {m: [x[m] for x in res] for m in ("detection_rate", "median_lead_s", "false_activations_per_hour")}
    return th, sweep


def scores(y, s):
    return {"pr_auc": float(average_precision_score(y, s)), "roc_auc": float(roc_auc_score(y, s))}


def blank(X, keep_groups):
    Xb = np.full_like(X, np.nan)
    for g in keep_groups:
        Xb[:, C.GROUPS[g]] = X[:, C.GROUPS[g]]
    return Xb


def fit_all(X, y, person, ride, rng, ks=C.K, keep_groups=None):
    """원본 + 그룹 드롭아웃 복사본으로 k마다 학습. keep_groups가 있으면 그 그룹만 남기고 학습."""
    va = C.person_split(person, 0.15, rng)
    tr = ~va
    Xtr = X[tr]
    Xaug = np.vstack([Xtr, C.group_dropout(Xtr, ride[tr], rng)])
    Xva = X[va]
    if keep_groups:
        Xaug, Xva = blank(Xaug, keep_groups), blank(Xva, keep_groups)
    models = {}
    for k in ks:
        j = C.K.index(k)
        yaug = np.r_[y[tr, j], y[tr, j]]
        models[k] = C.train_binary(Xaug, yaug, Xva, y[va, j])
    return models, va


def predict(models, X, ks=C.K):
    return C.monotone_fix(np.column_stack([models[k].predict(X, num_iteration=models[k].best_iteration) for k in ks]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(OUT / "dataset.npz"))
    ap.add_argument("--persons", type=int, default=0, help="빠른 확인용: 앞 N명만")
    ap.add_argument("--no-final", action="store_true")
    a = ap.parse_args()
    t0 = time.time()
    d = C.load(a.data)
    if a.persons:
        m = d["person_id"] < a.persons
        d = {k: (v[m] if v.ndim and len(v) == len(m) else v) for k, v in d.items()}
    X, Y, person, ride = d["X"], d["y"], d["person_id"], d["ride_id"]
    rng = np.random.default_rng(0)
    oman_i, time_i = C.NAMES.index("oman_score"), C.NAMES.index("ride_time")

    folds = []
    for f, (tr, te) in enumerate(GroupKFold(5).split(X, groups=person)):
        tf = time.time()
        models, va_mask = fit_all(X[tr], Y[tr], person[tr], ride[tr], rng)
        va = tr[va_mask]
        th, _ = thresholds_from(predict(models, X[va]), d["misc"][va], d["future_max"][va], d["t"][va], ride[va])
        P = predict(models, X[te])
        res = {"fold": f, "test_persons": int(len(np.unique(person[te]))), "test_rows": int(len(te)),
               "best_iter": {k: models[k].best_iteration for k in C.K}, "thresholds": th, "k": {}}
        for j, k in enumerate(C.K):
            yt = Y[te, j]
            res["k"][k] = {"rate": float(yt.mean()), "model": scores(yt, P[:, j]),
                           "oman_only": scores(yt, X[te, oman_i]), "ride_time_only": scores(yt, X[te, time_i])}
        res["policy"] = policy.evaluate(P, d["misc"][te], d["future_max"][te], d["t"][te], ride[te], th, C.K)
        # AC-04: 시험 데이터에서 그룹 지우기
        rob = {}
        conds = {"all": None, "imu_live (G1+G9+G10)": ["G1", "G9", "G10"], "G1+G9 only": ["G1", "G9"]}
        conds.update({f"without {g}": [x for x in C.GROUPS if x != g] for g in OPTIONAL})
        for name, keep in conds.items():
            Pb = P if keep is None else predict(models, blank(X[te], keep))
            rob[name] = {k: float(average_precision_score(Y[te, C.K.index(k)], Pb[:, C.K.index(k)])) for k in EVAL_K}
        Pd = predict(models, default_profile(X[te]))
        rob["default profile (no phone)"] = {k: float(average_precision_score(Y[te, C.K.index(k)], Pd[:, C.K.index(k)])) for k in EVAL_K}
        base, _ = fit_all(X[tr], Y[tr], person[tr], ride[tr], rng, ks=EVAL_K, keep_groups=["G1", "G9"])
        Pg = np.column_stack([base[k].predict(blank(X[te], ["G1", "G9"]), num_iteration=base[k].best_iteration) for k in EVAL_K])
        rob["trained on G1+G9"] = {k: float(average_precision_score(Y[te, C.K.index(k)], Pg[:, i])) for i, k in enumerate(EVAL_K)}
        res["robustness_pr_auc"] = rob
        folds.append(res)
        print(f"fold {f}: P(>=4) PR-AUC {res['k'][4]['model']['pr_auc']:.3f} (Oman {res['k'][4]['oman_only']['pr_auc']:.3f}),"
              f" th {th}, {time.time() - tf:.0f} s", flush=True)

    mean = lambda f: float(np.nanmean([f(r) for r in folds]))
    summary = {
        "rows": int(len(Y)), "persons": int(len(np.unique(person))), "rides": int(len(np.unique(ride))),
        "k": {k: {"rate": mean(lambda r: r["k"][k]["rate"]),
                  **{f"{src}_{m}": mean(lambda r: r["k"][k][src][m]) for src in ("model", "oman_only", "ride_time_only")
                     for m in ("pr_auc", "roc_auc")}} for k in C.K},
        "thresholds_mean": {t: mean(lambda r: r["thresholds"][t]) for t in TARGETS},
        "policy": {t: {m: mean(lambda r: r["policy"][t][m] if r["policy"][t][m] is not None else np.nan)
                       for m in ("detection_rate", "on_within_2min_after_rate", "median_lead_s", "false_activations_per_hour", "false_activation_share",
                                 "on_time_share")} for t in TARGETS},
        "robustness_pr_auc": {c: {k: mean(lambda r: r["robustness_pr_auc"][c][k]) for k in EVAL_K}
                              for c in folds[0]["robustness_pr_auc"]},
        "g8": "G8 is enabled=false in feature_order v1 (always NaN), so with / without G8 are the same model",
    }

    final = None
    if not a.no_final:
        models, va = fit_all(X, Y, person, ride, rng)
        vi = np.where(va)[0]
        th, sweep = thresholds_from(predict(models, X[vi]), d["misc"][vi], d["future_max"][vi], d["t"][vi], ride[vi])
        mdir = OUT / "models"
        mdir.mkdir(parents=True, exist_ok=True)
        files = {}
        for k in C.K:
            p = mdir / f"model_{k}.txt"
            models[k].save_model(str(p), num_iteration=models[k].best_iteration)
            files[k] = {"file": p.name, "trees": models[k].best_iteration,
                        "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
        (mdir / "models.json").write_text(json.dumps({"feature_order_version": C.FO["version"], "n_features": len(C.NAMES),
                                                       "k": C.K, "models": files}, indent=1))
        (C.REPO / "controller" / "config" / "thresholds.json").write_text(json.dumps({
            "placeholder": False, "feature_order_version": C.FO["version"],
            "source": "ml/train/train.py final model, policy replay on a 15% person holdout of the synthetic data",
            "rule": {t: f"{'largest' if op == '>=' else 'smallest'} threshold with {m} {op} {v} (replay, grid step 0.01)"
                     for t, (m, op, v) in TARGETS.items()},
            "base_th": {t: round(v, 4) for t, v in th.items()}}, indent=2) + "\n")
        final = {"thresholds": th, "models": files, "grid": GRID.tolist(), "sweep": sweep}

    report = {"summary": summary, "final": final, "folds": folds, "seconds": round(time.time() - t0, 1)}
    (OUT / "train").mkdir(parents=True, exist_ok=True)
    (OUT / "train" / "report.json").write_text(json.dumps(report, indent=1))
    (OUT / "train" / "report.md").write_text(markdown(report))
    print(markdown(report))


def markdown(r):
    s = r["summary"]
    L = [f"# 학습 리포트 (사람 단위 5-fold, {s['persons']}명, {s['rides']}회 탑승, {s['rows']}행)", "",
         "## k별 성능 (시험 fold 평균)", "", "| k | 양성 비율 | 모델 PR-AUC | Oman만 | 탄 시간만 | 모델 ROC-AUC | Oman만 |",
         "|---|---|---|---|---|---|---|"]
    for k, v in s["k"].items():
        L.append(f"| {k} | {v['rate']:.3f} | **{v['model_pr_auc']:.3f}** | {v['oman_only_pr_auc']:.3f} | "
                 f"{v['ride_time_only_pr_auc']:.3f} | {v['model_roc_auc']:.3f} | {v['oman_only_roc_auc']:.3f} |")
    L += ["", "## 정책 재생 (AC-05, 게이트 항상 열림, 수동 조작 없음)", "",
          "문턱은 학습 쪽 15% 사람의 재생으로 고름: " + ", ".join(f"Tier {t} {m} {op} {v}" for t, (m, op, v) in TARGETS.items()), "",
          "| Tier | 문턱 (fold 평균) | 탐지율 | 도달 후 2분 안 ON | 중앙 리드타임 | 시간당 오경보 | 오경보 비율 | ON 시간 비율 |", "|---|---|---|---|---|---|---|---|"]
    for t, v in s["policy"].items():
        L.append(f"| {t} | {s['thresholds_mean'][t]:.3f} | {v['detection_rate']:.2f} | {v['on_within_2min_after_rate']:.2f} | {v['median_lead_s'] / 60:.1f}분 | "
                 f"{v['false_activations_per_hour']:.2f} | {v['false_activation_share']:.2f} | {v['on_time_share']:.2f} |")
    L += ["", "## 그룹 결측 강건성 (AC-04, PR-AUC)", "", "| 시험 조건 | P(≥3) | P(≥4) |", "|---|---|---|"]
    for c, v in s["robustness_pr_auc"].items():
        L.append(f"| {c} | {v[3]:.3f} | {v[4]:.3f} |")
    L += ["", f"G8: {s['g8']}"]
    if r["final"]:
        L += ["", "## 최종 모델", "", f"문턱: {r['final']['thresholds']}",
              "트리 수: " + ", ".join(f"k={k} {v['trees']}" for k, v in r["final"]["models"].items())]
    L += ["", f"소요 시간 {r['seconds'] / 60:.1f}분"]
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    main()
