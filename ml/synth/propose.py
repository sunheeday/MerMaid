"""Claude API로 시뮬레이터 설정 변경 후보 받기.

Claude는 샘플을 만들지 않는다. 실차와 시뮬레이터의 분포 차이를 보고 sim_config.json의 값을 어떻게 바꿀지
후보 몇 개를 JSON으로 낸다. 검사(patch.py)와 점수(loop.py)는 이쪽 코드가 한다.

- 모델 claude-opus-5-5, thinking은 이 모델에서 항상 켜짐(adaptive). effort 기본 high.
- 구조화 출력: client.messages.parse(output_format=Proposal) → 스키마에 맞는 Pydantic 객체.
- 고정 부분(역할, 규칙, 피처 설명, bounds.json)은 system에 두고 cache_control을 단다.
  라운드마다 바뀌는 지금 값, 통계, 이전 결과는 user 메시지에 둔다.
- 키: 환경 변수 ANTHROPIC_API_KEY (또는 `ant auth login` 프로필). 저장소에 두지 않는다.
"""
from __future__ import annotations

import hashlib
import json

from pydantic import BaseModel

import summarize

MODEL = "claude-opus-5-5"
MAX_TOKENS = 16000


class Change(BaseModel):
    path: str
    value: float | list[float]


class Candidate(BaseModel):
    hypothesis: str
    changes: list[Change]


class Proposal(BaseModel):
    analysis: str
    candidates: list[Candidate]


class ProposalError(RuntimeError):
    pass


FEATURES = """\
All features are computed by the in-car feature pipeline on a sliding window, one row every 10 s.
x = longitudinal, y = lateral, z = vertical; accelerations in m/s^2, speeds in m/s.
- rms_x_1m, rms_y_1m, rms_z_1m: RMS acceleration over the last minute (z: road-induced vertical motion and vibration)
- jerk_rms_x_1m, jerk_rms_y_1m: RMS jerk over the last minute
- decel_rms_x_1m: RMS of braking (negative longitudinal) acceleration over the last minute
- hard_brake_count_5m, hard_accel_count_5m, hard_turn_count_5m: event counts over the last 5 minutes
- yaw_rms_1m: RMS yaw rate over the last minute
- msdv: motion sickness dose value, cumulative since the ride started
- oman_score: Oman motion sickness model state, cumulative since the ride started
- speed_mean_1m, speed_std_1m: speed mean and spread over the last minute
- stop_ratio_5m: share of the last 5 minutes spent stopped"""

SYSTEM = f"""\
You tune the parameters of a vehicle motion simulator so that its synthetic rides match real driving.
The simulator builds routes from road segments, drives them with a randomized driver model, and adds
IMU noise, vibration, road profile and lane-keeping sway. Its rides train a motion sickness model, so
the motion statistics must look like real rides.

Each round you receive:
- the editable parameters with their kind, allowed range and current value,
- distribution summaries comparing real driving with a small simulator run under the current values
  (p10/p50/p90 for real and sim, and the two-sample KS distance per feature),
- results of earlier candidates, if any.

Reference data:
- baton: human driving logs (2 drivers, about 19 h, driver assistance off). This is the score to improve:
  the mean KS over features (lower is better).
- comma: comma2k19, short highway-heavy commute clips; the simulator rows are cut to the same elapsed ride
  time. It is a check only: a candidate is rejected if it makes comma clearly worse. Do not chase its
  speed mix, which comes from how that dataset was collected.

Features:
{FEATURES}

Rules:
- Only use paths from the editable list. Everything else is locked.
- kind "scalar" takes one number, kind "range" takes [lo, hi] with lo <= hi; both must stay inside [min, max].
  The simulator draws a value uniformly from each range per segment, ride or driver.
- segments.weights.* are relative weights; the simulator normalizes them.
- Each candidate is one hypothesis that explains a specific gap you see in the statistics, and changes at
  most {{max_changes}} paths. Make the candidates different from each other so one round tests several ideas.
- Move values in proportion to the gap; small runs are noisy, so KS changes below about 0.01 mean nothing.
- Learn from earlier rounds: do not repeat a change that already failed, and build on ones that helped.
- In "analysis", say briefly which gaps matter most and why. In each "hypothesis", say what you expect to change.

Allowed ranges (bounds.json):
{{bounds}}
"""


def system_text(bounds):
    return SYSTEM.replace("{max_changes}", str(bounds["max_changes_per_candidate"])).replace(
        "{bounds}", json.dumps(bounds["paths"], indent=0))


def build_request(stats, editable, history, n, bounds, effort="high"):
    """messages.parse()에 넘길 인자 (output_format 제외). stats만 요약 검사를 거친다"""
    summarize.assert_aggregate(stats, "stats")
    user = {
        "task": f"Propose {n} candidates.",
        "editable": [{"path": e["path"], "kind": e["kind"], "current": e["current"]} for e in editable],
        "stats": stats,
        "history": history,
    }
    return {
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "output_config": {"effort": effort},
        "system": [{"type": "text", "text": system_text(bounds), "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": json.dumps(user, indent=1)}],
    }


def request_hash(req):
    return hashlib.sha256(json.dumps(req, sort_keys=True).encode()).hexdigest()[:16]


def propose(client, req):
    """→ (Proposal, meta). 거절이나 잘림은 ProposalError"""
    resp = client.messages.parse(**req, output_format=Proposal)
    meta = {"model": resp.model, "stop_reason": resp.stop_reason, "request_hash": request_hash(req),
            "usage": resp.usage.model_dump() if hasattr(resp.usage, "model_dump") else dict(resp.usage)}
    if resp.stop_reason == "refusal":
        raise ProposalError(f"Claude가 거절함: {getattr(resp, 'stop_details', None)}")
    if resp.stop_reason == "max_tokens":
        raise ProposalError("출력이 max_tokens에서 잘림")
    if resp.parsed_output is None:
        raise ProposalError(f"구조화 출력 없음 (stop_reason={resp.stop_reason})")
    return resp.parsed_output, meta


def make_client():
    import anthropic
    return anthropic.Anthropic()
