#!/usr/bin/env python3
"""차(P7)의 닫힌 주행 파일을 로그 서버로 올린다 (docs/DATA_LOGGING.md §8). 몇 번을 다시 돌려도 결과가 같다.

  python3 tools/collect_logs.py [--host qnxuser@192.168.2.2] [--remote-dir mermaid/logs/rides]
                                [--local DIR] [--server http://localhost:8080] [--key KEY] [--dry-run]

1. 주행 폴더에서 <ride_id>.sqlite 중 <ride_id>.uploaded가 없는 것 (.open, .broken은 건드리지 않는다)
2. 하나씩 받아(scp) sha256 계산 → POST /api/rides (파일 + sha256)
3. 201(새로 받음)이나 200(이미 있음)이면 차에 <ride_id>.uploaded를 만든다. P7은 공간이 모자랄 때 이것만 지운다
4. 409(같은 id, 다른 내용)나 422(검증 실패)는 표시만 하고 다음 파일로. 하나라도 실패하면 종료 코드 1

--local DIR: SSH 대신 이 폴더에서 (보드 폴더를 마운트했거나 시험할 때). API 키는 --key 또는 MERMAID_API_KEY.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx

RIDE_FILE = re.compile(r"^([A-Za-z0-9_.-]{1,64}-\d{5}-\d{3,})\.sqlite$")
SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def pending(names: list[str]) -> list[str]:
    """올릴 ride_id 목록 (이름 순 = 세션·주행 순)"""
    have = set(names)
    out = []
    for n in names:
        m = RIDE_FILE.match(n)
        if m and f"{m.group(1)}.uploaded" not in have:
            out.append(m.group(1))
    return sorted(out)


class LocalSource:
    def __init__(self, root: str):
        self.root = Path(root)

    def names(self) -> list[str]:
        return [p.name for p in self.root.iterdir()]

    def fetch(self, ride_id: str, tmp: Path) -> Path:
        return self.root / f"{ride_id}.sqlite"

    def mark(self, ride_id: str) -> None:
        (self.root / f"{ride_id}.uploaded").touch()

    def __str__(self):
        return str(self.root)


class SshSource:
    def __init__(self, host: str, remote_dir: str):
        self.host, self.dir = host, remote_dir.rstrip("/")

    def _ssh(self, cmd: str) -> str:
        r = subprocess.run(["ssh", *SSH_OPTS, self.host, cmd], capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            raise RuntimeError(f"ssh {self.host} {cmd!r}: {r.stderr.strip() or r.returncode}")
        return r.stdout

    def names(self) -> list[str]:
        return [line for line in self._ssh(f"ls -1 {shlex.quote(self.dir)}").splitlines() if line]

    def fetch(self, ride_id: str, tmp: Path) -> Path:
        dest = tmp / f"{ride_id}.sqlite"
        r = subprocess.run(["scp", "-q", *SSH_OPTS, f"{self.host}:{self.dir}/{ride_id}.sqlite", str(dest)],
                           capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            raise RuntimeError(f"scp {ride_id}: {r.stderr.strip() or r.returncode}")
        return dest

    def mark(self, ride_id: str) -> None:
        self._ssh(f"touch {shlex.quote(f'{self.dir}/{ride_id}.uploaded')}")

    def __str__(self):
        return f"{self.host}:{self.dir}"


def collect(source, client: httpx.Client, key: str, dry_run: bool = False, log=print) -> dict:
    """source의 올릴 파일을 모두 올린다. 반환: {uploaded, exists, failed: [(id, 이유)]}"""
    result = {"uploaded": [], "exists": [], "failed": []}
    todo = pending(source.names())
    if dry_run:
        for rid in todo:
            log(f"WOULD UPLOAD {rid}")
        result["pending"] = todo
        return result
    with tempfile.TemporaryDirectory(prefix="mmcollect") as td:
        tmp = Path(td)
        for rid in todo:
            try:
                path = source.fetch(rid, tmp)
                digest = sha256_of(path)
                with open(path, "rb") as f:
                    r = client.post("/api/rides", headers={"X-API-Key": key},
                                    files={"file": (f"{rid}.sqlite", f, "application/vnd.sqlite3")},
                                    data={"sha256": digest}, timeout=600)
                if r.status_code in (200, 201):
                    source.mark(rid)
                    kind = "uploaded" if r.status_code == 201 else "exists"
                    result[kind].append(rid)
                    log(f"{kind.upper():9s}{rid}")
                else:
                    try:
                        detail = r.json().get("detail", r.text)
                    except ValueError:
                        detail = r.text
                    result["failed"].append((rid, f"{r.status_code} {detail}"))
                    log(f"FAILED   {rid}: {r.status_code} {detail}")
            except (OSError, RuntimeError, httpx.HTTPError) as e:
                result["failed"].append((rid, str(e)))
                log(f"FAILED   {rid}: {e}")
            finally:
                p = tmp / f"{rid}.sqlite"
                if p.exists():
                    p.unlink()
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--host", default="qnxuser@192.168.2.2", help="차(Pi) SSH 주소")
    ap.add_argument("--remote-dir", default="mermaid/logs/rides", help="차의 주행 폴더 (홈 기준)")
    ap.add_argument("--local", help="SSH 대신 이 폴더에서")
    ap.add_argument("--server", default="http://localhost:8080")
    ap.add_argument("--key", default=os.environ.get("MERMAID_API_KEY"))
    ap.add_argument("--dry-run", action="store_true", help="올릴 목록만 보여 준다")
    a = ap.parse_args(argv)
    if not a.key and not a.dry_run:
        print("API key: --key 또는 MERMAID_API_KEY", file=sys.stderr)
        return 2
    if not a.local and not shutil.which("ssh"):
        print("ssh not found", file=sys.stderr)
        return 2
    source = LocalSource(a.local) if a.local else SshSource(a.host, a.remote_dir)
    try:
        with httpx.Client(base_url=a.server) as client:
            res = collect(source, client, a.key or "", a.dry_run)
    except (OSError, RuntimeError) as e:
        print(f"cannot list {source}: {e}", file=sys.stderr)
        return 1
    print(json.dumps({"source": str(source), "uploaded": len(res["uploaded"]), "exists": len(res["exists"]),
                      "failed": len(res["failed"])}))
    return 1 if res["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
