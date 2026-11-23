"""주행 하나를 보정한다.

쓰는 법 (맥 터미널):
    .venv/bin/python run_calibration.py <주행 폴더> --tilt-window 316 324

읽는 것 (주행 폴더 안, 고치지 않음):
    raw_caraxis/samples_100hz.npy    원본 [t, ax, ay, az, gx, gy, gz, hold]
    logs/rides/*.sqlite              묶음마다의 Pi 시계 (시간축 보정용)
    export/meta.json                 센서 측정 범위

쓰는 것 (주행 폴더/calibrated/):
    calibration.json     무엇을 얼마나 보정했는지 (이 파일만 있으면 다시 만들 수 있다)
    samples_100hz.npy    보정된 데이터 [t, ax, ay, az, gx, gy, gz, flag]
    samples_100hz.csv    같은 내용 (PlotJuggler·엑셀용)
    samples_1s_mean.csv  1초 평균
    report.md            검증 결과
"""
import argparse
import glob
import json
import os
import sqlite3

import numpy as np

import imu_calib as ic


def load_ride(ride):
    raw = np.load(os.path.join(ride, "raw_caraxis", "samples_100hz.npy")).astype(float)
    db = glob.glob(os.path.join(ride, "logs", "rides", "*.sqlite"))[0]
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)          # 읽기 전용으로 연다
    rows = np.array(con.execute("select seq0, mono_ns0 from samples order by seq0").fetchall(), dtype=np.int64)
    meta = dict(con.execute("select key, value from meta").fetchall())
    con.close()
    return raw, rows[:, 0], rows[:, 1], meta


def fmt(v):
    return "(" + ", ".join(f"{x:+.3f}" for x in v) + ")"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ride")
    ap.add_argument("--tilt-window", nargs=2, type=float, metavar=("T0", "T1"), required=True,
                    help="기울기를 구할 정지 구간 [원본 시각, 초]. 모두 탄 뒤 출발 직전")
    ap.add_argument("--yaw-window", nargs=2, type=float, metavar=("T0", "T1"),
                    help="방향(요)을 추정할 평지 주행 구간 [초]")
    ap.add_argument("--drive-window", nargs=2, type=float, metavar=("T0", "T1"),
                    help="검증(V3 등)에 쓸 주행 구간 [원본 시각, 초]. 주행 전후에 센서를 만졌으면 꼭 지정")
    ap.add_argument("--apply-yaw", action="store_true", help="추정한 방향 보정을 실제로 적용")
    g3 = ap.add_mutually_exclusive_group()
    g3.add_argument("--six-face", help="3단계: 6면 측정 결과 json (six_face.py 가 만든 것, bias·scale)")
    g3.add_argument("--z-scale-1pt", action="store_true",
                    help="3단계 임시: 기울기 기준 구간의 정지 값으로 z축 눈금만 바로잡기 (센서를 못 뗄 때)")
    args = ap.parse_args()

    raw, seq0, mono_ns0, meta = load_ride(args.ride)
    t, acc, gyro, hold = raw[:, 0], raw[:, 1:4], raw[:, 4:7], raw[:, 7]
    lines = ["# 보정 검증 보고", "", f"주행: `{os.path.abspath(args.ride)}`", f"샘플 {len(t):,}개", ""]

    # ---- 보정 전 정지 구간 (검증의 기준이 된다)
    still = ic.find_still_windows(t, acc, gyro)
    d0, d1 = args.drive_window if args.drive_window else (0.0, float(t[-1]))
    # 기울기 기준 구간과 주행 구간 안의 정지만 검증에 쓴다 (주행 뒤 센서를 만진 구간 제외)
    still = [w for w in still if (w[0] < args.tilt_window[1] and w[1] > args.tilt_window[0]) or (w[0] >= d0 and w[1] <= d1)]

    # ---- 3단계: 센서 자체 오차 (센서 축 기준이라 가장 먼저)
    # 치우침·눈금은 센서 칩의 축(x, y, z)에 붙은 값이다. 원본은 차 축으로 이름만 바꾼 것이라
    # (raw_caraxis/transform.json 의 R: 차 = R · 센서) 센서 축으로 되돌려 적용하고 다시 차 축으로 돌린다.
    tf_path = os.path.join(args.ride, "raw_caraxis", "transform.json")
    R_cs = np.array(json.load(open(tf_path))["R_car_from_sensor"], float) if os.path.exists(tf_path) else np.eye(3)
    acc_s = acc @ R_cs                     # 차 축 → 센서 축 (각 샘플 v 에 대해 Rᵀ·v)
    bias, scale, mode3 = [0, 0, 0], [1, 1, 1], "건너뜀 (센서 오차 측정 없음)"
    if args.six_face:
        sf = json.load(open(args.six_face))
        bias, scale = sf["bias"], sf["scale"]
        mode3 = f"6면 측정값 적용 ({args.six_face})"
    elif args.z_scale_1pt:
        bias, scale = ic.one_point_z_scale(ic.window_mean(t, acc_s, *args.tilt_window))
        mode3 = f"임시 1점 보정: z축 눈금 × {scale[2]:.5f} (x·y축 눈금은 모름, 6면 측정 필요)"
    acc1 = ic.apply_sensor_model(acc_s, bias, scale) @ R_cs.T   # 센서 축에서 바로잡고 차 축으로

    # ---- 1단계: 장착 기울기
    t0, t1 = args.tilt_window
    m_ref = ic.window_mean(t, acc1, t0, t1)
    R_tilt = ic.tilt_rotation(m_ref)
    acc2 = acc1 @ R_tilt.T          # 각 샘플 v 에 대해 R·v
    gyro2 = gyro @ R_tilt.T
    pitch, roll = ic.tilt_angles_deg(m_ref)

    # ---- 2단계: 방향(요)
    yaw_deg, yaw_se, yaw_n = 0.0, None, 0
    R_yaw = np.eye(3)
    if args.yaw_window:
        yaw_deg, yaw_se, yaw_n = ic.estimate_yaw_offset(t, acc2, gyro2, *args.yaw_window)
        if args.apply_yaw:
            R_yaw = ic.yaw_rotation(-yaw_deg)
    acc3 = acc2 @ R_yaw.T
    gyro3 = gyro2 @ R_yaw.T

    # ---- 6단계: 이상값 표시 (시간축을 다시 뽑기 전에, 원래 샘플 기준으로)
    flag = ic.flag_samples(acc, gyro, hold, float(meta.get("accel_range_g", 4)),
                           float(meta.get("gyro_range_dps", 250)))

    # ---- 4단계: 시간축
    t_true, t_resid = ic.true_sample_times(seq0, mono_ns0, int(meta["mono_start_ns"]), len(t))
    t_new, data_new = ic.resample_uniform(t_true, np.column_stack([acc3, gyro3]))
    idx = np.clip(np.searchsorted(t_true, t_new), 0, len(t) - 1)   # 표시는 가까운 원래 샘플 것을 따른다
    flag_new = flag[idx]
    acc_c, gyro_c = data_new[:, :3], data_new[:, 3:]

    # ================================================================ 검증
    to_new = lambda x: float(np.interp(x, t, t_true))     # 원본 시각 → 보정 시각

    lines += ["## 적용한 보정", "",
              f"- 3단계 센서 오차: {mode3}",
              f"- 1단계 기울기: 기준 구간 {t0:.0f}~{t1:.0f}초, 정지 평균 {fmt(m_ref)} m/s² → 앞뒤 {pitch:+.2f}도, 좌우 {roll:+.2f}도",
              f"- 2단계 방향: " + (f"추정 {yaw_deg:+.2f}도 (표준오차 {yaw_se:.2f}도, {yaw_n}초 사용), "
                                + ("적용함" if args.apply_yaw else "**적용 안 함**") if args.yaw_window else "건너뜀"),
              f"- 4단계 시간축: 실제 {t_true[-1]:.2f}초 (샘플 번호로는 {t[-1]:.2f}초), 평균 {(len(t) - 1) / t_true[-1]:.3f} Hz → 100 Hz로 다시 뽑아 {len(t_new):,}개",
              f"- 6단계 표시: 빠진 샘플 {int((flag & 1).sum())}개, 포화 {int((flag >> 1 & 1).sum())}개 (지우지 않음)", ""]

    lines += ["## V1·V2 정지 구간의 값 (평지라면 보정 후 수평이 0이어야 한다)", "",
              "| 정지 구간 [초] | 보정 전 (ax, ay, az) | 보정 후 (ax, ay, az) | 수평 크기 전 → 후 | 비고 |", "|---|---|---|---|---|"]
    still_out = []
    for (a, b, m) in still:
        mc = ic.window_mean(t_new, acc_c, to_new(a), to_new(b))
        note = "기준 구간" if (a < t1 and b > t0) else ""
        lines.append(f"| {a:.0f}~{b:.0f} | {fmt(m)} | {fmt(mc)} | {np.hypot(m[0], m[1]):.3f} → {np.hypot(mc[0], mc[1]):.3f} | {note} |")
        still_out.append({"t0": a, "t1": b, "before": m.tolist(), "after": mc.tolist()})
    mags = np.array([np.linalg.norm(s["after"]) for s in still_out])
    lines += ["", f"가속도 크기 (기대 {ic.G:.3f}): 정지 구간 {len(mags)}곳 평균 {mags.mean():.4f}, 최소 {mags.min():.4f}, 최대 {mags.max():.4f}"]
    lines += ["", "기준 구간이 아닌 곳의 '보정 후' 수평 값은 그 자리의 바닥 경사(+ 차체 기울어짐)다. 1 m/s² ≈ 5.8도.", ""]

    # V3: 정지 구간 앞 3초(제동)와 뒤 3초(출발)의 ax 부호, 좌우 가속도와 회전의 관계
    brake, launch = [], []
    for (a, b, _) in still:
        if a > t1:
            brake.append(ic.window_mean(t_new, acc_c, to_new(a) - 3, to_new(a))[0])
        if b >= t1 and b + 3 < t[-1]:
            launch.append(ic.window_mean(t_new, acc_c, to_new(b), to_new(b) + 3)[0])
    n1 = len(t_new) // 100
    ay1 = acc_c[:n1 * 100, 1].reshape(n1, 100).mean(axis=1)
    gz1 = gyro_c[:n1 * 100, 2].reshape(n1, 100).mean(axis=1)
    in_drive = (np.arange(n1) >= to_new(d0)) & (np.arange(n1) < to_new(d1))
    turning = (np.abs(np.degrees(gz1)) > 3) & in_drive
    corr = float(np.corrcoef(ay1[turning], gz1[turning])[0, 1])
    lines += ["## V3 부호" + (f" (주행 구간 {d0:.0f}~{d1:.0f}초)" if args.drive_window else ""), "",
              f"- 출발 직후 3초의 ax: 평균 {np.mean(launch):+.2f} m/s², {sum(x > 0 for x in launch)}/{len(launch)}번이 + (기대: +)",
              f"- 정차 직전 3초의 ax: 평균 {np.mean(brake):+.2f} m/s², {sum(x < 0 for x in brake)}/{len(brake)}번이 − (기대: −)",
              f"- 회전 중(3도/초 이상) 좌우 가속도와 회전 속도의 상관: {corr:+.2f} (기대: +, 좌회전이면 둘 다 +)", ""]

    # V9: 처음과 마지막 정지 구간 비교 (같은 자리로 돌아왔을 때만 뜻이 있다)
    if len(still) >= 2:
        d = np.array(still_out[-1]["after"]) - np.array(still_out[0]["after"])
        lines += ["## V9 처음과 마지막 정지 값의 차이", "",
                  f"- {fmt(d)} m/s². 같은 자리로 돌아온 주행이 아니면 바닥 경사 차이가 섞여 있다.", ""]

    lines += ["## V10 시간", "",
              f"- 묶음 시계와 맞춘 곡선의 차이(표준편차) {t_resid * 1000:.1f} ms",
              f"- 보정 후 길이 {len(t_new) / 100:.2f}초, Pi 시계로 잰 길이 {t_true[-1]:.2f}초", ""]

    # ================================================================ 저장
    out = os.path.join(args.ride, "calibrated")
    os.makedirs(out, exist_ok=True)
    arr = np.column_stack([t_new, acc_c, gyro_c, flag_new]).astype(np.float32)
    np.save(os.path.join(out, "samples_100hz.npy"), arr)
    np.savetxt(os.path.join(out, "samples_100hz.csv"), arr, delimiter=",", comments="",
               header="t_s,ax,ay,az,gx,gy,gz,flag", fmt=["%.2f"] + ["%.5f"] * 6 + ["%d"])
    k = len(t_new) // 100
    sec = arr[:k * 100, :7].reshape(k, 100, 7).mean(axis=1)
    sec[:, 0] = np.arange(k)
    np.savetxt(os.path.join(out, "samples_1s_mean.csv"), sec, delimiter=",", comments="",
               header="t_s,ax,ay,az,gx,gy,gz", fmt=["%d"] + ["%.5f"] * 6)

    calib = {
        "input": "raw_caraxis/samples_100hz.npy",
        "order": "sensor model -> tilt -> yaw -> flags -> time resample",
        "accel_bias": list(map(float, bias)), "accel_scale": list(map(float, scale)),
        "sensor_model_axes": "센서 축 (차 = R_car_from_sensor · 센서)", "R_car_from_sensor": R_cs.tolist(), "stage3_mode": mode3,
        "tilt": {"window_s": [t0, t1], "mean_acc_still": m_ref.tolist(),
                 "pitch_deg": pitch, "roll_deg": roll, "R": R_tilt.tolist()},
        "yaw": {"window_s": args.yaw_window, "estimated_deg": yaw_deg, "stderr_deg": yaw_se,
                "seconds_used": yaw_n, "applied": bool(args.apply_yaw), "R": R_yaw.tolist()},
        "R_total": (R_yaw @ R_tilt).tolist(),
        "time": {"true_duration_s": float(t_true[-1]), "nominal_duration_s": float(t[-1]),
                 "mean_rate_hz": float((len(t) - 1) / t_true[-1]), "fit_resid_ms": t_resid * 1000},
        "flags": {"bit1_hold": int((flag & 1).sum()), "bit2_saturated": int((flag >> 1 & 1).sum())},
        "gravity_component": "not removed (stage 5 undecided)",
        "still_windows": still_out,
    }
    json.dump(calib, open(os.path.join(out, "calibration.json"), "w"), indent=1, ensure_ascii=False)
    open(os.path.join(out, "report.md"), "w").write("\n".join(lines) + "\n")
    print("\n".join(lines))
    print("저장:", out)


if __name__ == "__main__":
    main()
