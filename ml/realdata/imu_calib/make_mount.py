"""주행 전에 Pi에 넣을 장착 회전(imu_mount.json)을 만든다.

Pi의 P1은 시작할 때 controller/config/imu_mount.json 의 R(3×3)을 읽고, 샘플마다
    차 축(x 앞, y 왼쪽, z 위) 값 = R · 센서 축 값
으로 바꿔 P2(피처) → P3(모델)에 넘긴다. 지금까지는 R = 단위 행렬이라 센서가 기울거나 돌아가 붙어 있으면
그대로 모델에 들어갔다. 이 스크립트는 차를 세운 채 짧게 기록한 파일로 R을 계산한다.

    R = R_tilt · R_axes
    R_axes : 센서 축이 차의 어느 쪽을 보는지 (사람이 알려 줌, --forward/--left). 중력만으로는 알 수 없다.
    R_tilt : 정지 평균 가속도 m(R_axes 적용 후)을 z축으로 보내는 최단 회전 (run_calibration.py 1단계와 같은 식)

바닥 기울기: 기록한 자리의 바닥이 기울어 있으면 그 기울기가 장착 기울기로 들어간다.
    같은 자리에서 차 방향만 180도 돌려 한 번 더 기록해 두 번째 파일로 주면, 두 정지 평균의 수평 성분을
    평균해 바닥 기울기를 지운다 (차 축에서 장착 기울기는 그대로, 바닥 기울기는 부호가 바뀐다).

입력 기록의 값은 그때 Pi에 들어 있던 imu_mount R로 이미 돌아가 있다. meta의 imu_mount를 읽어 센서 축으로 되돌린 뒤 계산한다.

쓰는 법 (imu_calib 폴더에서):
    .venv/bin/python make_mount.py <기록 .sqlite 또는 주행 폴더> [<180도 돌린 기록>] \
        [--forward +x] [--left +y] [--window 시작 끝] [--out imu_mount.json]
    예) 센서 y축이 차 앞, 센서 −x축이 차 왼쪽 (11-21 장착):  --forward=+y --left=-x
        (값이 '-'로 시작하므로 '=' 를 붙여 쓴다)
    축 방향(--forward/--left)은 정지 기록으로 확인할 수 없다. 틀리게 주면 기울기는 맞아도 앞뒤·좌우가 뒤바뀐다.
    주행 뒤 run_calibration.py 의 V3(출발 때 ax가 +인가, 좌회전 때 ay와 gz가 같은 부호인가)로 확인한다.
"""
import argparse
import datetime
import glob
import json
import os
import sqlite3
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import imu_calib as ic  # noqa: E402

AXES = {"+x": [1, 0, 0], "-x": [-1, 0, 0], "+y": [0, 1, 0], "-y": [0, -1, 0], "+z": [0, 0, 1], "-z": [0, 0, -1]}


def axes_rotation(forward, left):
    """센서 축 → 차 축 (축 순서·부호만). 행 i = 차 축 i 방향을 센서 축으로 쓴 것"""
    f, l = np.array(AXES[forward], float), np.array(AXES[left], float)
    if abs(f @ l) > 0:
        raise SystemExit(f"--forward {forward} 와 --left {left} 가 같은 축이다")
    return np.vstack([f, l, np.cross(f, l)])


def undo_accel_cal(meta, acc_sensor):
    """Pi가 가속도 센서 오차 보정(P1 --accel-cal)을 켜고 기록했으면 되돌린다: 원래 값 = 보정값 / scale + bias"""
    if "accel_cal" not in meta:
        return acc_sensor
    c = json.loads(meta["accel_cal"])
    return acc_sensor / np.asarray(c["scale"], float) + np.asarray(c["bias"], float)


def load_sensor(path):
    """기록 → (t, 센서 축 가속도(센서 오차 보정 전 원래 값), 센서 축 자이로, 기록 당시 R)"""
    db = path if path.endswith(".sqlite") else sorted(glob.glob(os.path.join(path, "logs", "rides", "*.sqlite")))[0]
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    meta = dict(con.execute("select key, value from meta").fetchall())
    rows = con.execute("select n, data from samples order by seq0").fetchall()
    con.close()
    d = np.concatenate([np.frombuffer(b, "<f4").reshape(n, 6) for n, b in rows]).astype(float)
    R_rec = np.array(json.loads(meta.get("imu_mount", '{"R": [[1,0,0],[0,1,0],[0,0,1]]}'))["R"], float)
    t = np.arange(len(d)) / 100.0
    return db, t, undo_accel_cal(meta, d[:, :3] @ R_rec), d[:, 3:] @ R_rec, R_rec    # 각 행 v 에 Rᵀ·v → 센서 축


def still_mean(t, acc, gyro, window):
    if window:
        m = ic.window_mean(t, acc, *window)
        return m, tuple(window)
    st = ic.find_still_windows(t, acc, gyro)
    if not st:
        raise SystemExit("정지 구간(3초 이상)을 찾지 못함. 차를 세운 채 다시 기록하거나 --window 로 지정")
    a, b, m = max(st, key=lambda s: s[1] - s[0])
    return m, (a, b)


def angles(m):
    """차 축 정지 평균 → (앞뒤, 좌우) 기울기 [도]"""
    return float(np.degrees(np.arctan2(m[0], m[2]))), float(np.degrees(np.arctan2(m[1], m[2])))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("record")
    ap.add_argument("record_turned", nargs="?", help="같은 자리에서 차를 180도 돌려 기록한 것 (바닥 기울기 지우기)")
    ap.add_argument("--forward", default="+x", choices=AXES, help="차 앞을 보는 센서 축")
    ap.add_argument("--left", default="+y", choices=AXES, help="차 왼쪽을 보는 센서 축")
    ap.add_argument("--window", type=float, nargs=2, help="정지 구간 [초] (기본: 가장 긴 정지 구간)")
    ap.add_argument("--window-turned", type=float, nargs=2)
    ap.add_argument("--accel-cal", help="Pi가 P1 --accel-cal(ACCEL_CAL)로 달릴 때 쓸 센서 오차 파일 (sensor_6face.json). "
                                        "주면 그 보정을 한 값으로 기울기를 잰다 (Pi와 같은 순서: 센서 오차 → R)")
    ap.add_argument("--max-deg", type=float, default=10.0, help="기울기가 이보다 크면 멈춘다 (차가 서 있지 않았거나 센서가 눕혀 붙은 경우)")
    ap.add_argument("--out", default="imu_mount.json")
    a = ap.parse_args()

    R_axes = axes_rotation(a.forward, a.left)
    cal = None
    if a.accel_cal:
        c = json.load(open(a.accel_cal))
        cal = (np.asarray(c["bias"], float), np.asarray(c["scale"], float))
    fix = (lambda x: (x - cal[0]) * cal[1]) if cal else (lambda x: x)
    db1, t, acc, gyro, R_rec = load_sensor(a.record)
    acc = fix(acc)
    m1_s, w1 = still_mean(t, acc, gyro, a.window)
    m1 = R_axes @ m1_s
    lines = [f"기록 1: {db1}", f"  기록 당시 Pi R = {R_rec.tolist()}",
             f"  정지 구간 {w1[0]:.0f}~{w1[1]:.0f}초" + (f", 센서 오차 보정 {a.accel_cal} 적용" if cal else "")
             + (f", Pi가 낸 값(기록 그대로) 평균 {np.round(R_rec @ m1_s, 3).tolist()}" if not cal else ""),
             f"  센서 축 평균 {np.round(m1_s, 3).tolist()}, 크기 {np.linalg.norm(m1_s):.3f}",
             f"  축만 맞춘 차 축 평균 {np.round(m1, 3).tolist()} → 앞뒤 {angles(m1)[0]:+.2f}도, 좌우 {angles(m1)[1]:+.2f}도"]
    m = m1
    floor = None
    if a.record_turned:
        db2, t2, acc2, gyro2, R_rec2 = load_sensor(a.record_turned)
        acc2 = fix(acc2)
        m2_s, w2 = still_mean(t2, acc2, gyro2, a.window_turned)
        m2 = R_axes @ m2_s
        lines += [f"기록 2 (180도 돌림): {db2}",
                  f"  정지 구간 {w2[0]:.0f}~{w2[1]:.0f}초, 차 축 평균 {np.round(m2, 3).tolist()} → 앞뒤 {angles(m2)[0]:+.2f}도, 좌우 {angles(m2)[1]:+.2f}도"]
        m = (m1 + m2) / 2.0
        floor = (m1 - m2) / 2.0
        lines += [f"  두 기록 평균 → 장착 기울기 앞뒤 {angles(m)[0]:+.2f}도, 좌우 {angles(m)[1]:+.2f}도",
                  f"  바닥 기울기 (기록 1 자리, 차 축) 앞뒤 {np.degrees(floor[0] / 9.80665):+.2f}도, 좌우 {np.degrees(floor[1] / 9.80665):+.2f}도"]

    tilt_deg = float(np.degrees(np.arccos(np.clip(m[2] / np.linalg.norm(m), -1, 1))))
    if tilt_deg > a.max_deg:
        print("\n".join(lines))
        raise SystemExit(f"기울기 {tilt_deg:.1f}도 > {a.max_deg}도. 차가 서 있었는지, 센서가 위(+z)를 보게 붙었는지 확인")
    R_tilt = ic.tilt_rotation(m)
    R = R_tilt @ R_axes
    # Pi(parse_mount)와 같은 검사: R·Rᵀ = I, det = +1 (1e-3 안)
    assert np.abs(R @ R.T - np.eye(3)).max() < 1e-6 and abs(np.linalg.det(R) - 1) < 1e-6
    after = R @ m1_s
    lines += ["", f"전체 기울기 {tilt_deg:.2f}도",
              f"새 R 적용 시 기록 1 정지 평균 {np.round(after, 3).tolist()}" +
              (" (바닥 기울기만큼 남는 것이 정상)" if a.record_turned else " (0, 0, 크기)가 되어야 함")]

    out = {"version": 1, "R": [[round(float(x), 6) for x in row] for row in R],
           "note": f"차량 축(x 앞, y 왼쪽, z 위) = R · 센서 축. make_mount.py {datetime.date.today().isoformat()}: "
                   f"축 forward={a.forward} left={a.left}, 기울기 앞뒤 {angles(m)[0]:+.2f}도 좌우 {angles(m)[1]:+.2f}도"
                   + (" (180도 돌린 두 기록 평균)" if a.record_turned else " (기록 1개, 바닥 기울기 포함)")
                   + (f", 센서 오차 보정 후 ({os.path.basename(a.accel_cal)}) — Pi에서 ACCEL_CAL과 함께 쓴다" if cal else ""),
           "source": [os.path.basename(db1)] + ([os.path.basename(db2)] if a.record_turned else [])}
    rows = ",\n        ".join("[" + ", ".join(f"{x:.6f}" for x in r) + "]" for r in out["R"])
    text = ("{\n  \"version\": 1,\n  \"R\": [" + rows + "],\n  \"note\": " + json.dumps(out["note"], ensure_ascii=False)
            + ",\n  \"source\": " + json.dumps(out["source"]) + "\n}\n")
    assert json.loads(text) == out
    with open(a.out, "w") as f:
        f.write(text)
    lines += ["", f"저장: {os.path.abspath(a.out)}", json.dumps(out["R"])]
    print("\n".join(lines))


if __name__ == "__main__":
    main()
