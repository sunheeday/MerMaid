"""P7 주행 파일(<ride_id>.sqlite) 읽기와 검증 (docs/DATA_LOGGING.md §6-5).

서버(server/app.py)와 학습 데이터 내보내기(ml/realdata, 6단계)가 같이 쓴다. 표준 라이브러리 sqlite3 + numpy.
파일은 읽기 전용으로 연다. 받은 파일은 서버가 바꾸지 않는다.
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import numpy as np

SCHEMA_VERSION = 1
TABLES = ("meta", "samples", "infer", "decision", "ctx", "label", "health")
RIDE_ID = re.compile(r"^[A-Za-z0-9_.-]{1,64}-\d{5}-\d{3,}$")  # <device>-<session:05d>-<ride:03d>
MAX_SAMPLE_WINDOW_S = 600.0
PROBS = ("p2", "p3", "p4", "p5", "p6")


class RideFileError(Exception):
    """검증 실패 (서버는 422로 돌려준다)"""


@dataclass
class Summary:
    ride_id: str
    device_id: str | None
    session: int | None
    ride_no: int | None
    source: str | None
    schema_version: int
    model_version: str | None
    git_commit: str | None
    wall_start: str | None
    duration_s: float | None
    n_samples: int
    n_hold: int
    n_cycles: int
    n_labels: int
    phones: list[str]
    closed_reason: str | None
    health: dict


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


class RideFile:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        if not self.path.is_file():
            raise RideFileError(f"no such file: {self.path.name}")
        # immutable=1: 잠금·WAL 파일을 만들지 않는다 (닫힌 주행 파일은 바뀌지 않는다)
        uri = f"file:{self.path.resolve()}?mode=ro&immutable=1"
        try:
            self.con = sqlite3.connect(uri, uri=True)
            self.con.execute("SELECT count(*) FROM sqlite_master").fetchone()
        except sqlite3.DatabaseError as e:
            raise RideFileError(f"not a SQLite database: {e}") from e
        self._meta: dict[str, str] | None = None

    def close(self):
        self.con.close()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    # ---------- 검증 ----------
    def validate(self, expected_id: str | None = None) -> None:
        """서버가 받기 전에: 무결성, 표, schema_version, 파일 이름 = meta.ride_id. 실패하면 RideFileError"""
        try:
            ok = self.con.execute("PRAGMA integrity_check").fetchone()[0]
        except sqlite3.DatabaseError as e:
            raise RideFileError(f"integrity_check failed: {e}") from e
        if ok != "ok":
            raise RideFileError(f"integrity_check: {ok}")
        have = {r[0] for r in self.con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        missing = [t for t in TABLES if t not in have]
        if missing:
            raise RideFileError(f"missing tables: {', '.join(missing)}")
        m = self.meta
        if _int(m.get("schema_version")) != SCHEMA_VERSION:
            raise RideFileError(f"schema_version {m.get('schema_version')!r}, expected {SCHEMA_VERSION}")
        rid = m.get("ride_id")
        if not rid or not RIDE_ID.match(rid):
            raise RideFileError(f"bad meta.ride_id {rid!r}")
        if expected_id is not None and rid != expected_id:
            raise RideFileError(f"file name {expected_id!r} does not match meta.ride_id {rid!r}")
        if not m.get("closed_reason"):
            raise RideFileError("ride file was not closed (no meta.closed_reason)")
        try:
            self.columns
        except (ValueError, TypeError) as e:
            raise RideFileError(f"bad meta.columns: {e}") from e

    # ---------- meta ----------
    @property
    def meta(self) -> dict[str, str]:
        if self._meta is None:
            self._meta = dict(self.con.execute("SELECT key, value FROM meta"))
        return self._meta

    @property
    def ride_id(self) -> str:
        return self.meta["ride_id"]

    @property
    def columns(self) -> list[str]:
        cols = json.loads(self.meta.get("columns", "[]"))
        if not isinstance(cols, list) or not all(isinstance(c, str) for c in cols):
            raise ValueError("columns must be a list of names")
        return cols

    @property
    def hz(self) -> float:
        try:
            return float(self.meta.get("sample_rate_hz", 100))
        except ValueError:
            return 100.0

    # ---------- 요약 ----------
    def summary(self) -> Summary:
        m = self.meta
        n_samples, first, end = self.con.execute(
            "SELECT coalesce(sum(n), 0), min(seq0), max(seq0 + n) FROM samples").fetchone()
        n_hold = 0
        for (h,) in self.con.execute("SELECT hold FROM samples WHERE hold IS NOT NULL"):
            n_hold += int(np.unpackbits(np.frombuffer(h, dtype=np.uint8)).sum())
        if n_samples and first is not None:
            duration = (end - first) / self.hz
        else:
            s, e = _int(m.get("mono_start_ns")), _int(m.get("mono_end_ns"))
            duration = (e - s) / 1e9 if s is not None and e is not None and e >= s else None
        n_cycles = self.con.execute("SELECT count(DISTINCT seq) FROM infer").fetchone()[0]
        n_labels = self.con.execute("SELECT count(*) FROM label").fetchone()[0]
        phones = sorted({r[0] for r in self.con.execute(
            "SELECT phone FROM infer WHERE phone != 'default' UNION SELECT phone FROM label")})
        return Summary(
            ride_id=m["ride_id"], device_id=m.get("device_id"), session=_int(m.get("session")),
            ride_no=_int(m.get("ride_no")), source=m.get("source"), schema_version=_int(m.get("schema_version")) or 0,
            model_version=m.get("model_version"), git_commit=m.get("git_commit"), wall_start=m.get("wall_start"),
            duration_s=duration, n_samples=int(n_samples), n_hold=n_hold, n_cycles=n_cycles, n_labels=n_labels,
            phones=phones, closed_reason=m.get("closed_reason"), health=self.health())

    def health(self) -> dict:
        """key마다 마지막 값 (sample_rate_hz는 최소·최대도)"""
        out: dict = {}
        rates = []
        for key, value in self.con.execute("SELECT key, value FROM health ORDER BY mono_ns, rowid"):
            out[key] = value
            if key == "sample_rate_hz":
                rates.append(value)
        if rates:
            out["sample_rate_hz"] = {"min": min(rates), "max": max(rates), "last": rates[-1]}
        return out

    # ---------- 표 ----------
    def infer_rows(self, with_x: bool = False) -> list[dict]:
        cols = "seq, t, mono_ns, phone, p2, p3, p4, p5, p6, cal_a, cal_b" + (", x" if with_x else "")
        rows = []
        for r in self.con.execute(f"SELECT {cols} FROM infer ORDER BY seq, phone"):
            d = dict(zip(("seq", "t", "mono_ns", "phone", *PROBS, "cal_a", "cal_b"), r[:11]))
            if with_x:
                d["x"] = np.frombuffer(r[11], dtype="<f8").copy()
            rows.append(d)
        return rows

    def decision_rows(self) -> list[dict]:
        return [dict(zip(("seq", "t", "device", "on", "tier", "changed"), r)) for r in self.con.execute(
            "SELECT seq, t, device, on_, tier, changed FROM decision ORDER BY seq, device")]

    def ctx_rows(self) -> list[dict]:
        return [dict(zip(("mono_ns", "t", "sender", "kind", "value"), r)) for r in self.con.execute(
            "SELECT mono_ns, t, sender, kind, value FROM ctx ORDER BY mono_ns, rowid")]

    def label_rows(self) -> list[dict]:
        return [dict(zip(("mono_ns", "t", "phone", "misc"), r)) for r in self.con.execute(
            "SELECT mono_ns, t, phone, misc FROM label ORDER BY mono_ns, rowid")]

    # ---------- 원본 샘플 ----------
    def samples(self, t_from: float = 0.0, t_to: float | None = None, cols: list[str] | None = None) -> dict:
        """[t_from, t_to) 구간의 원본. 반환: {hz, columns, t, data(n × cols, NaN 가능), hold(구간 안 인덱스)}"""
        hz = self.hz
        all_cols = self.columns
        if t_to is None:
            t_to = t_from + MAX_SAMPLE_WINDOW_S
        if t_to < t_from:
            raise ValueError("to must be >= from")
        if t_to - t_from > MAX_SAMPLE_WINDOW_S:
            raise ValueError(f"window longer than {MAX_SAMPLE_WINDOW_S:g} s")
        use = cols or all_cols
        unknown = [c for c in use if c not in all_cols]
        if unknown:
            raise ValueError(f"unknown columns: {', '.join(unknown)}")
        idx = [all_cols.index(c) for c in use]
        s0, s1 = int(np.floor(t_from * hz)), int(np.ceil(t_to * hz))
        ts, parts, hold = [], [], []
        k = len(all_cols)
        for seq0, n, data, hb in self.con.execute(
                "SELECT seq0, n, data, hold FROM samples WHERE seq0 + n > ? AND seq0 < ? ORDER BY seq0", (s0, s1)):
            if k == 0:
                continue
            a = np.frombuffer(data, dtype="<f4").reshape(n, k)
            seq = np.arange(seq0, seq0 + n)
            keep = (seq >= s0) & (seq < s1)
            hbits = np.unpackbits(np.frombuffer(hb, dtype=np.uint8), bitorder="little")[:n].astype(bool) if hb else None
            base = sum(len(x) for x in ts)
            ts.append(seq[keep] / hz)
            parts.append(a[keep][:, idx])
            if hbits is not None:
                hold.extend((base + np.flatnonzero(hbits[keep])).tolist())
        t = np.concatenate(ts) if ts else np.zeros(0)
        data = np.concatenate(parts) if parts else np.zeros((0, len(idx)), dtype="<f4")
        return {"hz": hz, "columns": use, "t": t, "data": data, "hold": hold}
