"""실차 주행 기록 → trip.bin 행 + 라벨용 운동학 (리더 공통 부분).

리더(comma2k19.py, baton.py)는 데이터셋마다 다른 파일을 읽어 아래 모양의 dict 하나를 돌려준다.
시각은 모두 같은 시계(초)여야 하고, 축은 센서 그대로여도 된다 (여기서 정렬한다).

    {"name": str,
     "acc": (t, xyz),   # 비력 m/s², 중력 포함
     "gyr": (t, xyz),   # rad/s
     "speed": (t, v),   # m/s
     "extra": {trip 칸 이름: (t, x)}}   # 있으면 (페달, 크루즈 등)

정렬 (T-04 장착 보정과 같은 문제):
 1. 정차(속도 < 0.1 m/s) 구간의 평균 가속도를 중력으로 보고, 그 방향을 +z로 돌린다.
    정차가 10초 미만이면 주행 전체 평균을 쓴다 (경사·가감속 평균만큼 기울기 오차).
 2. 수평면 안의 회전 ψ는 IMU 수평 가속도를 CAN 기준 [dv/dt, v·ω_z]에 최소제곱으로 맞춰 정한다.
    ω_z는 1단계 뒤 자이로 z라 ψ와 무관하다. 센서 축이 오른손 좌표계라는 가정 (거울상은 못 고친다).
 3. 정차가 5초 이상이면 그 구간 자이로 중앙값을 바이어스로 뺀다.
 4. 남은 기울기(정차 지점의 경사)는 주행 중 평균 (IMU 수평 − 운동학)이 0이 되도록 한 번 더 돌린다.
    주행 전체 평균 경사가 0이라는 가정 (오르막만 가는 구간이면 그만큼 틀린다).
"""
import pathlib
import sys

import numpy as np
from scipy.signal import butter, filtfilt

ML = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ML / "simulator"))
sys.path.insert(0, str(ML))
import trip as tripio  # noqa: E402

FS = tripio.FS
DT = 1 / FS
G = 9.81
STILL_V = 0.1


def resample(src, grid):
    t, x = src
    t = np.asarray(t, float)
    x = np.asarray(x, float).reshape(len(t), -1)
    keep = np.r_[True, np.diff(t) > 0]  # 중복 시각 제거
    t, x = t[keep], x[keep]
    return np.column_stack([np.interp(grid, t, x[:, j]) for j in range(x.shape[1])])


def lowpass(x, hz, axis=0):
    b, a = butter(2, hz, fs=FS)
    return filtfilt(b, a, x, axis=axis)


def rot_to_z(g):
    """단위벡터 g를 +z로 보내는 회전행렬 (로드리게스)."""
    g = g / np.linalg.norm(g)
    z = np.array([0.0, 0.0, 1.0])
    v = np.cross(g, z)
    s, c = np.linalg.norm(v), float(g @ z)
    if s < 1e-12:
        return np.eye(3) if c > 0 else np.diag([1.0, -1.0, -1.0])
    k = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]]) / s
    return np.eye(3) + s * k + (1 - c) * k @ k


def align(acc, gyr, v):
    """센서축 → 차량축 (x 앞, y 왼쪽, z 위). → acc, gyr, 정보 dict"""
    still = v < STILL_V
    g = acc[still].mean(0) if still.sum() >= 10 * FS else acc.mean(0)
    R1 = rot_to_z(g)
    a1, w1 = acc @ R1.T, gyr @ R1.T
    bias = np.median(w1[still], axis=0) if still.sum() >= 5 * FS else np.zeros(3)
    w1 = w1 - bias
    ref0 = np.column_stack([np.gradient(lowpass(v, 0.5), DT), v * lowpass(w1[:, 2], 0.5)])
    ah = lowpass(a1[:, :2] - a1[:, :2].mean(0), 0.5)
    ref = ref0 - ref0.mean(0)
    za, zr = ah[:, 0] + 1j * ah[:, 1], ref[:, 0] + 1j * ref[:, 1]
    psi = float(np.angle(np.sum(zr * np.conj(za))))
    c, s = np.cos(psi), np.sin(psi)
    R2 = np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])
    # 4. 남은 기울기: 주행 중 평균 (IMU 수평 - 운동학)을 중력이 새어 든 몫으로 보고 지운다.
    #    정차 지점 한 곳의 경사 대신 주행 전체 평균 경사(≈0)를 기준으로 삼는 셈.
    a2 = a1 @ R2.T
    moving = ~still
    e = (lowpass(a2[:, :2], 0.5) - ref0)[moving].mean(0) if moving.any() else np.zeros(2)
    R3 = rot_to_z(np.array([e[0], e[1], np.linalg.norm(g)]))
    R = R3 @ R2 @ R1
    a, w = acc @ R.T, (gyr @ R1.T - bias) @ (R3 @ R2).T
    ahf = lowpass(a[:, :2] - a[:, :2].mean(0), 0.5)
    corr = [float(np.corrcoef(ahf[:, j], ref[:, j])[0, 1]) for j in range(2)]
    tilt = float(np.degrees(np.arccos(min(1.0, abs(g[2]) / np.linalg.norm(g)))))  # 센서 ±z축과 중력 사이
    return a, w, {"R": R.round(5).tolist(), "g_norm": float(np.linalg.norm(g)), "tilt_deg": tilt,
                  "tilt_fix_deg": float(np.degrees(np.arctan(np.hypot(*e) / np.linalg.norm(g)))),
                  "sensor_x_heading_deg": float(np.degrees(np.arctan2(R[1, 0], R[0, 0]))), "still_s": float(still.sum() / FS),
                  "corr_long": corr[0], "corr_lat": corr[1],
                  "gyro_bias": bias.round(5).tolist()}


def to_trip(raw):
    """리더 출력 → (rows n×32, motion, info). 시작·끝은 모든 신호가 있는 구간으로 자른다."""
    srcs = [raw["acc"], raw["gyr"], raw["speed"]]
    t0 = max(s[0][0] for s in srcs)
    t1 = min(s[0][-1] for s in srcs)
    grid = np.arange(t0, t1, DT)
    acc, gyr = resample(raw["acc"], grid), resample(raw["gyr"], grid)
    v = np.maximum(resample(raw["speed"], grid)[:, 0], 0)
    a, w, info = align(acc, gyr, v)
    C = tripio.COL
    rows = tripio.empty(len(grid))
    rows[:, C["ax"]], rows[:, C["ay"]], rows[:, C["az"]] = a.T
    rows[:, C["gx"]], rows[:, C["gy"]], rows[:, C["gz"]] = w.T
    rows[:, C["speed"]] = v
    for name, src in raw.get("extra", {}).items():
        rows[:, C[name]] = resample(src, grid)[:, 0]
    # 라벨 입력: 시뮬레이터 label()과 같이 중력 없는 운동학 가속도. 속도 미분과 v·ω_z
    t = rows[:, C["t"]]
    motion = {"t": t, "v": v, "a_long": np.gradient(v, DT), "a_lat": v * w[:, 2]}
    info.update(name=raw["name"], minutes=len(grid) / FS / 60,
                speed_mean=float(v.mean()), speed_max=float(v.max()))
    return rows, motion, info


def runs(seg_ids, min_len):
    """정수 목록 → 연속 구간 [(a, b), ...] (길이 min_len 이상만)."""
    s = sorted(seg_ids)
    out, a = [], 0
    for i in range(1, len(s) + 1):
        if i == len(s) or s[i] != s[i - 1] + 1:
            if i - a >= min_len:
                out.append(s[a:i])
            a = i
    return out
