"""comma2k19 리더 (MIT, github.com/commaai/comma2k19).

원본 청크(Chunk_N.zip)를 푼 폴더 구조를 읽는다:
    <root>/Chunk_1/<dongle>_<route>/<segment>/processed_log/IMU/{accelerometer,gyro}/{t,value}
                                             /processed_log/CAN/speed/{t,value}
(zip 안 경로의 '|'는 '_'로 바꿔 저장. 받는 방법은 docs/ml/REALDATA.md)
세그먼트는 1분이고 같은 route의 연속 번호는 시계가 이어진다. 연속 구간마다 주행 하나로 만든다.
IMU 104 Hz (값은 양자화), CAN 속도 82 Hz. 축은 common.align이 정한다 (원래는 대략 앞-오른쪽-아래).
페달·기어·크루즈는 raw_can에만 있어(차종별 DBC 필요) 여기서는 NaN.
"""
import pathlib

import numpy as np

from convert import runs

MIN_SEGMENTS = 5  # 5분 미만 구간은 버린다 (5분 창 피처와 5분 라벨 지평)


def _load(seg, rel):
    d = seg / rel
    return np.load(d / "t", allow_pickle=False), np.load(d / "value", allow_pickle=False)


def _cat(parts):
    return np.concatenate([p[0] for p in parts]), np.concatenate([p[1] for p in parts])


def routes(root):
    """→ [(route 폴더, [세그먼트 번호...]), ...] 연속 구간마다 하나"""
    out = []
    for r in sorted(pathlib.Path(root).glob("Chunk_*/*")):
        segs = [int(p.name) for p in r.iterdir() if p.name.isdigit() and (p / "processed_log").exists()]
        out += [(r, run) for run in runs(segs, MIN_SEGMENTS)]
    return out


def read(route_dir, segs):
    seg = [route_dir / str(s) for s in segs]
    return {"name": f"{route_dir.name}_{segs[0]}-{segs[-1]}",
            "acc": _cat([_load(s, "processed_log/IMU/accelerometer") for s in seg]),
            "gyr": _cat([_load(s, "processed_log/IMU/gyro") for s in seg]),
            "speed": _cat([_load(s, "processed_log/CAN/speed") for s in seg]),
            "extra": {}}
