"""보정 전과 보정 후 데이터를 같은 피처 계산기(featurize)와 같은 모델에 넣어 위험 확률을 비교한다 (검증 V12).

쓰는 법 (맥 터미널, imu_calib 폴더에서):
    .venv/bin/python model_rerun.py <주행 폴더> <결과 폴더> [--models <모델 폴더>]

비교 대상 (둘 다 맥에서 새로 계산):
    C 보정 전   raw_caraxis: 센서 축을 차량 축(x 앞, y 왼쪽)으로만 바로잡음. 기울기·눈금·시간 보정 없음
    D 보정 후   calibrated: 3·1·2·6·4단계까지 적용한 것
확인용 (비교 대상 아님):
    A Pi 기록   주행 때 Pi가 실제로 낸 피처와 확률 (sqlite infer 표)
    B 맥 재현   Pi가 본 것과 같은 입력(센서 축 그대로)으로 맥에서 계산. A와 같으면 계산 경로가 맞다는 뜻
               (모델도 A 피처 → A 확률이 그대로 나오는지 확인한다)

--models 기본값: 저장소 ml/out/runs 에서 모델이 있는 가장 최근 버전.
차량 외 피처(승객 프로필, 허용도 등 71~76번)는 A의 값을 그대로 쓴다.
저장소 위치는 환경 변수 MERMAID_REPO 로 바꿀 수 있다 (기본: 이 파일이 들어 있는 저장소).
"""
import argparse
import glob
import json
import os
import sqlite3
import sys
import tempfile

import numpy as np

def _default_repo():
    """이 파일이 저장소 안(ml/realdata/imu_calib)에 있으면 그 저장소, 아니면 ~/SE_project/MerMaid-main"""
    here = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
    return here if os.path.exists(os.path.join(here, "ml", "trip.py")) else os.path.expanduser("~/SE_project/MerMaid-main")


REPO = os.environ.get("MERMAID_REPO") or _default_repo()
sys.path.insert(0, os.path.join(REPO, "ml"))
import trip as T  # noqa: E402  저장소의 trip.bin 읽기·쓰기

K = [2, 3, 4, 5, 6]


def load_pi(db):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)  # 읽기 전용
    rows = con.execute("select t, x, p2, p3, p4, p5, p6 from infer order by seq").fetchall()
    meta = dict(con.execute("select key, value from meta").fetchall())
    con.close()
    t = np.array([r[0] for r in rows])
    X = np.stack([np.frombuffer(r[1], "<f8") for r in rows])
    P = np.array([r[2:] for r in rows], float)
    return t, X, P, meta


def featurize(samples):
    """samples: [t, ax, ay, az, gx, gy, gz, ...] 100 Hz → (피처 시각, 피처 71개)"""
    rows = T.empty(len(samples))
    for i, name in enumerate(["ax", "ay", "az", "gx", "gy", "gz"]):
        rows[:, T.COL[name]] = samples[:, 1 + i]
    rows[:, T.COL["t"]] = samples[:, 0] - samples[0, 0]
    with tempfile.TemporaryDirectory() as td:
        T.write_trip(os.path.join(td, "trip.bin"), rows)
        return T.featurize(os.path.join(td, "trip.bin"), os.path.join(td, "f.bin"))


def align(t_pi, t_f, V, tol=0.5):
    """Pi 주기 시각에 가장 가까운 맥 피처 행 (tol 초 안)"""
    j = np.clip(np.searchsorted(t_f, t_pi), 1, len(t_f) - 1)
    j = np.where(np.abs(t_f[j - 1] - t_pi) < np.abs(t_f[j] - t_pi), j - 1, j)
    ok = np.abs(t_f[j] - t_pi) < tol
    out = np.full((len(t_pi), V.shape[1]), np.nan)
    out[ok] = V[j[ok]]
    return out


def predict(models_dir, X):
    import lightgbm as lgb
    P = np.column_stack([lgb.Booster(model_file=os.path.join(models_dir, f"model_{k}.txt")).predict(X) for k in K])
    for j in range(1, P.shape[1]):  # 저장소 common.monotone_fix 와 같음
        P[:, j] = np.minimum(P[:, j], P[:, j - 1])
    return P


def replay(r, th, hold, off_margin=0.10, off_hold=6):
    """저장소 train/policy.replay_ride 와 같은 규칙: 10초마다 확률 → ON 여부"""
    on, cnt, low, state = False, 0, 0, np.zeros(len(r), bool)
    for j, x in enumerate(r):
        cnt = cnt + 1 if x >= th else 0
        low = low + 1 if x < th - off_margin else 0
        if not on and cnt >= hold:
            on = True
        elif on and low >= off_hold:
            on = False
        state[j] = on
    return state


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ride")
    ap.add_argument("outdir")
    ap.add_argument("--models", help="v007 모델 폴더 (model_2.txt ~ model_6.txt)")
    ap.add_argument("--still-until", type=float, default=320, help="출발 전 정지 끝 [초]")
    ap.add_argument("--mssq", type=float,
                    help="승객 설문값을 이 값으로 바꿔 계산 (민감도도 oman.sensitivity로 함께). A는 Pi 피처로 다시 계산한 값이 된다")
    ap.add_argument("--extra", action="append", default=[], metavar="이름=폴더",
                    help="비교에 더할 보정 결과 폴더 (주행 폴더 기준, 예: 'E 횡단경사 뺌=calibrated_xslope')")
    ap.add_argument("--until", type=float, help="이 시각[초] 뒤 주기는 빼고 비교 (주행 뒤 센서를 만진 구간)")
    args = ap.parse_args()
    if args.models is None:
        found = sorted(glob.glob(os.path.join(REPO, "ml", "out", "runs", "v[0-9][0-9][0-9]", "models", "model_6.txt")))
        args.models = os.path.dirname(found[-1]) if found else None
    print("모델:", args.models or "없음 (피처까지만 비교)")
    os.makedirs(args.outdir, exist_ok=True)

    db = glob.glob(os.path.join(args.ride, "logs", "rides", "*.sqlite"))[0]
    t_pi, X_pi, P_pi, meta = load_pi(db)
    P_rec, X_rec = P_pi.copy(), X_pi.copy()          # Pi가 실제로 기록한 값 (모델 확인용)
    if args.mssq is not None:
        sys.path.insert(0, os.path.join(REPO, "ml"))
        import oman
        phone = ~np.isnan(X_pi[:, 73]) & (np.abs(X_pi[:, 73] - 12.9) > 1e-9)   # 기본 프로필(12.9) 주기는 그대로
        X_pi[phone, 73], X_pi[phone, 74] = args.mssq, float(oman.sensitivity(args.mssq))
        P_pi = predict(args.models, X_pi)
        print(f"MSSQ {args.mssq:g}, 민감도 {oman.sensitivity(args.mssq):.3f}로 바꿈: 폰 주기 {phone.sum()}/{len(phone)}")
    if args.until:
        keep = t_pi <= args.until
        t_pi, X_pi, P_pi = t_pi[keep], X_pi[keep], P_pi[keep]
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    labels = np.array(con.execute("select t, misc from label order by t").fetchall(), float).reshape(-1, 2)
    con.close()
    if args.until:
        labels = labels[labels[:, 0] <= args.until]
    # Pi 주기 시각은 샘플 번호 기준. 보정 후(D)는 실제 시계 기준이라 같은 순간을 고르려면 늘여 준다
    tm = json.load(open(os.path.join(args.ride, "calibrated", "calibration.json")))["time"]
    stretch = tm["true_duration_s"] / tm["nominal_duration_s"]
    tr = json.load(open(os.path.join(args.ride, "raw_caraxis", "transform.json")))
    R = np.array(tr["R_car_from_sensor"])
    car = np.load(os.path.join(args.ride, "raw_caraxis", "samples_100hz.npy")).astype(float)
    cal = np.load(os.path.join(args.ride, "calibrated", "samples_100hz.npy")).astype(float)
    sensor = car.copy()  # 차량 축 → 센서 축으로 되돌림 (Pi가 주행 때 본 그대로)
    sensor[:, 1:4] = car[:, 1:4] @ R
    sensor[:, 4:7] = car[:, 4:7] @ R

    cases = {"B 맥 재현(센서 축)": sensor, "C 보정 전(축만)": car, "D 보정 후": cal}
    for e in args.extra:
        name, folder = e.split("=", 1)
        cases[name] = np.load(os.path.join(args.ride, folder, "samples_100hz.npy")).astype(float)
    real_clock = lambda name: name[0] in "DEFG"   # 보정 후 계열은 실제 시계 기준
    X = {"A Pi 기록": X_pi}
    for name, s in cases.items():
        tf, V = featurize(s)
        Xc = X_pi.copy()
        if real_clock(name):  # 실제 시계로 같은 순간에 가장 가까운 주기 (주기 10초라 ±5초 안)
            Xc[:, :71] = align(t_pi * stretch, tf, V, tol=5.0)
        else:
            Xc[:, :71] = align(t_pi, tf, V)
        Xc[np.isnan(X_pi)] = np.nan  # Pi에서 꺼져 있던 칸은 똑같이 비운다
        X[name] = Xc
        assert not np.isnan(Xc[:, :71][:, ~np.isnan(X_pi[:, :71]).all(axis=0)]).all(axis=1).any(), f"{name}: 짝을 못 찾은 주기 있음"
        print(f"{name}: 피처 {len(tf)}행")

    names = json.load(open(os.path.join(REPO, "protocol", "feature_order.json")))["features"]
    names = [f["name"] for f in names]
    lines = ["# 보정 전후 모델 입력·출력 비교 (V12)", "",
             f"주행: `{args.ride}`  모델: {meta.get('model_version')}  주기 {len(t_pi)}개(10초마다)", ""]
    if args.mssq is not None:
        lines += [f"**승객 설문값 MSSQ를 {args.mssq:g}로 바꿔 계산한 결과** (실제 주행은 33). "
                  "A는 Pi 피처에 이 값을 넣어 다시 계산한 것이고, 'B와 A 확률 차이'도 이 기준이다.", ""]

    # 1) 계산 경로 확인: B가 A와 같은가
    d = np.abs(X["B 맥 재현(센서 축)"][:, :71] - X_pi[:, :71])
    rel = d / np.maximum(np.abs(X_pi[:, :71]), 1e-3)  # 0 근처 값의 반올림 차이는 크게 보이지 않게
    worst = np.nanmax(np.where(np.isnan(rel), -1, rel), axis=0)
    lines += ["## 1. 계산 경로 확인 (A Pi 기록 vs B 맥 재현, 같은 입력)", "",
              f"- 차량 피처 71개 중 상대 차이 1% 이하: {(worst <= 0.01).sum()}개 (비어 있는 칸 제외)",
              "- 차이가 큰 피처: " + ", ".join(f"{names[i]} {worst[i]*100:.1f}%" for i in np.argsort(-worst)[:6]), ""]

    # 2) 피처 변화: 출발 전 정지와 주행 중
    still = t_pi < args.still_until
    key = ["rms_x_1m", "rms_y_1m", "rms_x_5m", "rms_y_5m", "pitch_mean_1m", "oman_score", "msdv", "jerk_rms_x_1m", "yaw_rms_1m"]
    lines += ["## 2. 주요 피처의 중앙값", "", "| 피처 | 구간 | " + " | ".join(X) + " |", "|---|---|" + "---|" * len(X)]
    for f in key:
        i = names.index(f)
        for lab, m in [("정지", still), ("주행", ~still)]:
            lines.append(f"| {f} | {lab} | " + " | ".join(f"{np.nanmedian(v[m, i]):.3f}" for v in X.values()) + " |")
    lines.append("")

    out = {"t": t_pi.tolist(), "features": {k: v.tolist() for k, v in X.items()}}
    if args.models:
        th = json.loads(meta["thresholds"])["base_th"]
        pol = json.loads(meta["policy_json"])["tiers"]
        P = {"A Pi 기록": P_pi}
        P.update({k: predict(args.models, v) for k, v in X.items() if k != "A Pi 기록"})
        lines += ["## 3. 위험 확률", "",
                  f"- 모델 확인: A 피처를 이 모델에 넣은 값과 Pi 기록 확률의 최대 차이 "
                  f"{np.abs(predict(args.models, X_rec) - P_rec).max():.2e} (1e-6 이하면 Pi와 같은 모델)",
                  f"- 계산 경로 확인: B와 A 확률의 최대 차이 {np.abs(P['B 맥 재현(센서 축)'] - P_pi).max():.2e}", ""]
        for kk in (3, 4):
            lines += [f"P(멀미 점수 ≥ {kk}) (Tier {'1' if kk == 3 else '2·3'} 입력)", "",
                      "| 경우 | 정지 중 평균 | 정지 중 최대 | 주행 중 평균 | 주행 중 최대 |", "|---|---|---|---|---|"]
            for k, p in P.items():
                r = p[:, K.index(kk)]
                lines.append(f"| {k} | {r[still].mean():.3f} | {r[still].max():.3f} | {r[~still].mean():.3f} | {r[~still].max():.3f} |")
            for other in [k for k in P if k[0] in "DEFG"]:
                dc = P[other][:, K.index(kk)] - P["C 보정 전(축만)"][:, K.index(kk)]
                lines += [f"- {other[0]} − C: 정지 중 평균 {dc[still].mean():+.3f}, 주행 중 평균 {dc[~still].mean():+.3f}, "
                          f"주기별 |차이| 중앙값 {np.median(np.abs(dc)):.3f}, 최대 {np.abs(dc).max():.3f}"]
            lines.append("")
        lines += ["", "## 4. 기기 ON 시간 (정책 재생, 문턱 " + ", ".join(f"Tier{t} {v}" for t, v in th.items()) + ")", "",
                  "| 경우 | " + " | ".join(f"Tier{t} {pol[t]['device']}" for t in th) + " | 처음 켜진 시각(Tier1) |",
                  "|---|" + "---|" * (len(th) + 1)]
        for k, p in P.items():
            cells, first = [], "-"
            for t in th:
                s = replay(p[:, K.index(pol[t]["input_k"])], th[t], pol[t]["hold_cycles"])
                cells.append(f"{s.sum() * 10 / 60:.1f}분 ({s[still].sum() * 10}초 정지 중)")
                if t == "1" and s.any():
                    first = f"{t_pi[np.argmax(s)]:.0f}초"
            lines.append(f"| {k} | " + " | ".join(cells) + f" | {first} |")
        if len(labels):
            j = np.clip(np.searchsorted(t_pi, labels[:, 0]) - 1, 0, len(t_pi) - 1)  # 입력 직전 주기
            cmp = [k for k in P if k[0] in "CDEFG"]
            short = lambda k: k.split(" ", 1)[1]
            lines += ["", "## 5. 승객이 직접 입력한 MISC vs 그 직전 주기의 확률", "",
                      "| 시각 [초] | MISC | " + " | ".join(f"{short(k)} P(≥3)" for k in cmp) + " | "
                      + " | ".join(f"{short(k)} P(≥4)" for k in cmp) + " |", "|---|---|" + "---|" * (2 * len(cmp))]
            for (tl, mi), jj in zip(labels, j):
                lines.append(f"| {tl:.0f} | {mi:.0f} | " + " | ".join(f"{P[k][jj, 1]:.2f}" for k in cmp) + " | "
                             + " | ".join(f"{P[k][jj, 2]:.2f}" for k in cmp) + " |")
            lines.append("")
            for kk in (3, 4):
                y = labels[:, 1] >= kk
                for k in cmp:
                    r = P[k][j, K.index(kk)]
                    lines.append(f"- {short(k)} P(≥{kk}): 실제 {kk} 이상일 때 평균 "
                                 + (f"{r[y].mean():.2f} ({y.sum()}번)" if y.any() else "- (0번)")
                                 + f", 실제 {kk} 미만일 때 평균 " + (f"{r[~y].mean():.2f} ({(~y).sum()}번)" if (~y).any() else "- (0번)"))
        out["P"] = {k: v.tolist() for k, v in P.items()}
        out["labels"] = labels.tolist()
        plot(t_pi, P, args.still_until, os.path.join(args.outdir, "5_위험확률_전후.png"), labels,
             "" if args.mssq is None else f" (MSSQ {args.mssq:g})")
    else:
        lines += ["## 3. 위험 확률", "", "모델 파일이 없어 아직 계산하지 않음 (`--models` 필요)."]

    open(os.path.join(args.outdir, "model_rerun.md"), "w").write("\n".join(lines) + "\n")
    json.dump(out, open(os.path.join(args.outdir, "model_rerun.json"), "w"))
    print("\n".join(lines))


def plot(t, P, still_until, path, labels=None, note=""):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.family"] = "Apple SD Gothic Neo"
    plt.rcParams["axes.unicode_minus"] = False
    col = {"A": "#999", "B": "#e67e22", "C": "#c0392b", "D": "#1f6fb2", "E": "#27ae60", "F": "#8e44ad"}
    fig, ax = plt.subplots(2, 1, figsize=(13, 7), sharex=True)
    for i, k in enumerate([3, 4]):  # Tier1 입력 k=3, Tier2·3 입력 k=4
        for name, p in P.items():
            ax[i].plot(t, p[:, K.index(k)], color=col[name[0]], lw=1.4 if name[0] in "ADE" else 0.8,
                       ls="--" if name[0] == "B" else "-", label=name)
        ax[i].axvspan(0, still_until, color="#eee")
        ax[i].set_ylabel(f"P(멀미 점수 ≥ {k})"); ax[i].set_ylim(0, 1)
        if labels is not None and len(labels):
            hit = labels[:, 1] >= k  # 승객 입력: 이 k 이상이면 검은 점이 위(1), 아니면 아래(0)
            ax[i].scatter(labels[:, 0], hit.astype(float), marker="v", color="k", s=40, zorder=5,
                          label=f"승객 입력 MISC (위 = {k} 이상)")
    ax[0].legend(loc="center right", fontsize=8); ax[1].legend(loc="center right", fontsize=8)
    ax[0].set_title("모델 위험 확률: 보정 전(빨강) vs 보정 후(파랑). 회색 = 출발 전 정지" + note)
    ax[1].set_xlabel("주행 시작부터 [초]")
    fig.tight_layout(); fig.savefig(path, dpi=130); plt.close(fig)


if __name__ == "__main__":
    main()
