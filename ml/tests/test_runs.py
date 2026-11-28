import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import runs  # noqa: E402


def test_new_never_reuses_a_version(tmp_path, monkeypatch):
    monkeypatch.setattr(runs, "ROOT", tmp_path)
    monkeypatch.setattr(runs, "CURRENT", tmp_path / "CURRENT")
    a = runs.new("dataset", "a")
    (a / "dataset.npz").write_bytes(b"x")
    b = runs.new("models", "b", inputs={"dataset": a / "dataset.npz"})
    assert (a.name, b.name) == ("v001", "v002")
    assert runs.latest_with("dataset.npz") == a / "dataset.npz"
    assert runs.dataset_for(b).resolve() == (a / "dataset.npz").resolve()
    assert runs.meta(b)["inputs"]["dataset"].endswith("v001/dataset.npz")


def test_v1_tools_skip_a_deployed_v3_model(tmp_path, monkeypatch):
    for name in ("ROOT", "CURRENT", "CURRENT_V1", "THRESHOLDS"):
        monkeypatch.setattr(runs, name, tmp_path / {"ROOT": "", "CURRENT": "CURRENT", "CURRENT_V1": "CURRENT_V1", "THRESHOLDS": "th.json"}[name])
    def model(kind):
        r = runs.new("models", kind or "v1")
        (r / "models").mkdir()
        (r / "models" / "models.json").write_text(json.dumps({"kind": kind} if kind else {}))
        (r / "thresholds.json").write_text(json.dumps({"base_th": {"1": 0.5, "2": 0.5, "3": 0.5}}))
        return r
    a, b, c = model(None), model(None), model("ctx_gru")
    assert runs.current_v1_models() == b                 # 배포 기록 없음: V1이 있는 최신
    runs.use(a.name)
    assert runs.current_v1_models() == a                 # V1 배포
    runs.use(c.name)
    assert runs.current_models() == c and runs.model_kind(c) == "ctx_gru"
    assert runs.current_v1_models() == a                 # V3 배포 뒤에도 마지막 V1
