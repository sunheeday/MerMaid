"""시뮬레이터 결과를 엑셀에서 열 수 있는 CSV로 내보내기

    python3 ml/verify/export_csv.py

입력은 배포 중인 모델(ml/runs.py CURRENT)의 학습 데이터셋 버전. 출력은 그 버전 폴더의 csv/ (git 제외):
  rides.csv              탑승 1,920회의 설정 (승객, 좌석, 회생제동, 설문/실제 민감도, 최고 MISC)
  dataset_10s.csv        학습 데이터 전체: 10초마다 한 행, 정답(MISC, Oman 점수, 앞으로 5분 최고)과 피처 전부 (feature_order.json)
  features.csv           피처 설명과 비어 있는(NaN) 비율, 비어 있는 이유
  trips_100hz/ride_XXXX.csv  탑승 8회의 100 Hz 센서 원본 (그 버전의 trips/*.bin)
빈 칸은 값이 없음(NaN)이라는 뜻입니다.
"""
import json
import pathlib
import sys

import numpy as np
import pandas as pd

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "ml"))
import runs  # noqa: E402

DATA = runs.dataset_for(runs.current_v1_models()).parent
OUT = DATA / "csv"
FO = json.loads((REPO / "protocol" / "feature_order.json").read_text())
TRIP = json.loads((REPO / "protocol" / "trip_format.json").read_text())

# 데이터에서 비어 있거나 늘 같은 피처의 이유 (FEATURES.md, sim.py 기준)
WHY = {
    "svc_conflict": "v1에서 끈 피처 (enabled=false). 감각 충돌(SVC) 정의가 PRD에 없어 항상 비움",
    "tol_display": "v1에서 끈 피처 (enabled=false). 기기 허용도는 실제 사용 기록이 쌓여야 의미가 있어 학습에는 비움",
    "tol_phone": "v1에서 끈 피처 (enabled=false). tol_display와 같은 이유",
    "time_since_rest": "휴식(180초 연속 정차) 뒤 지난 시간. 시뮬레이터 정차는 5~45초라 휴식이 한 번도 없어 항상 비어 있음",
    "reexposure": "휴식 뒤 다시 출발했는지. 휴식이 없어서 항상 0",
    "event_irregularity_5m": "5분 안 급가속·급제동·급회전 간격의 변동. 이벤트가 2개 미만이면 비어 있음",
}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    d = dict(np.load(DATA / "dataset.npz", allow_pickle=False))
    names = [f["name"] for f in FO["features"]]

    rides = pd.DataFrame(json.loads((DATA / "rides.json").read_text()))
    rides = rides.rename(columns={"vehicle_type": "vehicle_type_ev1", "seat_row": "seat_row_rear1", "m": "env_multiplier",
                                  "sens": "sens_from_mssq", "true_sens": "sens_true_hidden"})
    rides.to_csv(OUT / "rides.csv", index=False, float_format="%.4f")

    df = pd.DataFrame({"ride_id": d["ride_id"], "trip_id": d["trip_id"], "person_id": d["person_id"],
                       "t_s": d["t"], "misc": d["misc"] + 0.0, "oman_score": d["score"], "future_5min_max_misc": d["future_max"]})
    for j, k in enumerate(d["k"]):
        df[f"y_ge{k}"] = d["y"][:, j].astype(int)
    df = pd.concat([df, pd.DataFrame(d["X"], columns=names)], axis=1)
    df.to_csv(OUT / "dataset_10s.csv", index=False, float_format="%.6g")

    X = d["X"]
    rows = []
    for i, f in enumerate(FO["features"]):
        col = X[:, i]
        nan = float(np.isnan(col).mean())
        const = nan < 1 and np.nanstd(col) == 0
        why = WHY.get(f["name"], "")
        rows.append({"index": i, "name": f["name"], "group": f["group"], "group_desc": FO["groups"][f["group"]]["desc"],
                     "unit": f.get("unit", ""), "window": f.get("window", ""), "enabled": f["enabled"],
                     "empty_share": round(nan, 4), "always_same_value": bool(const), "why_empty": why})
    pd.DataFrame(rows).to_csv(OUT / "features.csv", index=False)

    cols = [c["name"] for c in TRIP["columns"]]
    (OUT / "trips_100hz").mkdir(exist_ok=True)
    for p in sorted((DATA / "trips").glob("ride_*.bin")):
        a = np.fromfile(p, dtype="<f8")
        assert int(a[1]) == len(cols)
        pd.DataFrame(a[3:].reshape(-1, len(cols)), columns=cols).to_csv(
            OUT / "trips_100hz" / (p.stem + ".csv"), index=False, float_format="%.6g")

    for p in sorted(OUT.rglob("*.csv")):
        print(f"{p.stat().st_size / 1e6:8.1f} MB  {p.relative_to(REPO)}")


if __name__ == "__main__":
    main()
