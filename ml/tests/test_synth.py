"""ml/synth: 설정 변경 검사, 보낼 요약 검사, Claude 요청 모양, 루프 (Claude와 시뮬레이터는 가짜로)"""
import json
import os
import pathlib
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "ml" / "synth"))
sys.path.insert(0, str(REPO / "ml"))
import loop  # noqa: E402
import patch  # noqa: E402
import propose as P  # noqa: E402
import runs  # noqa: E402
import summarize  # noqa: E402

B = patch.load_bounds()
CFGS = patch.load_configs()


# ---------------- bounds.json ----------------
def test_bounds_paths_exist_and_current_values_fit():
    for p, spec in B["paths"].items():
        assert patch.check_value(spec, patch.get(CFGS, p)) is None, p


def test_sourced_blocks_are_locked():
    for p in B["paths"]:
        assert p.split(".")[0] not in ("passenger", "label", "scale", "vehicle"), p
        assert not p.startswith("effects."), p  # 라벨 효과는 실제 MISC 라벨이 생길 때까지 잠금


# ---------------- patch.apply ----------------
def ok(changes):
    return patch.apply(CFGS, changes, B)


def test_valid_change_is_applied_on_a_copy():
    new, err, eff = ok([{"path": "driver.lat_accel_mps2", "value": [1.6, 3.0]},
                        {"path": "sway.lat_per_mps", "value": 0.008}])
    assert err == [] and len(eff) == 2
    assert new["sim_config.json"]["driver"]["lat_accel_mps2"] == [1.6, 3.0]
    assert CFGS["sim_config.json"]["driver"]["lat_accel_mps2"] == [1.5, 3.5]  # 원본 그대로


@pytest.mark.parametrize("changes, word", [
    ([{"path": "passenger.mssq_mean", "value": 20.0}], "바꿀 수 없는"),
    ([{"path": "driver.lat_accel_mps2", "value": [3.0, 1.0]}], "lo > hi"),
    ([{"path": "driver.lat_accel_mps2", "value": [0.1, 3.0]}], "허용 범위"),
    ([{"path": "driver.lat_accel_mps2", "value": 2.0}], "두 숫자"),
    ([{"path": "sway.lat_per_mps", "value": [0.1, 0.2]}], "숫자 하나"),
    ([{"path": "sway.lat_per_mps", "value": float("nan")}], "숫자 하나"),
    ([{"path": "sway.lat_per_mps", "value": 0.01}, {"path": "sway.lat_per_mps", "value": 0.02}], "두 번"),
    ([{"path": "sway.lat_per_mps", "value": CFGS["sim_config.json"]["sway"]["lat_per_mps"]}], "바뀐 것이 없음"),
    ([], "변경이 없음"),
])
def test_invalid_change_is_rejected(changes, word):
    new, err, _ = ok(changes)
    assert new is None and any(word in e for e in err), err


def test_one_bad_change_rejects_the_whole_candidate():
    new, err, _ = ok([{"path": "imu.vib_base", "value": 0.03}, {"path": "passenger.mssq_mean", "value": 20.0}])
    assert new is None and len(err) == 1


def test_too_many_changes():
    ch = [{"path": p, "value": 0.03} for p in list(B["paths"])[:B["max_changes_per_candidate"] + 1]]
    new, err, _ = ok(ch)
    assert new is None and any("한도" in e for e in err)


# ---------------- 보낼 요약 ----------------
def fake_cal(baton=0.2, comma=0.3):
    feat = {"real_q": [0.1, 0.2, 0.3], "sim_q": [0.1, 0.25, 0.4], "ks": baton, "real": 0.2, "sim": 0.25}
    one = lambda ks: {"features": {"rms_x_1m": {**feat, "ks": ks}}, "ks_mean": ks,
                      "speed_share": {"real": [0.3, 0.4, 0.3], "sim": [0.3, 0.5, 0.2]},
                      "label": {"real": [0.9, 0.5, 0.2, 0.06, 0.02], "sim": [0.9, 0.5, 0.2, 0.05, 0.01]}}
    return {"baton": one(baton), "comma": one(comma)}


def test_summary_is_aggregate_and_guard_catches_rows():
    s = summarize.from_calibration(fake_cal())
    summarize.assert_aggregate(s)
    with pytest.raises(ValueError, match="보내면 안 되는"):
        summarize.assert_aggregate({"baton": {"phone": "p1"}})
    with pytest.raises(ValueError, match="목록 길이"):
        summarize.assert_aggregate({"x": list(range(100))})


def test_request_shape():
    req = P.build_request(summarize.from_calibration(fake_cal()), patch.editable(CFGS, B), [], 3, B)
    assert req["model"] == "claude-opus-5-5" and req["output_config"] == {"effort": "high"}
    assert "thinking" not in req and "temperature" not in req  # Opus 5.5: thinking 항상 켜짐, 샘플링 인자 없음
    sys_block = req["system"][0]
    assert sys_block["cache_control"] == {"type": "ephemeral"}
    assert "driver.lat_accel_mps2" in sys_block["text"] and "{bounds}" not in sys_block["text"]
    user = json.loads(req["messages"][0]["content"])
    assert user["task"] == "Propose 3 candidates." and len(user["editable"]) == len(B["paths"])
    assert "current" in user["editable"][0] and "min" not in user["editable"][0]  # 범위는 캐시되는 system에
    json.dumps(req)


# ---------------- 가짜 Claude ----------------
class FakeClient:
    def __init__(self, proposals, stop="end_turn"):
        self.proposals, self.stop, self.calls = list(proposals), stop, []
        self.messages = self

    def parse(self, **kw):
        self.calls.append(kw)
        out = self.proposals.pop(0) if self.proposals else None
        return SimpleNamespace(model=kw["model"], stop_reason=self.stop, stop_details=None, parsed_output=out,
                               usage=SimpleNamespace(model_dump=lambda: {"input_tokens": 1, "output_tokens": 1}))


def cand(h, *changes):
    return P.Candidate(hypothesis=h, changes=[P.Change(path=p, value=v) for p, v in changes])


def test_propose_passes_schema_and_handles_refusal():
    prop = P.Proposal(analysis="a", candidates=[cand("h", ("sway.lat_per_mps", 0.01))])
    c = FakeClient([prop])
    got, meta = P.propose(c, {"model": P.MODEL, "max_tokens": 10, "messages": []})
    assert got is prop and c.calls[0]["output_format"] is P.Proposal and meta["request_hash"]
    with pytest.raises(P.ProposalError, match="거절"):
        P.propose(FakeClient([prop], stop="refusal"), {"model": P.MODEL, "messages": []})


# ---------------- 루프 ----------------
def fake_scorer(cfg_dir):
    """baton KS = |lat_accel hi - 3.0| 기반, comma는 sway가 크면 나빠짐"""
    cfg = json.loads((pathlib.Path(cfg_dir) / "sim_config.json").read_text())
    hi = cfg["driver"]["lat_accel_mps2"][1]
    return fake_cal(baton=0.1 + abs(hi - 3.0) / 5, comma=0.3 + max(0, cfg["sway"]["lat_per_mps"] - 0.01) * 10)


@pytest.fixture
def tmp_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(runs, "ROOT", tmp_path / "runs")
    monkeypatch.setattr(runs, "CURRENT", tmp_path / "runs" / "CURRENT")
    return tmp_path


def args(**kw):
    a = dict(rounds=2, candidates=3, trips=4, seed=0, jobs=0, effort="high", min_gain=0.005, comma_guard=0.02,
             keep_data=False, dry_run=False, note="test")
    a.update(kw)
    return SimpleNamespace(**a)


def test_loop_adopts_only_valid_improving_candidate(tmp_runs, monkeypatch):
    r1 = P.Proposal(analysis="lateral too high", candidates=[
        cand("lower lateral limit", ("driver.lat_accel_mps2", [1.5, 3.1])),
        cand("locked path", ("passenger.mssq_mean", 20.0)),
        cand("more sway, better baton but comma worse", ("driver.lat_accel_mps2", [1.5, 3.0]), ("sway.lat_per_mps", 0.02)),
    ])
    r2 = P.Proposal(analysis="no gain left", candidates=[cand("tiny", ("imu.vib_base", 0.021))])
    client = FakeClient([r1, r2])
    rd = loop.run(args(), client=client, scorer=fake_scorer)
    s = json.loads((rd / "synth.json").read_text())
    c = s["rounds"][0]["candidates"]
    assert c[0]["adopted"] and not c[2]["adopted"]
    assert "rejected" in c[1] and "ks" not in c[1]
    assert s["best_from"] == "r1/c0" and s["best"]["baton"] < s["base"]["baton"]
    assert s["changes_vs_repo"] == [{"path": "driver.lat_accel_mps2", "old": [1.5, 3.5], "new": [1.5, 3.1]}]
    assert not any(x["adopted"] for x in s["rounds"][1]["candidates"])
    # 2라운드 요청은 1라운드 결과를 history로 받고, 기준은 채택된 설정
    u2 = json.loads(client.calls[1]["messages"][0]["content"])
    assert u2["history"][0]["candidates"][1]["rejected"]
    assert next(e for e in u2["editable"] if e["path"] == "driver.lat_accel_mps2")["current"] == [1.5, 3.1]
    best = json.loads((rd / "best" / "sim_config.json").read_text())
    assert best["driver"]["lat_accel_mps2"] == [1.5, 3.1]
    assert runs.meta(rd)["kind"] == "synth"
    assert patch.load_configs() == CFGS  # ml/simulator는 그대로


def test_dry_run_calls_nothing(tmp_runs):
    client = FakeClient([])
    rd = loop.run(args(dry_run=True), client=client, scorer=fake_scorer)
    assert client.calls == [] and (rd / "r1" / "request.json").exists()


def test_adopt_copies_best_and_refuses_locked_drift(tmp_runs, monkeypatch):
    rd = loop.run(args(rounds=1), client=FakeClient([P.Proposal(analysis="", candidates=[
        cand("x", ("driver.lat_accel_mps2", [1.5, 3.0]))])]), scorer=fake_scorer)
    sim = tmp_runs / "sim"
    shutil.copytree(patch.SIM_DIR, sim, ignore=shutil.ignore_patterns("*.py", "__pycache__"))
    monkeypatch.setattr(patch, "SIM_DIR", sim)
    loop.main(["adopt", str(rd)])
    assert json.loads((sim / "sim_config.json").read_text())["driver"]["lat_accel_mps2"] == [1.5, 3.0]
    cfg = json.loads((sim / "sim_config.json").read_text())
    cfg["passenger"]["mssq_mean"] = 1.0  # 그 사이 사람이 잠긴 값을 고침
    (sim / "sim_config.json").write_text(json.dumps(cfg))
    with pytest.raises(SystemExit, match="잠긴 값"):
        loop.main(["adopt", str(rd)])


def test_sim_reads_config_dir_from_env(tmp_path):
    cfgs = json.loads(json.dumps(CFGS))
    cfgs["sim_config.json"]["driver"]["lat_accel_mps2"] = [1.0, 2.0]
    patch.write_configs(cfgs, tmp_path)
    out = subprocess.run([sys.executable, "-c", "import sim; print(sim.CFG['driver']['lat_accel_mps2'], sim.SIM_DIR)"],
                         cwd=REPO / "ml" / "simulator", env={**os.environ, "MERMAID_SIM_DIR": str(tmp_path)},
                         capture_output=True, text=True, check=True).stdout
    assert out.startswith("[1.0, 2.0]") and str(tmp_path.resolve()) in out
