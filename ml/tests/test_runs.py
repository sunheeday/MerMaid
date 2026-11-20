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
