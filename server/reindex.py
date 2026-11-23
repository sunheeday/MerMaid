"""중앙 DB를 저장소의 주행 파일로 다시 채운다. 주행 파일이 원본이고 DB는 색인이라 언제든 다시 만들 수 있다.

쓰는 때: SQLite → PostgreSQL로 옮길 때, 표 모양을 바꿀 때, DB를 잃었을 때 (S3 버전 관리된 주행 파일에서 복구).
DB에 이미 있는 주행은 건너뛴다. received_at은 다시 채운 시각이 된다 (처음 받은 시각은 주행 파일에 없다).

  python -m server.reindex [--dry-run]     DATABASE_URL, STORAGE_URL, MERMAID_DATA를 서버와 똑같이 읽는다
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .app import _now
from .db import DB, default_url
from .ridefile import RIDE_ID, RideFile, RideFileError
from .storage import Storage, from_env


def reindex(db: DB, store: Storage, dry_run: bool = False, log=print) -> dict:
    have = db.ride_ids()
    added, skipped, bad = [], 0, []
    keys = store.keys("rides/")
    for key in keys:
        name = Path(key).name
        ride_id = name[: -len(".sqlite")] if name.endswith(".sqlite") else ""
        if not RIDE_ID.match(ride_id):
            bad.append((key, "not a <ride_id>.sqlite name"))
            continue
        if ride_id in have:
            skipped += 1
            continue
        path = store.local_path(key)
        try:
            with RideFile(path) as rf:
                rf.validate(ride_id)
                if not dry_run:
                    db.insert_ride(rf, store.sha256(key), path.stat().st_size, _now())
        except RideFileError as e:
            bad.append((key, str(e)))
            continue
        added.append(ride_id)
    stored = {Path(k).name[: -len(".sqlite")] for k in keys}
    orphans = sorted(have - stored)
    for key, why in bad:
        log(f"skip {key}: {why}")
    for rid in orphans:
        log(f"in DB but no ride file in storage: {rid}")
    log(f"{'would add' if dry_run else 'added'} {len(added)}, already indexed {skipped}, bad {len(bad)}, "
        f"missing files {len(orphans)} ({store.url})")
    return {"added": added, "skipped": skipped, "bad": bad, "orphans": orphans}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m server.reindex", description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="count only, write nothing")
    a = ap.parse_args(argv)
    data = os.environ.get("MERMAID_DATA") or Path(__file__).resolve().parent / "data"
    db = DB(os.environ.get("DATABASE_URL") or default_url(data))
    r = reindex(db, from_env(), a.dry_run, log=lambda m: print(m, file=sys.stderr))
    return 1 if r["bad"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
