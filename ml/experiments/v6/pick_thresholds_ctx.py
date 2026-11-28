"""V3 문맥 모델(ctx_gru)의 정책 임계값 thresholds.json (kind response3) 만들기.

    python3 ml/experiments/v6/pick_thresholds_ctx.py --preds <context6 출력>/fleet_preds.npz \
        --out <models run>/thresholds.json [--run vNNN]

입력은 context6.py가 남긴 플릿 채점 행(trip_order >= adapt_trips)의 문맥 확률 p와 라벨 Y, 자격 W (머리 fan, ambient, dots).
tier ↔ 머리는 controller/config/policy.json의 input_head (1 fan, 2 ambient, 3 dots).

규칙 (V1 thresholds.json 규칙 2·3과 같은 모양, 행동이 더 거슬리는 tier일수록 엄격하게):
  각 주행에서 시간 순으로 '자격 있고 p >= th'가 새로 시작되는 행 = 자동 켜기 한 번.
  그 행의 Y가 0(앞으로 5분 안에 켜지 않음)이면 잘못 켠 것. 시간 = 자격 있는 행 수 × 행 간격.
  tier마다 잘못 켜기/시간이 한도 이하인 가장 작은 th (격자 0.01): tier 1 ≤ 1.0, tier 2 ≤ 0.5, tier 3 ≤ 0.25.
  같이 적는 것: 그 th에서 양성 행 재현율, 켜기 중 맞은 비율. 정책의 hold_cycles·쿨다운은 반영하지 않은 행 단위 근사다.
"""
import argparse
import json
import pathlib

import numpy as np

HEADS = ["fan", "ambient", "dots"]
LIMIT = {"1": 1.0, "2": 0.5, "3": 0.25}  # 잘못 켜기 / 시간
TIER_HEAD = {"1": 0, "2": 1, "3": 2}
GRID = np.round(np.arange(0.01, 1.0, 0.01), 2)


def stats(p, y, w, ride, t, th):
    order = np.lexsort((t, ride))
    p, y, w, ride, t = p[order], y[order], w[order] > 0, ride[order], t[order]
    above = w & (p >= th)
    new_ride = np.r_[True, ride[1:] != ride[:-1]]
    prev = np.r_[False, above[:-1]] & ~new_ride
    on = above & ~prev
    dt = np.median(np.diff(t)[~new_ride[1:]]) if len(t) > 1 else 10.0
    hours = w.sum() * dt / 3600.0
    false_on = int((on & (y == 0)).sum())
    pos = w & (y == 1)
    return {
        "false_per_hour": false_on / hours if hours > 0 else float("nan"),
        "activations": int(on.sum()),
        "precision": float((on & (y == 1)).sum() / on.sum()) if on.any() else float("nan"),
        "recall_rows": float((above & pos).sum() / pos.sum()) if pos.any() else float("nan"),
        "hours": hours,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preds", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--run", default="")
    a = ap.parse_args()
    d = np.load(a.preds)
    p, Y, W, ride, t = d["p"], d["Y"], d["W"], d["ride_id"], d["t"]
    base, report = {}, {}
    for tier, j in TIER_HEAD.items():
        if not (W[:, j] > 0).any():
            print(f"WARNING tier {tier} ({HEADS[j]}): no eligible scoring rows in {a.preds}, threshold falls back to 0.99")
        pick = None
        for th in GRID:
            s = stats(p[:, j], Y[:, j], W[:, j], ride, t, th)
            if s["false_per_hour"] <= LIMIT[tier]:
                pick = (float(th), s)
                break
        if pick is None:
            pick = (0.99, stats(p[:, j], Y[:, j], W[:, j], ride, t, 0.99))
        base[tier] = pick[0]
        report[tier] = {"head": HEADS[j], **{k: round(v, 4) if isinstance(v, float) else v for k, v in pick[1].items()}}
        print(f"tier {tier} ({HEADS[j]}): th {pick[0]:.2f}  " + "  ".join(f"{k} {v}" for k, v in report[tier].items() if k != "head"))
    out = {
        "placeholder": False,
        "kind": "response3",
        "feature_order_version": 5,
        "source": f"ml/experiments/v6/context6.py ctx_gru fleet scoring rows ({a.preds})",
        "rule": {t_: f"smallest threshold with false auto-ons per hour <= {LIMIT[t_]} (head {HEADS[j]}, row replay, grid 0.01)"
                 for t_, j in TIER_HEAD.items()},
        "base_th": base,
        "fleet": report,
        "run": a.run,
    }
    pathlib.Path(a.out).write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
