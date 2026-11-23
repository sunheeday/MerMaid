"""저장소 계층(server/storage.py), 저장소-색인 어긋남 복구, reindex, 예전 DB 모양 감지."""
import hashlib
import sqlite3

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from conftest import KEY, make_ride
from server import storage as storage_mod
from server.app import create_app
from server.db import DB, SchemaMismatch, default_url
from server.reindex import reindex
from server.storage import FileStorage, S3Storage, open_storage, ride_key

H = {"X-API-Key": KEY}


def upload(client, path):
    data = path.read_bytes()
    return client.post("/api/rides", headers=H, files={"file": (path.name, data, "application/vnd.sqlite3")},
                       data={"sha256": hashlib.sha256(data).hexdigest()})


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------- 폴더 저장소 ----------
def test_file_storage_put_once(tmp_path):
    st = FileStorage(tmp_path / "store")
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_bytes(b"first")
    b.write_bytes(b"second")
    assert st.put_new("rides/x.sqlite", a, sha(a)) is True
    assert st.put_new("rides/x.sqlite", b, sha(b)) is False, "never overwrites"
    p = st.local_path("rides/x.sqlite")
    assert p.read_bytes() == b"first" and not (p.stat().st_mode & 0o222)
    assert st.sha256("rides/x.sqlite") == sha(a) and st.sha256("rides/y.sqlite") is None
    assert st.keys() == ["rides/x.sqlite"] and a.exists(), "source left in place, no temp files listed"
    with pytest.raises(ValueError):
        st.local_path("../outside")
    with pytest.raises(FileNotFoundError):
        st.local_path("rides/y.sqlite")
    st.delete("rides/x.sqlite")
    assert st.keys() == []


def test_open_storage_urls(tmp_path):
    assert isinstance(open_storage(None, tmp_path), FileStorage)
    assert open_storage(f"file://{tmp_path}/s", tmp_path).root == (tmp_path / "s").resolve()
    for bad in ("s3://", "gs://bucket", "/plain/path"):
        with pytest.raises(ValueError):
            open_storage(bad, tmp_path)


# ---------- S3 저장소 (boto3 클라이언트 흉내: 조건부 쓰기, 메타데이터, prefix) ----------
class FakeS3:
    def __init__(self):
        self.objects: dict[str, tuple[bytes, dict]] = {}

    @staticmethod
    def _err(code):
        from botocore.exceptions import ClientError

        return ClientError({"Error": {"Code": code}}, "op")

    buckets: set = set()
    versioning: dict = {}

    def head_bucket(self, Bucket):
        if self.buckets and Bucket not in self.buckets:
            raise self._err("404")

    def create_bucket(self, Bucket):
        self.buckets.add(Bucket)

    def put_bucket_versioning(self, Bucket, VersioningConfiguration):
        self.versioning[Bucket] = VersioningConfiguration["Status"]

    def put_object(self, Bucket, Key, Body, IfNoneMatch=None, Metadata=None, ContentType=None):
        if IfNoneMatch == "*" and Key in self.objects:
            raise self._err("PreconditionFailed")
        self.objects[Key] = (Body.read(), Metadata or {})

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise self._err("404")
        return {"Metadata": self.objects[Key][1]}

    def download_file(self, Bucket, Key, Filename):
        if Key not in self.objects:
            raise self._err("404")
        with open(Filename, "wb") as f:
            f.write(self.objects[Key][0])

    def delete_object(self, Bucket, Key):
        self.objects.pop(Key, None)

    def get_paginator(self, _name):
        objs = self.objects

        class P:
            def paginate(self, Bucket, Prefix):
                yield {"Contents": [{"Key": k} for k in sorted(objs) if k.startswith(Prefix)]}

        return P()


def test_s3_storage_with_prefix_and_cache(tmp_path):
    fake = FakeS3()
    st = S3Storage("bkt", "/env/prod/", tmp_path / "cache", client=fake)
    a = tmp_path / "a"
    a.write_bytes(b"ride")
    assert st.url == "s3://bkt/env/prod"
    assert st.put_new("rides/x.sqlite", a, sha(a)) is True
    assert st.put_new("rides/x.sqlite", a, sha(a)) is False
    assert list(fake.objects) == ["env/prod/rides/x.sqlite"]
    assert st.keys() == ["rides/x.sqlite"] and st.sha256("rides/x.sqlite") == sha(a)
    assert st.local_path("rides/x.sqlite").read_bytes() == b"ride"
    fake.objects.clear()
    assert st.local_path("rides/x.sqlite").read_bytes() == b"ride", "immutable files are served from the cache"
    st.delete("rides/x.sqlite")
    with pytest.raises(FileNotFoundError):
        st.local_path("rides/x.sqlite")
    assert st.sha256("rides/x.sqlite") is None


def test_s3_init_creates_bucket_with_versioning(tmp_path):
    fake = FakeS3()
    fake.buckets, fake.versioning = {"other"}, {}
    st = S3Storage("bkt", "", tmp_path / "cache", client=fake)
    st.init()
    st.init()
    assert fake.buckets == {"other", "bkt"} and fake.versioning == {"bkt": "Enabled"}


def test_api_on_fake_s3(tmp_path, monkeypatch):
    fake = FakeS3()
    real = S3Storage.__init__
    monkeypatch.setattr(S3Storage, "__init__", lambda self, b, p, c, client=None: real(self, b, p, c, client=fake))
    app = create_app(tmp_path / "data", KEY, storage_url="s3://bkt/mermaid")
    with TestClient(app) as c:
        f = make_ride(tmp_path / "pi01-00001-001.sqlite")
        assert upload(c, f).status_code == 201
        assert list(fake.objects) == ["mermaid/rides/pi01-00001-001.sqlite"]
        assert fake.objects["mermaid/rides/pi01-00001-001.sqlite"][1] == {"sha256": sha(f)}
        assert c.get("/api/rides/pi01-00001-001/file", headers=H).content == f.read_bytes()
        assert c.get("/api/rides/pi01-00001-001/samples?from=0&to=1&cols=ax", headers=H).json()["hz"] == 100


# ---------- 저장소와 색인이 어긋났을 때 ----------
def test_file_in_storage_but_not_indexed(client, tmp_path):
    """저장소에 쓰고 색인 전에 멈춘 경우: 같은 내용이면 다시 올릴 때 색인만 넣고, 다른 내용이면 409"""
    st = client.app.state.storage
    f = make_ride(tmp_path / "pi01-00001-001.sqlite")
    assert st.put_new(ride_key("pi01-00001-001"), f, sha(f))
    r = upload(client, f)
    assert r.status_code == 201 and r.json()["n_cycles"] == 2

    g = make_ride(tmp_path / "pi01-00002-001.sqlite")
    assert st.put_new(ride_key("pi01-00002-001"), g, sha(g))
    make_ride(g, labels=((7.0, "phone-a", 4),))
    assert upload(client, g).status_code == 409
    assert [x["ride_id"] for x in client.get("/api/rides", headers=H).json()] == ["pi01-00001-001"]


def test_index_failure_removes_stored_file(client, tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(client.app.state.db, "insert_ride", boom)
    f = make_ride(tmp_path / "pi01-00001-001.sqlite")
    with pytest.raises(RuntimeError):
        upload(client, f)
    assert client.app.state.storage.keys() == [], "no ride file without an index row"


# ---------- reindex ----------
def test_reindex_into_new_db(client, tmp_path):
    for name in ("pi01-00001-001", "pi01-00001-002"):
        assert upload(client, make_ride(tmp_path / f"{name}.sqlite")).status_code == 201
    st = client.app.state.storage
    bad = tmp_path / "pi01-00009-001.sqlite"
    bad.write_bytes(b"garbage" * 100)
    st.put_new(ride_key("pi01-00009-001"), bad, sha(bad))

    new = DB(default_url(tmp_path / "new"))
    logs = []
    r = reindex(new, st, dry_run=True, log=logs.append)
    assert r["added"] == ["pi01-00001-001", "pi01-00001-002"] and new.count() == 0
    r = reindex(new, st, log=logs.append)
    assert new.count() == 2 and [k for k, _ in r["bad"]] == ["rides/pi01-00009-001.sqlite"]
    old = client.app.state.db
    for rid in ("pi01-00001-001", "pi01-00001-002"):
        assert new.cycles(rid) == old.cycles(rid)
        assert {k: v for k, v in new.ride(rid).items() if k != "received_at"} == \
               {k: v for k, v in old.ride(rid).items() if k != "received_at"}
    assert reindex(new, st, log=logs.append)["skipped"] == 2


def test_reindex_reports_missing_files(tmp_path):
    st = FileStorage(tmp_path / "data")
    db = DB(default_url(tmp_path / "data"))
    f = make_ride(tmp_path / "pi01-00001-001.sqlite")
    from server.ridefile import RideFile

    with RideFile(f) as rf:
        db.insert_ride(rf, sha(f), f.stat().st_size, "2026-01-01T00:00:00Z")
    assert reindex(db, st, log=lambda m: None)["orphans"] == ["pi01-00001-001"]


# ---------- 예전 모양의 DB ----------
def test_old_schema_is_refused(tmp_path):
    path = tmp_path / "mermaid.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE event (ride_id TEXT NOT NULL, t REAL, sender TEXT, kind TEXT, value TEXT)")
    con.close()
    with pytest.raises(SchemaMismatch, match="reindex"):
        DB(f"sqlite:///{path}")


def test_sqlite_url_creates_parent(tmp_path):
    DB(f"sqlite:///{tmp_path}/a/b/mermaid.db")
    eng = create_engine(f"sqlite:///{tmp_path}/a/b/mermaid.db")
    with eng.connect() as con:
        assert con.execute(text("PRAGMA journal_mode")).scalar() == "wal"


# ---------- pull ----------
def test_pull(tmp_path, monkeypatch, capsys):
    st = FileStorage(tmp_path / "data")
    f = make_ride(tmp_path / "pi01-00001-001.sqlite")
    st.put_new(ride_key("pi01-00001-001"), f, sha(f))
    monkeypatch.setenv("MERMAID_DATA", str(tmp_path / "data"))
    monkeypatch.delenv("STORAGE_URL", raising=False)
    dest = tmp_path / "out"
    assert storage_mod.main(["pull", "--dest", str(dest)]) == 0
    assert (dest / "pi01-00001-001.sqlite").read_bytes() == f.read_bytes()
    assert storage_mod.main(["pull", "--dest", str(dest)]) == 0
    assert "0 new" in capsys.readouterr().err
