"""MerMaid 로그 서버 (docs/controller/DATA_LOGGING.md §9). 차의 P7이 남긴 주행 파일을 받아 보관하고, 색인·10초 기록·원본 구간을 돌려준다.

실행 (저장소 루트에서):
  MERMAID_API_KEY=… uvicorn server.app:app --host 0.0.0.0 --port 8080
환경 변수 (server/.env.example을 server/.env로 복사해 채운다):
  MERMAID_API_KEY   필수. 모든 /api 요청의 X-API-Key 헤더 (/api/health 제외)
  MERMAID_DATA      데이터 폴더 (기본 server/data): incoming/, rides/, mermaid.db
  MERMAID_MAX_UPLOAD_MB  업로드 최대 크기 (기본 1024)
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from .db import DB
from .ridefile import RIDE_ID, RideFile, RideFileError

SHA256 = re.compile(r"^[0-9a-f]{64}$")
CHUNK = 1 << 20


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _nan_to_none(a: np.ndarray) -> list:
    return [[None if not np.isfinite(v) else float(v) for v in row] for row in a.tolist()] if a.size else []


def create_app(data_dir: str | Path | None = None, api_key: str | None = None, max_upload_mb: float | None = None) -> FastAPI:
    data = Path(data_dir or os.environ.get("MERMAID_DATA") or Path(__file__).resolve().parent / "data")
    key = api_key if api_key is not None else os.environ.get("MERMAID_API_KEY", "")
    if not key:
        raise RuntimeError("MERMAID_API_KEY is not set")
    max_bytes = int((max_upload_mb or float(os.environ.get("MERMAID_MAX_UPLOAD_MB", "1024"))) * 1048576)
    incoming, rides = data / "incoming", data / "rides"
    incoming.mkdir(parents=True, exist_ok=True)
    rides.mkdir(parents=True, exist_ok=True)
    db = DB(data / "mermaid.db")
    lock = threading.Lock()  # 같은 ride_id를 동시에 올려도 하나만 받는다

    app = FastAPI(title="MerMaid log server", version="1")
    app.state.db = db
    app.state.data = data

    def auth(x_api_key: str | None = Header(default=None)):
        if not x_api_key or not hmac.compare_digest(x_api_key.encode(), key.encode()):
            raise HTTPException(401, "missing or wrong X-API-Key")

    def ride_or_404(ride_id: str) -> dict:
        r = db.ride(ride_id) if RIDE_ID.match(ride_id) else None
        if r is None:
            raise HTTPException(404, f"no ride {ride_id}")
        return r

    @app.get("/api/health")
    def health():
        return {"ok": True, "rides": len(db.rides())}

    @app.post("/api/rides", dependencies=[Depends(auth)])
    def upload(file: UploadFile = File(...), sha256: str = Form(...)):
        name = Path(file.filename or "").name
        if not name.endswith(".sqlite") or not RIDE_ID.match(name[: -len(".sqlite")]):
            raise HTTPException(422, f"file name must be <ride_id>.sqlite, got {name!r}")
        ride_id = name[: -len(".sqlite")]
        sha256 = sha256.strip().lower()
        if not SHA256.match(sha256):
            raise HTTPException(422, "sha256 must be 64 hex digits")
        part = incoming / f"{uuid.uuid4().hex}.part"
        try:
            h, size = hashlib.sha256(), 0
            with open(part, "wb") as out:
                while chunk := file.file.read(CHUNK):
                    size += len(chunk)
                    if size > max_bytes:
                        raise HTTPException(413, f"file larger than {max_bytes // 1048576} MB")
                    h.update(chunk)
                    out.write(chunk)
                out.flush()
                os.fsync(out.fileno())
            if h.hexdigest() != sha256:
                raise HTTPException(422, f"sha256 mismatch (got {h.hexdigest()})")
            with lock:
                have = db.ride(ride_id)
                if have is not None:
                    if have["sha256"] == sha256:
                        return JSONResponse({"ride_id": ride_id, "status": "exists"}, status_code=200)
                    raise HTTPException(409, f"{ride_id} already stored with a different sha256")
                try:
                    with RideFile(part) as rf:
                        rf.validate(ride_id)
                except RideFileError as e:
                    raise HTTPException(422, str(e)) from e
                dest = rides / name
                os.replace(part, dest)
                os.chmod(dest, 0o444)
                try:
                    with RideFile(dest) as rf:
                        db.insert_ride(rf, sha256, size, _now())
                except Exception:
                    dest.unlink(missing_ok=True)
                    raise
            r = db.ride(ride_id)
            return JSONResponse({"status": "created", **r}, status_code=201)
        finally:
            part.unlink(missing_ok=True)

    @app.get("/api/rides", dependencies=[Depends(auth)])
    def list_rides(device: str | None = None, since: str | None = None, has_labels: bool | None = None):
        keep = ("ride_id", "device_id", "wall_start", "received_at", "duration_s", "n_samples", "n_cycles", "n_labels",
                "phones", "closed_reason", "health")
        return [{k: r[k] for k in keep} for r in db.rides(device, since, has_labels)]

    @app.get("/api/rides/{ride_id}", dependencies=[Depends(auth)])
    def get_ride(ride_id: str):
        r = ride_or_404(ride_id)
        with RideFile(rides / f"{ride_id}.sqlite") as rf:
            r["meta"] = rf.meta
        return r

    @app.get("/api/rides/{ride_id}/cycles", dependencies=[Depends(auth)])
    def get_cycles(ride_id: str):
        ride_or_404(ride_id)
        return db.cycles(ride_id)

    @app.get("/api/rides/{ride_id}/samples", dependencies=[Depends(auth)])
    def get_samples(ride_id: str, from_: float = Query(0.0, alias="from", ge=0), to: float | None = None,
                    cols: str | None = None):
        ride_or_404(ride_id)
        with RideFile(rides / f"{ride_id}.sqlite") as rf:
            try:
                s = rf.samples(from_, to, [c for c in cols.split(",") if c] if cols else None)
            except ValueError as e:
                raise HTTPException(422, str(e)) from e
        return {"hz": s["hz"], "columns": s["columns"], "t": s["t"].tolist(), "data": _nan_to_none(s["data"]),
                "hold": s["hold"]}

    @app.get("/api/rides/{ride_id}/file", dependencies=[Depends(auth)])
    def get_file(ride_id: str):
        ride_or_404(ride_id)
        return FileResponse(rides / f"{ride_id}.sqlite", media_type="application/vnd.sqlite3",
                            filename=f"{ride_id}.sqlite")

    return app


def __getattr__(name: str):
    # uvicorn server.app:app 이 처음 app을 찾을 때 만든다 (import만으로는 MERMAID_API_KEY가 필요 없게)
    if name == "app":
        a = create_app()
        globals()["app"] = a
        return a
    raise AttributeError(name)
