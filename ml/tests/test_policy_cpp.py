"""C++ P4 정책(controller/common/policy.cpp)이 Python 재생(ml/train/policy.py)과 같은 ON/OFF를 내는지

학습 때 문턱을 고른 재생과 Pi에서 도는 정책이 같아야 AC-05 수치가 실제 동작을 대표한다.
조건은 재생과 같다: 게이트 항상 열림, 수동 조작 없음, 허용도 0. policy_replay가 없으면 cmake로 빌드한다.
"""
import pathlib
import subprocess
import sys

import numpy as np
import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "ml" / "train"))
import policy  # noqa: E402

TOOL = REPO / "build" / "policy_replay"


@pytest.fixture(scope="module")
def tool():
    b = REPO / "build"
    subprocess.run(["cmake", "-S", str(REPO / "controller"), "-B", str(b)], check=True, capture_output=True)
    subprocess.run(["cmake", "--build", str(b), "--target", "policy_replay"], check=True, capture_output=True)
    return TOOL


def rides(rng, n_rides=40):
    """문턱 근처를 오가는 확률 시퀀스 (10초 주기, 탑승마다 60~250행)"""
    out = []
    for _ in range(n_rides):
        n = int(rng.integers(60, 250))
        x = np.cumsum(rng.normal(0, 0.06, n)) + rng.uniform(0.2, 0.8)
        out.append(np.clip(x, 0, 1))
    return out


@pytest.mark.parametrize("tier", ["1", "2", "3"])
def test_cpp_matches_python_replay(tool, tier):
    rng = np.random.default_rng(int(tier))
    cfg = policy.POLICY["tiers"][tier]
    th = 0.55
    rs = rides(rng)
    text = "\n\n".join("\n".join(f"{v:.17g}" for v in r) for r in rs) + "\n"
    res = subprocess.run([str(tool), tier, str(th)], input=text, capture_output=True, text=True, check=True)
    got = [np.array([int(x) for x in block.split()], bool) for block in res.stdout.split("\n\n")]
    assert len(got) == len(rs)
    for r, g in zip(rs, got):
        want, _ = policy.replay_ride(r, th, cfg["hold_cycles"], policy.POLICY["off_margin"], policy.POLICY["off_hold_cycles"])
        assert np.array_equal(g, want)
    assert any(g.any() for g in got) and not all(g.all() for g in got)  # 켜짐과 꺼짐이 모두 나와야 의미가 있음
