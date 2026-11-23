"""로그 서버 시험용 주행 파일 만들기. 표는 P7과 같은 protocol/ride_schema.sql로 만든다:
스키마가 바뀌면 이 시험도 같이 바뀐다."""
from __future__ import annotations

import json
import os
import sqlite3
import struct
import sys
import uuid
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "server" / "tools"))

KEY = "test-key"
P7_SCHEMA = (REPO / "protocol" / "ride_schema.sql").read_text(encoding="utf-8")
COLUMNS = ["ax", "ay", "az", "gx", "gy", "gz"]


def make_ride(path: Path, ride_id: str | None = None, n_samples: int = 2100, hold=(120, 121),
              cycles: int = 2, phones=("phone-a",), labels=((5.0, "phone-a", 3),), closed: str | None = "ride_end",
              schema_version: int = 1, gap_at: int | None = None) -> Path:
    """P7과 같은 모양의 주행 파일. 샘플 값: ax = seq, az = 9.81, gz = NaN (seq 0만)"""
    ride_id = ride_id or path.name[: -len(".sqlite")]  # 기본: 파일 이름 = meta.ride_id (P7과 같게)
    if path.exists():
        path.unlink()
    con = sqlite3.connect(path)
    con.executescript(P7_SCHEMA)
    device, session, ride_no = ride_id.rsplit("-", 2)
    meta = {"schema_version": str(schema_version), "ride_id": ride_id, "device_id": device, "session": str(int(session)),
            "ride_no": str(int(ride_no)), "source": "live", "columns": json.dumps(COLUMNS), "sample_rate_hz": "100",
            "mono_start_ns": "1000000000", "imu_bias_dps": "[1, -0.5, 0.25]", "model_version": "v007",
            "git_commit": "abc1234", "p1_samples": str(n_samples)}
    if closed:
        meta["closed_reason"] = closed
        meta["mono_end_ns"] = str(1000000000 + n_samples * 10000000)
    con.executemany("INSERT INTO meta VALUES (?, ?)", meta.items())
    seqs = [s for s in range(n_samples) if gap_at is None or not (gap_at <= s < gap_at + 50)]
    chunk: list[int] = []

    def flush():
        if not chunk:
            return
        a = np.zeros((len(chunk), 6), dtype="<f4")
        a[:, 0] = chunk
        a[:, 2] = 9.81
        if chunk[0] == 0:
            a[0, 5] = np.nan
        bits = np.zeros((len(chunk) + 7) // 8 * 8, dtype=np.uint8)
        for i, s in enumerate(chunk):
            if s in hold:
                bits[i] = 1
        hb = np.packbits(bits, bitorder="little").tobytes() if bits.any() else None
        con.execute("INSERT INTO samples VALUES (?, ?, ?, ?, ?)",
                    (chunk[0], len(chunk), 1000000000 + chunk[0] * 10000000, a.tobytes(), hb))
        chunk.clear()

    prev = None
    for s in seqs:
        if chunk and (s != prev + 1 or len(chunk) == 100):
            flush()
        chunk.append(s)
        prev = s
    flush()
    nf = json.loads((REPO / "protocol" / "feature_order.json").read_text())["n_features"]
    x = struct.pack(f"<{nf}d", *([0.5] * nf))
    for c in range(cycles):
        t = 9.99 + 10 * c
        for ph in phones:
            con.execute("INSERT INTO infer VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0)",
                        (c, t, 0, ph, x, 0.95, 0.9, 0.5, 0.2, None))
        for dev, on in (("airflow-01", 1), ("display-01", 0)):
            con.execute("INSERT INTO decision VALUES (?, ?, ?, ?, ?, ?)", (c, t, dev, on, 1 if on else 0, int(c == 0 and on)))
    for t, ph, misc in labels:
        con.execute("INSERT INTO label VALUES (0, ?, ?, ?)", (t, ph, misc))
    con.execute("INSERT INTO ctx VALUES (0, NULL, 'phone-a', 'SCR', '{\"on\":1,\"att\":1}')")
    con.execute("INSERT INTO ctx VALUES (1, 12.5, 'phone-a', 'OVR', '{\"device\":\"airflow-01\",\"on\":0}')")
    con.execute("INSERT INTO health VALUES (0, 20.0, 'hold_samples', ?)", (len(hold),))
    con.execute("INSERT INTO health VALUES (0, 20.0, 'ring_overrun', 0)")
    con.commit()
    con.execute("PRAGMA journal_mode=DELETE")
    con.close()
    return path


# 같은 API 시험을 저장소·DB 조합마다 돌린다. 기본은 로컬 폴더 + SQLite.
# PostgreSQL + S3(SeaweedFS 같은 S3 호환 서버)도 돌리려면 둘 다 정한다 (docs/controller/DATA_LOGGING.md §9-3):
#   MERMAID_TEST_DATABASE_URL=postgresql+psycopg://…  MERMAID_TEST_STORAGE_URL=s3://bucket  (+ AWS_* 환경 변수)
PG_URL = os.environ.get("MERMAID_TEST_DATABASE_URL")
S3_URL = os.environ.get("MERMAID_TEST_STORAGE_URL")
BACKENDS = ["local"] + (["pg_s3"] if PG_URL and S3_URL else [])


def backend_urls(backend: str) -> tuple[str | None, str | None]:
    if backend == "local":
        return None, None
    from server.db import DB, meta

    db = DB(PG_URL)
    meta.drop_all(db.engine)  # 시험마다 빈 DB
    db.dispose()
    return PG_URL, f"{S3_URL.rstrip('/')}/test-{uuid.uuid4().hex[:12]}"  # 시험마다 새 prefix


@pytest.fixture(params=BACKENDS)
def client(request, tmp_path):
    from fastapi.testclient import TestClient

    from server.app import create_app

    db_url, st_url = backend_urls(request.param)
    app = create_app(tmp_path / "data", KEY, database_url=db_url, storage_url=st_url)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def rides_dir(tmp_path):
    d = tmp_path / "car_rides"
    d.mkdir()
    return d
