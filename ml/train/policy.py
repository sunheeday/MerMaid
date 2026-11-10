"""P4 정책의 Python 재생 (AC-05): 수동 조작 없이, 게이트는 항상 열린 상태로 본다.

설정은 controller/config/policy.json. 한 탑승의 10초 행 순서대로:
  ON  : r >= th 가 hold 주기 연속 (쿨다운은 수동 끄기에만 생기므로 재생에서는 없음)
  OFF : r < th - off_margin 이 off_hold 주기 연속
지표 (Tier마다, 입력 k = 3, 4, 4):
  탐지율    = MISC가 k에 처음 도달하기 전(같은 시점 포함)에 켜진 탑승 / k에 도달한 탑승
  늦은 ON   = 도달 뒤 2분 안에야 켜진 탑승 비율 (참고)
  리드타임  = 도달 시각 - 그 전 첫 ON 시각 (탐지된 탑승의 중앙값)
  오경보    = 켜진 시점부터 5분 안에 MISC가 k에 도달하지 않은 ON, 시간당 횟수
"""
import json
import pathlib

import numpy as np

REPO = pathlib.Path(__file__).resolve().parents[2]
POLICY = json.loads((REPO / "controller" / "config" / "policy.json").read_text())


def replay_ride(r, th, hold, off_margin, off_hold):
    """r: 10초마다의 확률 → 각 행의 ON 여부, ON이 시작된 행 번호들"""
    on, cnt, low = False, 0, 0
    state = np.zeros(len(r), bool)
    starts = []
    for j, x in enumerate(r):
        cnt = cnt + 1 if x >= th else 0
        low = low + 1 if x < th - off_margin else 0
        if not on and cnt >= hold:
            on = True
            starts.append(j)
        elif on and low >= off_hold:
            on = False
        state[j] = on
    return state, starts


def evaluate(P, misc, future_max, t, ride_id, thresholds, k_list):
    """P: monotone_fix를 거친 확률 (열 = k_list). thresholds: {"1": th, "2": th, "3": th}"""
    out = {}
    hours = len(t) * 10 / 3600
    for tier in ("1", "2", "3"):
        cfg = POLICY["tiers"][tier]
        k = cfg["input_k"]
        col = k_list.index(k)
        th = thresholds[tier]
        events = detected = late = alarms = false_alarms = 0
        leads = []
        on_rows = 0
        for rid in np.unique(ride_id):
            m = ride_id == rid
            state, starts = replay_ride(P[m, col], th, cfg["hold_cycles"], POLICY["off_margin"], POLICY["off_hold_cycles"])
            on_rows += state.sum()
            mi, fm, tt = misc[m], future_max[m], t[m]
            hit = np.where(mi >= k)[0]
            if len(hit):
                events += 1
                before = [s for s in starts if s <= hit[0]]
                if before:
                    detected += 1
                    leads.append(tt[hit[0]] - tt[before[0]])
                elif any(s <= hit[0] + 12 for s in starts):  # 도달 후 2분 안에 켜짐
                    late += 1
            for s in starts:
                alarms += 1
                false_alarms += max(mi[s], fm[s]) < k
        out[tier] = {
            "input_k": k, "threshold": th, "rides_reaching_k": events,
            "detection_rate": detected / events if events else None,
            "on_within_2min_after_rate": late / events if events else None,
            "median_lead_s": float(np.median(leads)) if leads else None,
            "activations": alarms, "false_activations_per_hour": false_alarms / hours,
            "false_activation_share": false_alarms / alarms if alarms else None,
            "on_time_share": float(on_rows / len(t)),
        }
    return out
