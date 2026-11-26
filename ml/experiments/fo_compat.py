"""실험 스크립트가 피처 목록 두 가지(feature_order v3 77칸 / v5 52칸)에서 모두 돌게 하는 도우미.

스크립트는 원래 v3(77칸, 켜진 74개)에서 썼다. 피처 목록은 저장소의 protocol/feature_order.json을 그대로 읽으므로
  - v5 이전 커밋(예: 77af82a^)에서 돌리면 원래와 같은 77칸 결과,
  - 지금 main(v5)에서 돌리면 52칸(켜진 49개)으로 돈다.
v5에 없는 그룹(G3a, G3b, G4, G7)은 건너뛰고, 4TU 행은 칸 번호가 아니라 이름으로 채운다.
출력 기본 폴더는 v3이면 원래 경로(ml/out/<이름>), v5부터는 ml/out/fo<버전>/<이름>이라 두 목록의 캐시가 섞이지 않는다.
"""
import json
import pathlib

import numpy as np

ML = pathlib.Path(__file__).resolve().parents[1]
FO = json.loads((ML.parent / "protocol" / "feature_order.json").read_text())
VERSION = int(FO["version"])
FEATS = FO["features"]
NF = len(FEATS)
NAMES = [f["name"] for f in FEATS]
IDX = {n: i for i, n in enumerate(NAMES)}


def present(groups):
    """지금 피처 목록에 있는 그룹만 (순서 유지)."""
    return [g for g in groups if g in FO["groups"]]


def out_dir(*parts):
    """출력·캐시 기본 경로. v3 이하는 원래 경로 그대로, v5부터 ml/out/fo<버전>/ 아래."""
    base = ML / "out" if VERSION < 5 else ML / "out" / f"fo{VERSION}"
    return base.joinpath(*parts)


def full_row(Xv, mssq, sens, vehicle_type=0.0):
    """4TU 재생 행: 차량 피처(featurize 출력) + MSSQ, 민감도, 차량 유형. EV 경험은 모름(NaN), 허용도(G8)는 NaN.
    v3에서는 원래 코드(X[:, :71] = Xv; X[:, 73], X[:, 74], X[:, 76] = mssq, sens, 0.0)와 켜진 칸이 같다. v5에서는 trip.full_vector와 같다."""
    X = np.full((len(Xv), NF), np.nan)
    X[:, :Xv.shape[1]] = Xv
    X[:, IDX["mssq"]], X[:, IDX["sensitivity"]], X[:, IDX["vehicle_type"]] = mssq, sens, vehicle_type
    for i, f in enumerate(FEATS):
        if not f["enabled"]:  # P3와 같이 꺼진 칸은 NaN (스크립트는 켜진 칸만 쓰므로 결과는 원래와 같다)
            X[:, i] = np.nan
    return X
