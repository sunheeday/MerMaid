"""IMU 보정 함수 모음.

좌표: x 앞, y 왼쪽, z 위.  가속도 m/s² (중력 포함), 자이로 rad/s.
배열 모양: acc, gyro 는 (샘플 수, 3).

이 파일에는 "계산"만 있다. 파일을 읽고 쓰는 일은 run_calibration.py 가 한다.
단계 번호는 IMU_보정_전략.md 의 번호와 같다.
"""
import numpy as np

G = 9.80665  # 표준 중력 [m/s²]


# ---------------------------------------------------------------- 정지 구간 찾기
def find_still_windows(t, acc, gyro, fs=100.0, min_len_s=3.0,
                       gyro_max_dps=0.6, acc_sd_max=0.06):
    """차가 가만히 있는 구간을 찾는다.

    1초씩 잘라서 (자이로 크기의 평균이 작다) 그리고 (가속도의 흔들림이 작다)
    두 조건을 모두 만족하는 1초를 "조용한 1초"로 본다. 조용한 1초가
    min_len_s 이상 이어지면 정지 구간이다.

    돌려주는 값: [(시작 초, 끝 초, 평균 가속도 3칸), ...]
    """
    n = int(fs)
    k = len(t) // n
    a = acc[:k * n].reshape(k, n, 3)
    w = gyro[:k * n].reshape(k, n, 3)
    gyro_mag = np.degrees(np.linalg.norm(w.mean(axis=1), axis=1))   # 1초 평균 회전 [도/초]
    acc_sd = a.std(axis=1)[:, :2].max(axis=1)                       # 수평 가속도의 흔들림
    quiet = (gyro_mag < gyro_max_dps) & (acc_sd < acc_sd_max)

    out, i = [], 0
    while i < k:
        if quiet[i]:
            j = i
            while j < k and quiet[j]:
                j += 1
            if j - i >= min_len_s:
                seg = acc[i * n:j * n]
                out.append((float(t[i * n]), float(t[min(j * n, len(t) - 1)]), seg.mean(axis=0)))
            i = j
        else:
            i += 1
    return out


# ---------------------------------------------------------------- 1단계: 장착 기울기
def tilt_rotation(mean_acc_still):
    """정지 때의 평균 가속도 벡터를 정확히 z축(위)으로 보내는 회전 행렬.

    평지에 서 있으면 센서가 재는 것은 중력뿐이고, 그 방향이 진짜 "위"다.
    센서가 기울어 있으면 그 벡터가 z축에서 벗어나 있다. 벗어난 만큼만
    (가장 짧은 길로) 돌린다. 돌리는 축이 수평면 안에 있으므로
    앞뒤·좌우 방향(요)은 건드리지 않는다.

    식: 로드리게스 회전.  v = m̂ × ẑ,  R = I + [v]× + [v]×² / (1 + m̂·ẑ)
    """
    m = np.asarray(mean_acc_still, float)
    mh = m / np.linalg.norm(m)
    z = np.array([0.0, 0.0, 1.0])
    v = np.cross(mh, z)
    c = float(mh @ z)
    vx = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + vx + vx @ vx / (1.0 + c)


def tilt_angles_deg(mean_acc):
    """평균 가속도 벡터가 뜻하는 기울기 (앞뒤, 좌우) [도]. 보고용."""
    ax, ay, az = mean_acc
    return float(np.degrees(np.arctan2(ax, az))), float(np.degrees(np.arctan2(ay, az)))


# ---------------------------------------------------------------- 2단계: 방향(요)
def yaw_rotation(deg):
    """z축 둘레로 deg 도 돌리는 회전 행렬 (위에서 볼 때 반시계가 +)."""
    c, s = np.cos(np.radians(deg)), np.sin(np.radians(deg))
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1.0]])


def estimate_yaw_offset(t, acc, gyro, t0, t1, fs=100.0,
                        straight_gz_dps=1.5, min_acc=0.3, n_boot=300, seed=0):
    """센서 x축이 차의 진행 방향에서 몇 도 어긋났는지 추정한다.

    생각: 차가 직선으로 가속·제동할 때(회전이 거의 없을 때) 가속도는
    차의 앞뒤 방향으로만 변한다. 그때의 (ax, ay) 점들이 늘어선 방향이
    진짜 앞뒤 방향이다. 그 방향과 x축의 각도가 어긋난 각도다.

    방법: 1초 평균으로 떨림을 줄이고, 회전이 작고(|gz| < straight_gz_dps)
    가속도가 뚜렷한(|a| > min_acc) 초만 골라, 원점을 지나는 직선을 맞춘다
    (주성분 방향). 표본을 다시 뽑아(bootstrap) 불확실한 정도도 구한다.

    기울기 보정이 끝난 데이터에 써야 한다(정지 값이 (0, 0)이어야 원점 기준이 맞다).
    돌려주는 값: (어긋난 각도[도], 표준오차[도], 쓴 초의 수)
        각도가 +이면 진짜 앞 방향이 센서 x축에서 왼쪽으로 돌아가 있다는 뜻.
        바로잡으려면 yaw_rotation(-각도)를 적용한다.
    """
    n = int(fs)
    i0, i1 = int(t0 * fs), int(t1 * fs)
    k = (i1 - i0) // n
    a = acc[i0:i0 + k * n].reshape(k, n, 3).mean(axis=1)
    gz = np.degrees(gyro[i0:i0 + k * n, 2].reshape(k, n).mean(axis=1))
    sel = (np.abs(gz) < straight_gz_dps) & (np.hypot(a[:, 0], a[:, 1]) > min_acc)
    p = a[sel, :2]
    if len(p) < 10:
        return 0.0, float("nan"), int(len(p))

    def angle(q):
        # 원점을 지나는 주 방향: 2x2 행렬 qᵀq 의 가장 큰 고유벡터
        w, v = np.linalg.eigh(q.T @ q)
        d = v[:, -1]
        if d[0] < 0:
            d = -d                      # x 쪽을 향하게 (앞뒤 부호는 따로 확인)
        return np.degrees(np.arctan2(d[1], d[0]))

    rng = np.random.default_rng(seed)
    boots = [angle(p[rng.integers(0, len(p), len(p))]) for _ in range(n_boot)]
    return float(angle(p)), float(np.std(boots)), int(len(p))


# ---------------------------------------------------------------- 3단계: 센서 자체 오차
def apply_sensor_model(acc, bias=(0, 0, 0), scale=(1, 1, 1)):
    """가속도 치우침과 눈금을 바로잡는다:  보정값 = (측정값 - bias) × scale.

    bias, scale 은 6면 정지 측정에서 구한다(solve_six_face). 아직 측정하지
    않았으면 기본값(0, 1)이라 아무것도 바꾸지 않는다.
    """
    return (acc - np.asarray(bias, float)) * np.asarray(scale, float)


def one_point_z_scale(mean_acc_still, g=G):
    """센서를 떼지 못할 때의 임시 3단계: z축 눈금 하나만 바로잡는다.

    센서가 거의 수평으로 붙어 있으면 정지 때 중력은 거의 z축에만 걸린다.
    그래서 한 자세의 정지 값으로는 "z축이 1 g를 얼마로 읽는지"만 알 수 있다.
        크기 조건  x² + y² + (s·z)² = g²   →   s = √(g² − x² − y²) / z
    x·y축의 치우침은 1단계(기울기 회전)가 함께 지운다(정지 때는 기울기와
    구분되지 않으므로). x·y축의 눈금은 이 방법으로 알 수 없다 → 6면 측정.
    z축 치우침과 z축 눈금도 서로 구분되지 않는다. az가 늘 g 근처라서
    어느 쪽으로 보아도 결과가 거의 같으므로 눈금으로 처리한다.

    돌려주는 값: (bias, scale)  apply_sensor_model 에 그대로 넣는다.
    """
    x, y, z = np.asarray(mean_acc_still, float)
    s = float(np.sqrt(g * g - x * x - y * y) / z)
    return [0.0, 0.0, 0.0], [1.0, 1.0, s]


def solve_six_face(face_means):
    """6면 정지 측정으로 축마다 치우침과 눈금을 구한다.

    face_means: 센서를 +x, -x, +y, -y, +z, -z 가 각각 위를 보게 놓았을 때의
    평균 가속도 (6, 3).  어떤 축이 위를 볼 때 +G, 아래를 볼 때 -G 가 나와야 하므로
        bias  = (위 + 아래) / 2
        scale = 2G / (위 - 아래)
    """
    f = np.asarray(face_means, float)
    bias = np.array([(f[2 * i, i] + f[2 * i + 1, i]) / 2 for i in range(3)])
    scale = np.array([2 * G / (f[2 * i, i] - f[2 * i + 1, i]) for i in range(3)])
    return bias, scale


# ---------------------------------------------------------------- 4단계: 시간축
def true_sample_times(seq0, mono_ns0, mono_start_ns, n_samples, order=3):
    """샘플 번호 → 진짜 시각(주행 시작부터의 초).

    로그에는 1초 묶음(100개)마다 Pi 시계가 한 번 찍혀 있다. 그 시계는
    "묶음을 받은 때"라서 ±0.02초쯤 들쭉날쭉하다. 센서의 실제 속도는 천천히만
    변하므로(온도) 낮은 차수 다항식 하나로 매끈하게 맞춰 그 들쭉날쭉을 없앤다.
    """
    x = np.asarray(seq0, float)
    y = (np.asarray(mono_ns0, float) - float(mono_start_ns)) / 1e9
    xm = x.mean()
    coef = np.polyfit(x - xm, y, order)
    resid = y - np.polyval(coef, x - xm)
    tt = np.polyval(coef, np.arange(n_samples) - xm)
    return tt - tt[0], float(resid.std())


def resample_uniform(t_true, data, fs=100.0):
    """진짜 시각 t_true 에 놓인 값을 정확히 1/fs 초 간격으로 다시 뽑는다 (선형 보간).

    data: (샘플 수, 칸 수).  돌려주는 값: (새 시각, 새 값)
    """
    t_new = np.arange(0.0, t_true[-1], 1.0 / fs)
    out = np.column_stack([np.interp(t_new, t_true, data[:, j]) for j in range(data.shape[1])])
    return t_new, out


# ---------------------------------------------------------------- 6단계: 이상값 표시
def flag_samples(acc, gyro, hold, accel_range_g=4.0, gyro_range_dps=250.0):
    """지우지 않고 표시만 한다.  비트 1 = 빠진 샘플(직전 값으로 채움), 비트 2 = 포화."""
    sat = (np.abs(acc) > 0.98 * accel_range_g * G).any(axis=1) | \
          (np.abs(np.degrees(gyro)) > 0.98 * gyro_range_dps).any(axis=1)
    return (hold > 0).astype(np.uint8) | (sat.astype(np.uint8) << 1)


# ---------------------------------------------------------------- 검증에 쓰는 것
def window_mean(t, x, t0, t1):
    m = (t >= t0) & (t < t1)
    return x[m].mean(axis=0)
