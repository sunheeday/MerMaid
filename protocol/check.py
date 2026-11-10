"""protocol/ 검증: python3 protocol/check.py (S0 완료 확인)

1. PROTOCOL.md의 예시 메시지가 messages.schema.json을 통과하고, 잘못된 메시지는 거부되는지
2. feature_order.json이 77개, 인덱스 연속, 그룹 개수와 범위가 맞는지
3. UUID, 설정 파일의 버전과 값이 서로 맞는지
"""
import json
import pathlib
import re
import sys

from jsonschema import Draft202012Validator

ROOT = pathlib.Path(__file__).resolve().parent
CONFIG = ROOT.parent / "controller" / "config"
load = lambda p: json.loads(p.read_text())
errors = []


def check(cond, msg):
    if not cond:
        errors.append(msg)


# 1. 메시지 스키마
schema = load(ROOT / "messages.schema.json")
Draft202012Validator.check_schema(schema)
v = Draft202012Validator(schema)
examples = [json.loads(l) for l in re.findall(r'^\{"t":.*\}$', (ROOT / "PROTOCOL.md").read_text(), re.M)]
check(len(examples) == 7, f"PROTOCOL.md 예시 {len(examples)}개 (7개여야 함)")
for m in examples:
    check(v.is_valid(m), f"예시가 스키마를 통과하지 못함: {m}")
check({m["t"] for m in examples} == {"HELLO", "INT", "MOT", "SCR", "OVR", "PROF", "HB"}, "예시에 빠진 메시지 종류가 있음")
for bad in [
    {"t": "HELLO", "role": "app", "dev": "x", "v": 2},
    {"t": "INT", "tier": 4, "on": True},
    {"t": "MOT", "ax": 0.1, "ay": 0.2, "pax": 0.3},
    {"t": "SCR", "on": True},
    {"t": "PROF", "mssq": 60, "sens": 1.0},
    {"t": "HB", "seq": -1},
    {"t": "MISC", "score": 3},
    {"t": "OVR", "dev": "display-01", "on": False, "extra": 1},
]:
    check(not v.is_valid(bad), f"잘못된 메시지가 통과함: {bad}")

# 2. 피처 순서
fo = load(ROOT / "feature_order.json")
feats = fo["features"]
check(fo["n_features"] == len(feats) == 77, f"피처 {len(feats)}개 (77개여야 함)")
check([f["index"] for f in feats] == list(range(len(feats))), "인덱스가 0부터 연속이 아님")
check(len({f["name"] for f in feats}) == len(feats), "피처 이름 중복")
for g, info in fo["groups"].items():
    idx = [f["index"] for f in feats if f["group"] == g]
    check(len(idx) == info["count"], f"{g}: {len(idx)}개 (표에는 {info['count']}개)")
    check(idx == list(range(idx[0], idx[0] + len(idx))) if idx else False, f"{g}: 인덱스가 연속이 아님")
check(sum(i["count"] for i in fo["groups"].values()) == 77, "그룹 개수 합이 77이 아님")
for g in ("G1", "G9"):
    check(fo["groups"][g]["dropout"] == 0, f"{g}는 드롭아웃 0이어야 함")
lo, hi = fo["vehicle_range"]
check(all(f["group"] not in ("G8", "G10") for f in feats[lo:hi + 1]), "차량 구간에 승객 피처가 있음")
check(all(f["group"] in ("G8", "G10") for f in feats[hi + 1:]), "승객 구간에 차량 피처가 있음")
check(all(f["monotone"] in (-1, 0, 1) for f in feats), "monotone은 -1, 0, 1만 가능")

# 3. UUID와 설정 파일
u = load(ROOT / "uuids.json")
base = lambda s: s[8:]
check(base(u["service"]) == base(u["rx"]) == base(u["tx"]), "UUID 베이스가 다름")
check(len({u["service"][:8], u["rx"][:8], u["tx"][:8]}) == 3, "UUID 16-bit 필드가 겹침")
th = load(CONFIG / "thresholds.json")
check(th["feature_order_version"] == fo["version"], "thresholds.json의 feature_order_version이 다름")
check(all(0 < th["base_th"][t] < 1 for t in ("1", "2", "3")), "문턱은 0과 1 사이")
pol = load(CONFIG / "policy.json")
check([pol["tiers"][t]["input_k"] for t in ("1", "2", "3")] == [3, 4, 4], "Tier 입력 k는 3, 4, 4")
c = load(ROOT / "constants.json")
check(c["device_ids"]["airflow"] == pol["tiers"]["1"]["device"], "airflow 기기 ID 불일치")
check(c["device_ids"]["display"] == pol["tiers"]["2"]["device"], "display 기기 ID 불일치")

if errors:
    print("\n".join("FAIL: " + e for e in errors))
    sys.exit(1)
on = sum(f["enabled"] for f in feats)
print(f"OK: 메시지 예시 {len(examples)}개 통과, 잘못된 메시지 8개 거부, 피처 {len(feats)}개 (enabled {on}개, NaN 고정 {len(feats) - on}개)")
