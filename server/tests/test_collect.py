"""server/tools/collect_logs.py (docs/controller/DATA_LOGGING.md §8): 닫힌 파일만, .uploaded 표시, 다시 돌려도 같은 결과, 실패는 표시 안 함."""
import collect_logs
from conftest import KEY, make_ride


def test_pending_rules():
    names = ["pi01-00001-001.sqlite", "pi01-00001-001.uploaded", "pi01-00001-002.sqlite", "pi01-00001-003.open.sqlite",
             "pi01-00001-003.open.sqlite-wal", "pi01-00000-009.broken.sqlite", "session_count", "x.sqlite"]
    assert collect_logs.pending(names) == ["pi01-00001-002"]


def test_collect_idempotent(client, rides_dir):
    make_ride(rides_dir / "pi01-00001-001.sqlite")
    make_ride(rides_dir / "pi01-00001-002.sqlite")
    (rides_dir / "pi01-00001-003.open.sqlite").write_bytes(b"being written")
    src = collect_logs.LocalSource(str(rides_dir))
    res = collect_logs.collect(src, client, KEY, log=lambda s: None)
    assert res["uploaded"] == ["pi01-00001-001", "pi01-00001-002"] and not res["failed"]
    assert (rides_dir / "pi01-00001-001.uploaded").exists() and (rides_dir / "pi01-00001-002.uploaded").exists()
    assert not (rides_dir / "pi01-00001-003.open.uploaded").exists()

    res = collect_logs.collect(src, client, KEY, log=lambda s: None)
    assert res == {"uploaded": [], "exists": [], "failed": []}, "nothing left to do"

    # 차에서 .uploaded가 사라져도 (예: 손으로 지움) 서버는 같은 파일을 한 번만 갖는다
    (rides_dir / "pi01-00001-001.uploaded").unlink()
    res = collect_logs.collect(src, client, KEY, log=lambda s: None)
    assert res["exists"] == ["pi01-00001-001"] and (rides_dir / "pi01-00001-001.uploaded").exists()
    assert len(client.get("/api/rides", headers={"X-API-Key": KEY}).json()) == 2


def test_collect_failures_not_marked(client, rides_dir):
    make_ride(rides_dir / "pi01-00001-001.sqlite", closed=None)  # 닫히지 않은 파일: 422
    make_ride(rides_dir / "pi01-00001-002.sqlite")
    src = collect_logs.LocalSource(str(rides_dir))
    res = collect_logs.collect(src, client, KEY, log=lambda s: None)
    assert [r for r, _ in res["failed"]] == ["pi01-00001-001"] and "422" in res["failed"][0][1]
    assert not (rides_dir / "pi01-00001-001.uploaded").exists()
    assert res["uploaded"] == ["pi01-00001-002"]

    res = collect_logs.collect(src, client, "wrong-key", log=lambda s: None)
    assert [r for r, _ in res["failed"]] == ["pi01-00001-001"], "only the unmarked file is retried"


def test_dry_run(rides_dir):
    make_ride(rides_dir / "pi01-00001-001.sqlite")
    res = collect_logs.collect(collect_logs.LocalSource(str(rides_dir)), None, KEY, dry_run=True, log=lambda s: None)
    assert res["pending"] == ["pi01-00001-001"] and not (rides_dir / "pi01-00001-001.uploaded").exists()
