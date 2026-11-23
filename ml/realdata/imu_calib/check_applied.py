"""보정 결과(calibrated/)에 1~6단계가 실제로 들어갔는지 따로 확인한다.

run_calibration.py 의 계산을 다시 부르지 않고, calibration.json 에 적힌 값과
원본(raw_caraxis)만으로 결과를 다시 만들어 calibrated/samples_100hz.npy 와 비교한다.

쓰는 법 (imu_calib 폴더에서):
    .venv/bin/python check_applied.py <주행 폴더>
"""
import json
import os
import sys

import glob
import sqlite3

import numpy as np

from imu_calib import true_sample_times  # 4단계 시계 맞추기만 같은 함수를 쓴다 (나머지는 여기서 따로 계산)

G = 9.80665


def sec_mean(t, x, t0, t1):
    m = (t >= t0) & (t < t1)
    return x[m].mean(axis=0)


def main(ride):
    raw = np.load(os.path.join(ride, "raw_caraxis", "samples_100hz.npy")).astype(float)
    cal = np.load(os.path.join(ride, "calibrated", "samples_100hz.npy")).astype(float)
    info = json.load(open(os.path.join(ride, "calibrated", "calibration.json")))
    t, acc, gyro = raw[:, 0], raw[:, 1:4], raw[:, 4:7]
    tc, accc, gyroc, flag = cal[:, 0], cal[:, 1:4], cal[:, 4:7], cal[:, 7].astype(int)
    ok = lambda c: "통과" if c else "**확인 필요**"
    out = [f"# 단계 적용 확인: `{ride}`", "", f"적용 순서(기록): {info['order']}", ""]

    # 3단계: 센서 오차 (치우침, 눈금)
    bias, scale = np.array(info["accel_bias"]), np.array(info["accel_scale"])
    R_cs = np.array(info.get("R_car_from_sensor", np.eye(3).tolist()))  # 치우침·눈금은 센서 축 값
    acc3 = ((acc @ R_cs - bias) * scale) @ R_cs.T
    t0, t1 = info["tilt"]["window_s"]
    mag_before = np.linalg.norm(sec_mean(t, acc, t0, t1))
    mag_after = np.linalg.norm(sec_mean(t, acc3, t0, t1))
    out += [f"- 센서 축 → 차 축: {R_cs.astype(int).tolist()} (치우침·눈금은 센서 축 기준으로 적용)"]
    out += ["## 3단계 센서 오차", f"- 적용 값: 치우침 {bias.round(4).tolist()}, 눈금 {scale.round(5).tolist()} ({info['stage3_mode']})",
            f"- 기준 정지 구간의 가속도 크기: 원본 {mag_before:.4f} → 적용 후 {mag_after:.4f} (기대 {G}) → {ok(abs(mag_after - G) < 0.005)}", ""]

    # 1단계 + 2단계: 회전
    R_tilt, R_yaw, R_tot = (np.array(info["tilt"]["R"]), np.array(info["yaw"]["R"]), np.array(info["R_total"]))
    mref = sec_mean(t, acc3, t0, t1) @ R_tilt.T
    out += ["## 1단계 장착 기울기",
            f"- 앞뒤 {info['tilt']['pitch_deg']:+.2f}도, 좌우 {info['tilt']['roll_deg']:+.2f}도",
            f"- 회전 행렬이 진짜 회전인가 (RᵀR = I, det = 1): {ok(np.allclose(R_tilt.T @ R_tilt, np.eye(3), atol=1e-9) and abs(np.linalg.det(R_tilt) - 1) < 1e-9)}",
            f"- 기준 구간을 돌리면 {np.round(mref, 4).tolist()} (기대 [0, 0, {G}]) → {ok(np.abs(mref[:2]).max() < 1e-4)}", "",
            "## 2단계 방향(요)",
            f"- 추정 {info['yaw']['estimated_deg']:+.2f}도 ± {info['yaw']['stderr_deg']:.2f}도, 적용: {info['yaw']['applied']}",
            f"- 전체 회전 = 2단계 회전 × 1단계 회전 인가: {ok(np.allclose(R_tot, R_yaw @ R_tilt, atol=1e-12))}", ""]

    # 4단계: 시간축 — 길이와 간격
    tm = info["time"]
    dt = np.diff(tc)
    out += ["## 4단계 시간축",
            f"- 원본 샘플 수 {len(t):,} (번호로 {t[-1]:.2f}초) → 실제 시계 {tm['true_duration_s']:.2f}초, 평균 {tm['mean_rate_hz']:.3f} Hz",
            f"- 보정 후 {len(tc):,}개, 간격 {dt.min():.4f}~{dt.max():.4f}초, 길이 {tc[-1]:.2f}초 → "
            f"{ok(np.allclose(dt, 0.01, atol=5e-4) and abs(tc[-1] - tm['true_duration_s']) < 0.02)}",
            "  (파일이 float32라 2000초대에서 시각 끝자리가 0.0002초쯤 흔들린다)", ""]

    # 값 전체 재현: 3단계 → 회전 → Pi 묶음 시계로 맞춘 실제 시각에서 100 Hz로 다시 뽑기
    db = glob.glob(os.path.join(ride, "logs", "rides", "*.sqlite"))[0]
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = np.array(con.execute("select seq0, mono_ns0 from samples order by seq0").fetchall(), dtype=np.int64)
    start = int(dict(con.execute("select key, value from meta").fetchall())["mono_start_ns"])
    con.close()
    t_true, _ = true_sample_times(rows[:, 0], rows[:, 1], start, len(t))
    grid = np.arange(len(tc)) * 0.01  # 파일의 t 열은 float32라 정확한 0.01초 격자를 다시 만든다
    rec = np.column_stack([np.interp(grid, t_true, c) for c in np.column_stack([acc3 @ R_tot.T, gyro @ R_tot.T]).T])
    da = np.abs(rec[:, :3] - accc).max(axis=0)
    dg = np.degrees(np.abs(rec[:, 3:] - gyroc).max(axis=0))
    out += ["## 전체 재현 (원본 + calibration.json 값으로 다시 만든 것 vs calibrated, 샘플마다)",
            f"- 가속도 최대 차이 {da.max():.5f} m/s², 자이로 최대 차이 {dg.max():.5f} 도/초 → {ok(da.max() < 1e-3 and dg.max() < 1e-2)}",
            "  (float32로 저장해 생기는 끝자리 차이만 남아야 한다)", ""]

    # 5단계: 도로 경사 중력 — 적용 안 함
    out += ["## 5단계 도로 경사 중력", f"- 기록: {info['gravity_component']}",
            f"- 보정 후 정지 구간 az {sec_mean(tc, accc, t0, t1)[2]:.3f} (중력 9.81이 그대로 남아 있음 = 빼지 않았음)", ""]

    # 6단계: 표시
    out += ["## 6단계 이상값 표시",
            f"- 기록: 빠진 샘플 {info['flags']['bit1_hold']}개, 포화 {info['flags']['bit2_saturated']}개",
            f"- 보정 파일 flag 열: 비트1 {int((flag & 1).sum())}개, 비트2 {int((flag >> 1 & 1).sum())}개 (시간축을 다시 뽑아 개수가 조금 다를 수 있음)",
            f"- 값은 지우지 않고 표시만 함: 행 수 {len(tc):,} = 4단계 결과와 같음", ""]
    print("\n".join(out))


if __name__ == "__main__":
    main(sys.argv[1])
