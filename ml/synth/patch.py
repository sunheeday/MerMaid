"""Claude가 낸 설정 변경을 검사하고 적용한다 (ml/synth/bounds.json).

변경 하나라도 틀리면 그 후보 전체를 버린다 (후보는 한 가지 가설이라 일부만 적용하면 의미가 바뀜).
설정은 {"sim_config.json": dict, "sim_effects.json": dict}로 다룬다. 원본은 바꾸지 않고 사본을 돌려준다.
"""
from __future__ import annotations

import copy
import json
import math
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
SIM_DIR = HERE.parent / "simulator"
FILES = ("sim_config.json", "sim_effects.json")


def load_bounds(path=HERE / "bounds.json"):
    return json.loads(pathlib.Path(path).read_text())


def load_configs(d=None):
    d = pathlib.Path(d or SIM_DIR)
    return {f: json.loads((d / f).read_text()) for f in FILES}


def write_configs(cfgs, d):
    d = pathlib.Path(d)
    d.mkdir(parents=True, exist_ok=True)
    for f in FILES:
        (d / f).write_text(json.dumps(cfgs[f], indent=1) + "\n")


def locate(path):
    """'effects.fan.range' → ('sim_effects.json', ['fan', 'range']), 그 밖은 sim_config.json"""
    if path.startswith("effects."):
        return "sim_effects.json", path[len("effects."):].split(".")
    return "sim_config.json", path.split(".")


def get(cfgs, path):
    f, keys = locate(path)
    node = cfgs[f]
    for k in keys:
        if not isinstance(node, dict) or k not in node:
            raise KeyError(path)
        node = node[k]
    return node


def _set(cfgs, path, value):
    f, keys = locate(path)
    node = cfgs[f]
    for k in keys[:-1]:
        node = node[k]
    node[keys[-1]] = value


def editable(cfgs, bounds):
    """Claude에게 보여 줄 표: 바꿀 수 있는 경로, 종류, 허용 범위, 지금 값"""
    return [{"path": p, **spec, "current": get(cfgs, p)} for p, spec in bounds["paths"].items()]


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def check_value(spec, v):
    """틀리면 이유 문자열, 맞으면 None"""
    lo, hi = spec["min"], spec["max"]
    if spec["kind"] == "scalar":
        if not _num(v):
            return f"숫자 하나여야 함: {v!r}"
        if not lo <= v <= hi:
            return f"{v} 가 허용 범위 [{lo}, {hi}] 밖"
    elif spec["kind"] == "range":
        if not (isinstance(v, list) and len(v) == 2 and all(_num(x) for x in v)):
            return f"[lo, hi] 두 숫자여야 함: {v!r}"
        if v[0] > v[1]:
            return f"lo > hi: {v}"
        if v[0] < lo or v[1] > hi:
            return f"{v} 가 허용 범위 [{lo}, {hi}] 밖"
    else:
        return f"알 수 없는 kind {spec['kind']}"
    return None


def apply(cfgs, changes, bounds):
    """changes: [{"path", "value"}]. → (새 설정 또는 None, 오류 목록, 실제로 바뀐 것 목록)"""
    errors = []
    if not changes:
        errors.append("변경이 없음")
    if len(changes) > bounds["max_changes_per_candidate"]:
        errors.append(f"변경 {len(changes)}개 > 한도 {bounds['max_changes_per_candidate']}")
    seen = set()
    out = copy.deepcopy(cfgs)
    effective = []
    for ch in changes:
        p, v = ch["path"], ch["value"]
        if p in seen:
            errors.append(f"{p}: 같은 경로를 두 번 바꿈")
            continue
        seen.add(p)
        spec = bounds["paths"].get(p)
        if spec is None:
            errors.append(f"{p}: 바꿀 수 없는 경로 (bounds.json에 없음)")
            continue
        if isinstance(v, (int, float)) and spec["kind"] == "range":
            errors.append(f"{p}: [lo, hi] 두 숫자여야 함: {v!r}")
            continue
        if isinstance(v, list) and spec["kind"] == "scalar":
            errors.append(f"{p}: 숫자 하나여야 함: {v!r}")
            continue
        v = [float(x) for x in v] if isinstance(v, list) else float(v)
        err = check_value(spec, v)
        if err:
            errors.append(f"{p}: {err}")
            continue
        try:
            old = get(out, p)
        except KeyError:
            errors.append(f"{p}: 설정 파일에 없는 경로")
            continue
        if old != v:
            _set(out, p, v)
            effective.append({"path": p, "old": old, "new": v})
    if not errors and not effective:
        errors.append("지금 값과 같아 바뀐 것이 없음")
    return (None if errors else out), errors, effective


def diff(a, b, bounds):
    """두 설정 사이에 bounds 경로 가운데 달라진 것"""
    return [{"path": p, "old": get(a, p), "new": get(b, p)} for p in bounds["paths"] if get(a, p) != get(b, p)]
