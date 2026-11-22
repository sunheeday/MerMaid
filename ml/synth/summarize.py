"""Claude에게 보낼 요약 통계 만들기.

지금(1단계)은 ml/realdata/calibrate.py 결과(공개 데이터 BATON, comma2k19 vs 시뮬레이터)를 줄인다.
MerMaid 실주행이 쌓이면 서버 DB에서 같은 모양의 요약을 만든다 (docs/controller/DATA_LOGGING.md §11).

보내는 것은 분포 요약(분위수, KS, 비율)뿐이다. 원시 샘플, 주행별 행, 개인 MSSQ·MISC 값, 기기·폰 ID는 보내지 않는다.
assert_aggregate()가 보내기 직전에 모양을 한 번 더 검사한다.
"""
from __future__ import annotations

FORBIDDEN = ("ride_id", "device", "phone", "person", "pid", "mssq", "misc_raw", "samples", "x_raw")
MAX_LIST = 16  # 이보다 긴 목록은 요약이 아니라 행으로 본다


def _r(x, n=4):
    return float(f"{x:.{n}g}")


def from_calibration(cal):
    """calibrate.evaluate() 결과 → {"baton": {...}, "comma": {...}}"""
    out = {}
    for tn, r in cal.items():
        out[tn] = {
            "ks_mean": _r(r["ks_mean"]),
            "features": {f: {"real_p10_p50_p90": [_r(x) for x in v["real_q"]],
                             "sim_p10_p50_p90": [_r(x) for x in v["sim_q"]],
                             "ks": _r(v["ks"], 3)}
                         for f, v in r["features"].items()},
            "speed_share_lt30_30to80_ge80": {"real": [_r(x, 3) for x in r["speed_share"]["real"]],
                                             "sim": [_r(x, 3) for x in r["speed_share"]["sim"]]},
            "label_rate_P_ge_2_to_6": {"real": r["label"]["real"], "sim": r["label"]["sim"]},
        }
    return out


def assert_aggregate(obj, path="payload"):
    """보낼 내용이 요약 통계 모양인지 검사. 아니면 ValueError"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            kl = str(k).lower()
            if any(f in kl for f in FORBIDDEN):
                raise ValueError(f"{path}.{k}: 보내면 안 되는 키")
            assert_aggregate(v, f"{path}.{k}")
    elif isinstance(obj, list):
        if len(obj) > MAX_LIST:
            raise ValueError(f"{path}: 목록 길이 {len(obj)} > {MAX_LIST} (요약이 아닌 행으로 보임)")
        for i, v in enumerate(obj):
            assert_aggregate(v, f"{path}[{i}]")
    elif not (obj is None or isinstance(obj, (str, int, float, bool))):
        raise ValueError(f"{path}: 지원하지 않는 값 {type(obj).__name__}")
