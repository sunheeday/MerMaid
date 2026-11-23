"""중앙 DB (docs/controller/DATA_LOGGING.md §9-1). 색인과 10초 기록만 둔다.

원본 샘플과 모델 입력(infer.x)은 주행 파일(저장소 rides/<ride_id>.sqlite)에 그대로 남는다. 그래서 이 DB는 주행 파일에서 언제든
다시 만들 수 있다 (python -m server.reindex).

SQLAlchemy Core. 연결은 DATABASE_URL 하나로 정한다:
  sqlite:///server/data/mermaid.db               로컬 기본 (파일 하나)
  postgresql+psycopg://user:pw@host:5432/mermaid  docker compose, AWS RDS
표는 시작할 때 없으면 만든다 (create_all). 표 모양을 바꿀 때는 새 DB에 reindex로 다시 채운다.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from sqlalchemy import (BigInteger, Column, Float, Index, Integer, MetaData, Table, Text, create_engine, event,
                        func, insert, inspect, select)
from sqlalchemy.engine import Engine, make_url

from .ridefile import RideFile

meta = MetaData()

ride = Table(
    "ride", meta,
    Column("ride_id", Text, primary_key=True),
    Column("device_id", Text), Column("session", Integer), Column("ride_no", Integer),
    Column("source", Text), Column("schema_version", Integer), Column("model_version", Text),
    Column("git_commit", Text), Column("wall_start", Text), Column("duration_s", Float),
    Column("n_samples", BigInteger), Column("n_hold", BigInteger), Column("n_cycles", Integer),
    Column("n_labels", Integer), Column("phones", Text), Column("closed_reason", Text),
    Column("sha256", Text, nullable=False), Column("size_bytes", BigInteger),
    Column("received_at", Text, nullable=False), Column("health", Text),
    Index("ride_device", "device_id", "received_at"),
)
cycle = Table(
    "cycle", meta,
    Column("ride_id", Text, primary_key=True), Column("seq", Integer, primary_key=True),
    Column("t", Float), Column("phone", Text, primary_key=True),
    *(Column(p, Float) for p in ("p2", "p3", "p4", "p5", "p6")), Column("cal_a", Float), Column("cal_b", Float),
)
decision = Table(
    "decision", meta,
    Column("ride_id", Text, primary_key=True), Column("seq", Integer, primary_key=True),
    Column("t", Float), Column("device", Text, primary_key=True),
    Column("on_", Integer), Column("tier", Integer), Column("changed", Integer),
)
label = Table(
    "label", meta,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("ride_id", Text, nullable=False), Column("t", Float), Column("phone", Text),
    Column("misc", Integer),
    Index("label_ride", "ride_id", "t"),
)
ride_event = Table(
    "event", meta,
    Column("id", Integer, primary_key=True, autoincrement=True),  # 같은 t 안에서 파일에 적힌 순서
    Column("ride_id", Text, nullable=False), Column("t", Float), Column("sender", Text),
    Column("kind", Text), Column("value", Text),
    Index("event_ride", "ride_id", "t"),
)

RIDE_COLS = tuple(c.name for c in ride.columns)


def default_url(data_dir: str | Path) -> str:
    return f"sqlite:///{Path(data_dir).resolve() / 'mermaid.db'}"


def make_engine(url: str) -> Engine:
    u = make_url(url)
    if u.get_backend_name() != "sqlite":
        return create_engine(url, pool_pre_ping=True)
    if u.database and u.database != ":memory:":
        Path(u.database).parent.mkdir(parents=True, exist_ok=True)
    eng = create_engine(url, connect_args={"timeout": 10, "check_same_thread": False})

    @event.listens_for(eng, "connect")
    def _pragma(con, _):
        cur = con.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    return eng


class SchemaMismatch(RuntimeError):
    pass


class DB:
    def __init__(self, url: str):
        self.engine = make_engine(url)
        meta.create_all(self.engine)
        self._check_columns()

    def _check_columns(self) -> None:
        """예전 모양의 표가 이미 있으면 create_all은 고치지 않는다. 조용히 틀리지 않게 시작할 때 멈춘다"""
        insp = inspect(self.engine)
        for t in meta.sorted_tables:
            have = {c["name"] for c in insp.get_columns(t.name)}
            missing = [c.name for c in t.columns if c.name not in have]
            if missing:
                raise SchemaMismatch(f"table {t.name} lacks columns {missing}: point DATABASE_URL at a new database "
                                     f"and run `python -m server.reindex` to refill it from the ride files")

    def dispose(self) -> None:
        self.engine.dispose()

    # ---------- 쓰기 ----------
    def insert_ride(self, rf: RideFile, sha256: str, size_bytes: int, received_at: str) -> None:
        """주행 하나의 색인과 10초 기록을 한 트랜잭션으로. 같은 ride_id가 이미 있으면 IntegrityError"""
        s = rf.summary()
        row = asdict(s)
        row.update(phones=json.dumps(s.phones), health=json.dumps(s.health), sha256=sha256, size_bytes=size_bytes,
                   received_at=received_at)
        rid = s.ride_id
        cycles = [{"ride_id": rid, **{k: r[k] for k in ("seq", "t", "phone", "p2", "p3", "p4", "p5", "p6", "cal_a",
                                                         "cal_b")}} for r in rf.infer_rows()]
        decisions = [{"ride_id": rid, "seq": r["seq"], "t": r["t"], "device": r["device"], "on_": r["on"],
                      "tier": r["tier"], "changed": r["changed"]} for r in rf.decision_rows()]
        labels = [{"ride_id": rid, "t": r["t"], "phone": r["phone"], "misc": r["misc"]} for r in rf.label_rows()]
        events = [{"ride_id": rid, "t": r["t"], "sender": r["sender"], "kind": r["kind"], "value": r["value"]}
                  for r in rf.ctx_rows()]
        with self.engine.begin() as con:
            con.execute(insert(ride), {c: row[c] for c in RIDE_COLS})
            for table, rows in ((cycle, cycles), (decision, decisions), (label, labels), (ride_event, events)):
                if rows:
                    con.execute(insert(table), rows)

    # ---------- 읽기 ----------
    @staticmethod
    def _ride(r) -> dict:
        d = dict(r._mapping)
        d["phones"] = json.loads(d["phones"] or "[]")
        d["health"] = json.loads(d["health"] or "{}")
        return d

    def count(self) -> int:
        with self.engine.connect() as con:
            return con.execute(select(func.count()).select_from(ride)).scalar_one()

    def ride(self, ride_id: str) -> dict | None:
        with self.engine.connect() as con:
            r = con.execute(select(ride).where(ride.c.ride_id == ride_id)).first()
        return self._ride(r) if r else None

    def ride_ids(self) -> set[str]:
        with self.engine.connect() as con:
            return set(con.execute(select(ride.c.ride_id)).scalars())

    def rides(self, device: str | None = None, since: str | None = None, has_labels: bool | None = None) -> list[dict]:
        q = select(ride)
        if device:
            q = q.where(ride.c.device_id == device)
        if since:
            q = q.where(ride.c.received_at >= since)
        if has_labels is True:
            q = q.where(ride.c.n_labels > 0)
        elif has_labels is False:
            q = q.where(ride.c.n_labels == 0)
        with self.engine.connect() as con:
            return [self._ride(r) for r in con.execute(q.order_by(ride.c.ride_id))]

    def cycles(self, ride_id: str) -> dict:
        """10초 기록: 주기마다 폰별 확률과 기기 상태. 라벨과 기기 사건은 t 순서 목록으로 따로"""
        by_seq: dict[int, dict] = {}

        def at(r) -> dict:  # r는 mapping: Row.t는 SQLAlchemy가 쓰는 이름이라 r["t"]로 읽는다
            return by_seq.setdefault(r["seq"], {"seq": r["seq"], "t": r["t"], "risk": {}, "devices": {}})

        c, d, lb, ev = cycle.c, decision.c, label.c, ride_event.c
        with self.engine.connect() as con:
            for r in con.execute(select(c.seq, c.t, c.phone, c.p2, c.p3, c.p4, c.p5, c.p6)
                                 .where(c.ride_id == ride_id).order_by(c.seq, c.phone)).mappings():
                at(r)["risk"][r["phone"]] = [r[p] for p in ("p2", "p3", "p4", "p5", "p6")]
            for r in con.execute(select(d.seq, d.t, d.device, d.on_, d.tier, d.changed)
                                 .where(d.ride_id == ride_id).order_by(d.seq, d.device)).mappings():
                at(r)["devices"][r["device"]] = {"on": r["on_"], "tier": r["tier"], "changed": r["changed"]}
            labels = [dict(r._mapping) for r in con.execute(
                select(lb.t, lb.phone, lb.misc).where(lb.ride_id == ride_id).order_by(lb.t.is_(None), lb.t, lb.id))]
            # 주행 전 상태(t 없음)가 먼저, 그다음 t 순서
            events = [dict(r._mapping) for r in con.execute(
                select(ev.t, ev.sender, ev.kind, ev.value).where(ev.ride_id == ride_id)
                .order_by(ev.t.is_not(None), ev.t, ev.id))]
        return {"cycles": [by_seq[k] for k in sorted(by_seq)], "labels": labels, "events": events}
