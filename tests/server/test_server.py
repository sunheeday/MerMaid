"""로그 서버 API (docs/DATA_LOGGING.md §9-2): 업로드 201/200/409/422, 인증, 목록, 상세, 10초 기록, 원본 구간, 파일."""
import hashlib
import sqlite3

import pytest

from conftest import KEY, make_ride

H = {"X-API-Key": KEY}


def upload(client, path, name=None, sha=None, headers=H):
    data = path.read_bytes()
    return client.post("/api/rides", headers=headers,
                       files={"file": (name or path.name, data, "application/vnd.sqlite3")},
                       data={"sha256": sha or hashlib.sha256(data).hexdigest()})


def test_upload_new_then_same_then_conflict(client, tmp_path):
    f = make_ride(tmp_path / "pi01-00001-001.sqlite")
    r = upload(client, f)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "created" and body["ride_id"] == "pi01-00001-001"
    assert body["n_samples"] == 2100 and body["n_hold"] == 2 and body["n_cycles"] == 2 and body["n_labels"] == 1
    assert body["duration_s"] == pytest.approx(21.0) and body["phones"] == ["phone-a"]
    assert body["health"]["hold_samples"] == 2 and body["model_version"] == "v007"
    stored = client.app.state.data / "rides" / "pi01-00001-001.sqlite"
    assert stored.read_bytes() == f.read_bytes() and not (stored.stat().st_mode & 0o222), "kept read-only, unchanged"
    assert list((client.app.state.data / "incoming").iterdir()) == [], "no leftovers in incoming"

    r = upload(client, f)
    assert r.status_code == 200 and r.json()["status"] == "exists"

    make_ride(f, labels=((6.0, "phone-a", 5),))  # 같은 id, 다른 내용
    assert upload(client, f).status_code == 409
    assert client.get("/api/rides", headers=H).json()[0]["n_labels"] == 1, "first upload kept"


@pytest.mark.parametrize("case", ["sha", "name", "name_mismatch", "not_closed", "schema", "missing_table", "garbage", "bad_sha"])
def test_upload_rejected(client, tmp_path, case):
    f = make_ride(tmp_path / "pi01-00001-001.sqlite", closed=None if case == "not_closed" else "ride_end",
                  schema_version=2 if case == "schema" else 1)
    kw = {}
    if case == "sha":
        kw["sha"] = "0" * 64
    elif case == "bad_sha":
        kw["sha"] = "xyz"
    elif case == "name":
        kw["name"] = "../../etc/passwd.sqlite"
    elif case == "name_mismatch":
        kw["name"] = "pi01-00001-002.sqlite"
    elif case == "missing_table":
        con = sqlite3.connect(f)
        con.execute("DROP TABLE label")
        con.commit()
        con.close()
    elif case == "garbage":
        f.write_bytes(b"not a database at all" * 100)
    r = upload(client, f, **kw)
    assert r.status_code == 422, (case, r.status_code, r.text)
    assert client.get("/api/rides", headers=H).json() == []
    assert list((client.app.state.data / "rides").iterdir()) == []
    assert list((client.app.state.data / "incoming").iterdir()) == []


def test_auth(client, tmp_path):
    f = make_ride(tmp_path / "pi01-00001-001.sqlite")
    assert upload(client, f, headers={}).status_code == 401
    assert upload(client, f, headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.get("/api/rides").status_code == 401
    assert client.get("/api/health").status_code == 200


def test_list_filters_and_detail(client, tmp_path):
    upload(client, make_ride(tmp_path / "pi01-00001-001.sqlite"))
    upload(client, make_ride(tmp_path / "pi01-00001-002.sqlite", labels=()))
    upload(client, make_ride(tmp_path / "pi02-00003-001.sqlite"))
    ids = lambda q="": [r["ride_id"] for r in client.get("/api/rides" + q, headers=H).json()]  # noqa: E731
    assert ids() == ["pi01-00001-001", "pi01-00001-002", "pi02-00003-001"]
    assert ids("?device=pi01") == ["pi01-00001-001", "pi01-00001-002"]
    assert ids("?has_labels=true") == ["pi01-00001-001", "pi02-00003-001"]
    assert ids("?has_labels=false") == ["pi01-00001-002"]
    assert ids("?since=2999-01-01") == []
    d = client.get("/api/rides/pi01-00001-001", headers=H).json()
    assert d["meta"]["imu_bias_dps"] == "[1, -0.5, 0.25]" and d["sha256"]
    assert client.get("/api/rides/pi09-00001-001", headers=H).status_code == 404
    assert client.get("/api/rides/..%2F..%2Fx", headers=H).status_code == 404


def test_cycles(client, tmp_path):
    upload(client, make_ride(tmp_path / "pi01-00001-001.sqlite", phones=("phone-a", "phone-b")))
    c = client.get("/api/rides/pi01-00001-001/cycles", headers=H).json()
    assert [x["seq"] for x in c["cycles"]] == [0, 1]
    first = c["cycles"][0]
    assert first["t"] == pytest.approx(9.99)
    assert first["risk"]["phone-b"] == [0.95, 0.9, 0.5, 0.2, None]
    assert first["devices"]["airflow-01"] == {"on": 1, "tier": 1, "changed": 1}
    assert c["labels"] == [{"t": 5.0, "phone": "phone-a", "misc": 3}]
    assert [e["kind"] for e in c["events"]] == ["SCR", "OVR"], "state before the ride (t NULL) first"


def test_samples(client, tmp_path):
    upload(client, make_ride(tmp_path / "pi01-00001-001.sqlite", gap_at=300))
    get = lambda q: client.get("/api/rides/pi01-00001-001/samples" + q, headers=H)  # noqa: E731
    s = get("?from=1.0&to=1.5&cols=ax,az").json()
    assert s["hz"] == 100 and s["columns"] == ["ax", "az"]
    assert len(s["t"]) == 50 and s["t"][0] == pytest.approx(1.0) and s["data"][0] == [100.0, pytest.approx(9.81)]
    assert s["hold"] == [20, 21], "hold indices inside the window"
    s = get("?from=0&to=0.02").json()
    assert s["data"][0][5] is None, "NaN as null"
    s = get("?from=2.9&to=3.6&cols=ax").json()
    assert len(s["t"]) == 20 and s["t"][10] == pytest.approx(3.5), "gap of 50 samples is not filled"
    assert get("?from=0&to=601").status_code == 422
    assert get("?cols=speed").status_code == 422


def test_file_download(client, tmp_path):
    f = make_ride(tmp_path / "pi01-00001-001.sqlite")
    upload(client, f)
    r = client.get("/api/rides/pi01-00001-001/file", headers=H)
    assert r.status_code == 200 and r.content == f.read_bytes()
