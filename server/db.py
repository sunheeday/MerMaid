"""중앙 DB (server/data/mermaid.db, docs/DATA_LOGGING.md §9-1).

색인과 10초 기록만 둔다. 원본 샘플과 77칸 입력은 주행 파일(server/data/rides/<ride_id>.sqlite)에 그대로 남는다.
표준 라이브러리 sqlite3. PostgreSQL로 옮길 때 이 파일만 바꾼다.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import asdict
from pathlib import Path

from .ridefile import RideFile

SCHEMA = """
CREATE TABLE IF NOT EXISTS ride (
  ride_id TEXT PRIMARY KEY, device_id TEXT, session INTEGER, ride_no INTEGER,
  source TEXT, schema_version INTEGER, model_version TEXT, git_commit TEXT,
  wall_start TEXT, duration_s REAL, n_samples INTEGER, n_hold INTEGER,
  n_cycles INTEGER, n_labels INTEGER, phones TEXT,
  closed_reason TEXT, sha256 TEXT NOT NULL, size_bytes INTEGER,
  received_at TEXT NOT NULL, health TEXT
);
CREATE TABLE IF NOT EXISTS cycle (ride_id TEXT NOT NULL, seq INTEGER, t REAL, phone TEXT,
  p2 REAL, p3 REAL, p4 REAL, p5 REAL, p6 REAL, cal_a REAL, cal_b REAL, PRIMARY KEY (ride_id, seq, phone));
CREATE TABLE IF NOT EXISTS decision (ride_id TEXT NOT NULL, seq INTEGER, t REAL, device TEXT,
  on_ INTEGER, tier INTEGER, changed INTEGER, PRIMARY KEY (ride_id, seq, device));
CREATE TABLE IF NOT EXISTS label (ride_id TEXT NOT NULL, t REAL, phone TEXT, misc INTEGER);
CREATE TABLE IF NOT EXISTS event (ride_id TEXT NOT NULL, t REAL, sender TEXT, kind TEXT, value TEXT);
CREATE INDEX IF NOT EXISTS label_ride ON label(ride_id, t);
CREATE INDEX IF NOT EXISTS event_ride ON event(ride_id, t);
CREATE INDEX IF NOT EXISTS ride_device ON ride(device_id, received_at);
"""

RIDE_COLS = ("ride_id", "device_id", "session", "ride_no", "source", "schema_version", "model_version", "git_commit",
             "wall_start", "duration_s", "n_samples", "n_hold", "n_cycles", "n_labels", "phones", "closed_reason",
             "sha256", "size_bytes", "received_at", "health")


class DB:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        with self._con() as con:
            con.executescript(SCHEMA)

    def _con(self) -> sqlite3.Connection:
        con = getattr(self._local, "con", None)
        if con is None:
            con = sqlite3.connect(self.path, timeout=10)
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA foreign_keys=ON")
            con.row_factory = sqlite3.Row
            self._local.con = con
        return con

    # ---------- 쓰기 ----------
    def insert_ride(self, rf: RideFile, sha256: str, size_bytes: int, received_at: str) -> None:
        """주행 하나의 색인과 10초 기록을 한 트랜잭션으로"""
        s = rf.summary()
        row = asdict(s)
        row.update(phones=json.dumps(s.phones), health=json.dumps(s.health), sha256=sha256, size_bytes=size_bytes,
                   received_at=received_at)
        rid = s.ride_id
        con = self._con()
        with con:
            con.execute(f"INSERT INTO ride ({', '.join(RIDE_COLS)}) VALUES ({', '.join('?' * len(RIDE_COLS))})",
                        [row[c] for c in RIDE_COLS])
            con.executemany(
                "INSERT INTO cycle VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(rid, r["seq"], r["t"], r["phone"], r["p2"], r["p3"], r["p4"], r["p5"], r["p6"], r["cal_a"], r["cal_b"])
                 for r in rf.infer_rows()])
            con.executemany("INSERT INTO decision VALUES (?, ?, ?, ?, ?, ?, ?)",
                            [(rid, r["seq"], r["t"], r["device"], r["on"], r["tier"], r["changed"])
                             for r in rf.decision_rows()])
            con.executemany("INSERT INTO label VALUES (?, ?, ?, ?)",
                            [(rid, r["t"], r["phone"], r["misc"]) for r in rf.label_rows()])
            con.executemany("INSERT INTO event VALUES (?, ?, ?, ?, ?)",
                            [(rid, r["t"], r["sender"], r["kind"], r["value"]) for r in rf.ctx_rows()])

    # ---------- 읽기 ----------
    @staticmethod
    def _ride(r: sqlite3.Row) -> dict:
        d = dict(r)
        d["phones"] = json.loads(d["phones"] or "[]")
        d["health"] = json.loads(d["health"] or "{}")
        return d

    def ride(self, ride_id: str) -> dict | None:
        r = self._con().execute("SELECT * FROM ride WHERE ride_id = ?", (ride_id,)).fetchone()
        return self._ride(r) if r else None

    def rides(self, device: str | None = None, since: str | None = None, has_labels: bool | None = None) -> list[dict]:
        where, args = [], []
        if device:
            where.append("device_id = ?")
            args.append(device)
        if since:
            where.append("received_at >= ?")
            args.append(since)
        if has_labels is True:
            where.append("n_labels > 0")
        elif has_labels is False:
            where.append("n_labels = 0")
        sql = "SELECT * FROM ride" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY ride_id"
        return [self._ride(r) for r in self._con().execute(sql, args)]

    def cycles(self, ride_id: str) -> dict:
        """10초 기록: 주기마다 폰별 확률과 기기 상태. 라벨과 기기 사건은 t 순서 목록으로 따로"""
        con = self._con()
        by_seq: dict[int, dict] = {}
        for r in con.execute("SELECT seq, t, phone, p2, p3, p4, p5, p6 FROM cycle WHERE ride_id = ? ORDER BY seq, phone",
                             (ride_id,)):
            c = by_seq.setdefault(r["seq"], {"seq": r["seq"], "t": r["t"], "risk": {}, "devices": {}})
            c["risk"][r["phone"]] = [r["p2"], r["p3"], r["p4"], r["p5"], r["p6"]]
        for r in con.execute("SELECT seq, t, device, on_, tier, changed FROM decision WHERE ride_id = ? ORDER BY seq",
                             (ride_id,)):
            c = by_seq.setdefault(r["seq"], {"seq": r["seq"], "t": r["t"], "risk": {}, "devices": {}})
            c["devices"][r["device"]] = {"on": r["on_"], "tier": r["tier"], "changed": r["changed"]}
        labels = [dict(r) for r in con.execute(
            "SELECT t, phone, misc FROM label WHERE ride_id = ? ORDER BY t IS NULL, t", (ride_id,))]
        events = [dict(r) for r in con.execute(
            "SELECT t, sender, kind, value FROM event WHERE ride_id = ? ORDER BY t IS NOT NULL, t, rowid", (ride_id,))]
        return {"cycles": [by_seq[k] for k in sorted(by_seq)], "labels": labels, "events": events}
