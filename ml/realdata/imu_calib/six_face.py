"""3단계: 6면 정지 측정으로 가속도 센서의 치우침·눈금을 구한다.

쓰는 법 (맥 터미널):
    .venv/bin/python six_face.py <측정 기록 .sqlite> <결과 .json>

측정 방법은 ../6면_측정_절차.md 참고. 기록은 Pi의 주행 기록(P1 + P7)을 그대로 쓴다.
센서를 여섯 방향으로 각각 20초 이상 가만히 두면, 이 스크립트가 정지 구간을 찾아
어느 축이 위를 보고 있었는지 스스로 알아낸다. 시각을 적어 둘 필요는 없다.

결과 json 은 run_calibration.py --six-face 에 넣는다.
센서 고유의 값이라 다시 붙여도 그대로 쓸 수 있다(센서를 바꾸면 다시 측정).
"""
import json
import sqlite3
import sys

import numpy as np

import imu_calib as ic

FACES = ["+x", "-x", "+y", "-y", "+z", "-z"]   # solve_six_face 가 기대하는 순서


def load_sensor_axes(db):
    """P7 기록을 읽어 '센서 축' 값으로 돌려준다.

    P1 은 imu_mount.json 의 R 로 센서 축 → 차 축 변환을 한 뒤 기록한다.
    눈금·치우침은 센서 축 기준이므로 R 을 거꾸로(Rᵀ) 적용해 되돌린다.
    """
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    meta = dict(con.execute("select key, value from meta").fetchall())
    rows = con.execute("select n, data from samples order by seq0").fetchall()
    con.close()
    d = np.concatenate([np.frombuffer(b, "<f4").reshape(n, 6) for n, b in rows]).astype(float)
    R = np.array(json.loads(meta.get("imu_mount", '{"R": [[1,0,0],[0,1,0],[0,0,1]]}'))["R"], float)
    acc, gyro = d[:, :3] @ R, d[:, 3:] @ R          # 각 행 v 에 대해 Rᵀ·v
    t = np.arange(len(d)) / 100.0
    return t, acc, gyro, meta


def analyze(t, acc, gyro, min_len_s=15.0):
    """정지 구간을 찾아 면마다 하나씩 고르고 치우침·눈금을 푼다."""
    still = ic.find_still_windows(t, acc, gyro, min_len_s=min_len_s, gyro_max_dps=0.6, acc_sd_max=0.05)
    best = {}
    for a, b, m in still:
        i = int(np.argmax(np.abs(m)))
        if abs(m[i]) < 0.9 * ic.G:                 # 한 축이 거의 똑바로 위·아래가 아니면 버림
            continue
        face = ("+" if m[i] > 0 else "-") + "xyz"[i]
        if face not in best or (b - a) > (best[face][1] - best[face][0]):
            best[face] = (a, b, m)
    missing = [f for f in FACES if f not in best]
    if missing:
        raise SystemExit(f"이 면의 정지 구간을 찾지 못함: {missing}. 각 면을 {min_len_s:.0f}초 넘게 가만히 두었는지 확인")

    means = np.array([best[f][2] for f in FACES])
    bias, scale = ic.solve_six_face(means)

    # 검증: 보정 후 여섯 면의 크기가 모두 G 이어야 한다.
    # 풀이에 쓴 것은 각 면의 '위를 보는 축' 한 칸뿐이라, 크기 전체는 독립된 확인이 된다.
    after = ic.apply_sensor_model(means, bias, scale)
    report = []
    for f, (a, b, m), ma in zip(FACES, [best[f] for f in FACES], after):
        report.append({"face": f, "t0": a, "t1": b, "mean_before": m.tolist(),
                       "norm_before": float(np.linalg.norm(m)), "norm_after": float(np.linalg.norm(ma)),
                       "gyro_mean_dps": np.degrees(ic.window_mean(t, gyro, a, b)).tolist()})
    return bias, scale, report


def main():
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    t, acc, gyro, meta = load_sensor_axes(sys.argv[1])
    bias, scale, report = analyze(t, acc, gyro)
    worst = max(abs(r["norm_after"] - ic.G) for r in report)
    out = {"source": sys.argv[1], "ride_id": meta.get("ride_id"), "wall_start": meta.get("wall_start"),
           "model": "a_corrected = (a_measured - bias) * scale, sensor axes",
           "bias": bias.tolist(), "scale": scale.tolist(),
           "check_max_norm_error": worst, "faces": report}
    json.dump(out, open(sys.argv[2], "w"), indent=1, ensure_ascii=False)

    print("면    구간[초]        평균 (x, y, z) [m/s²]           크기 전 → 후")
    for r in report:
        m = r["mean_before"]
        print(f"{r['face']}  {r['t0']:6.0f}~{r['t1']:<6.0f} ({m[0]:+7.3f}, {m[1]:+7.3f}, {m[2]:+7.3f})   {r['norm_before']:.4f} → {r['norm_after']:.4f}")
    print(f"\n치우침 bias  (x, y, z) = ({bias[0]:+.4f}, {bias[1]:+.4f}, {bias[2]:+.4f}) m/s²")
    print(f"눈금 scale   (x, y, z) = ({scale[0]:.5f}, {scale[1]:.5f}, {scale[2]:.5f})")
    print(f"검증: 보정 후 크기와 {ic.G:.3f}의 차이 최대 {worst:.4f} m/s² ({'합격' if worst < 0.03 else '불합격: 면이 기울었거나 센서 축끼리 어긋남'}, 기준 0.03)")
    print("저장:", sys.argv[2])


if __name__ == "__main__":
    main()
