"""주행 파일 저장소. STORAGE_URL 하나로 로컬 폴더와 S3(또는 S3 호환 MinIO)를 바꾼다.

  file:///abs/path   폴더 (기본: MERMAID_DATA). 키 rides/<id>.sqlite → <path>/rides/<id>.sqlite, 읽기 전용
  s3://bucket/prefix  S3. 키 → s3://bucket/prefix/rides/<id>.sqlite. 자격 증명과 주소는 boto3 기본 방식
                      (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_REGION, MinIO는 AWS_ENDPOINT_URL)

주행 파일은 한 번 쓰면 바뀌지 않는다. 그래서
  - put_new는 같은 키가 없을 때만 쓴다 (폴더: 하드 링크, S3: If-None-Match 조건부 쓰기). 서버가 여러 개여도 덮어쓰지 않는다
  - S3 파일은 처음 읽을 때 로컬 캐시(MERMAID_CACHE, 기본 MERMAID_DATA/cache)에 받아 두고 계속 쓴다. 캐시는 언제 지워도 된다

명령:
  python -m server.storage pull --dest DIR   저장소의 주행 파일을 DIR에 받는다 (이미 있는 파일은 건너뜀). ml 도구의 --root로 쓴다
  python -m server.storage init              s3:// 버킷이 없으면 만들고 버전 관리를 켠다 (docker compose의 s3-init)
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
import uuid
from pathlib import Path
from urllib.parse import unquote, urlparse

CHUNK = 1 << 20


def ride_key(ride_id: str) -> str:
    return f"rides/{ride_id}.sqlite"


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


class Storage:
    url: str

    def put_new(self, key: str, src: Path, sha256: str) -> bool:
        """key가 없으면 src 내용을 쓰고 True, 이미 있으면 아무것도 하지 않고 False. src는 그대로 둔다"""
        raise NotImplementedError

    def sha256(self, key: str) -> str | None:
        """저장된 파일의 sha256 (없으면 None)"""
        raise NotImplementedError

    def local_path(self, key: str) -> Path:
        """sqlite3로 열 수 있는 로컬 경로. 없으면 FileNotFoundError"""
        raise NotImplementedError

    def delete(self, key: str) -> None:
        raise NotImplementedError

    def keys(self, prefix: str = "rides/") -> list[str]:
        raise NotImplementedError

    def check(self) -> None:
        """시작할 때 저장소에 닿는지 확인 (실패하면 예외)"""


class FileStorage(Storage):
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.url = self.root.as_uri()
        (self.root / "rides").mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if self.root not in p.parents:
            raise ValueError(f"bad key {key!r}")
        return p

    def put_new(self, key: str, src: Path, sha256: str) -> bool:
        dest = self._path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.parent / f".{uuid.uuid4().hex}.tmp"
        shutil.copyfile(src, tmp)
        try:
            with open(tmp, "rb") as f:
                os.fsync(f.fileno())
            os.chmod(tmp, 0o444)
            try:
                os.link(tmp, dest)  # dest가 이미 있으면 실패: 덮어쓰지 않는다
            except FileExistsError:
                return False
            return True
        finally:
            tmp.unlink(missing_ok=True)

    def sha256(self, key: str) -> str | None:
        p = self._path(key)
        return file_sha256(p) if p.is_file() else None

    def local_path(self, key: str) -> Path:
        p = self._path(key)
        if not p.is_file():
            raise FileNotFoundError(key)
        return p

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def keys(self, prefix: str = "rides/") -> list[str]:
        base = self._path(prefix.rstrip("/"))
        if not base.is_dir():
            return []
        return sorted(f"{prefix.rstrip('/')}/{p.name}" for p in base.iterdir() if p.is_file() and not p.name.startswith("."))


class S3Storage(Storage):
    def __init__(self, bucket: str, prefix: str, cache_dir: str | Path, client=None):
        import boto3  # 폴더 저장소만 쓸 때는 boto3가 없어도 된다

        self.bucket, self.prefix = bucket, prefix.strip("/")
        self.url = f"s3://{bucket}/{self.prefix}" if self.prefix else f"s3://{bucket}"
        self.s3 = client or boto3.client("s3")
        self.cache = Path(cache_dir)
        self.cache.mkdir(parents=True, exist_ok=True)

    def _k(self, key: str) -> str:
        return f"{self.prefix}/{key}" if self.prefix else key

    @staticmethod
    def _code(e) -> str:
        return str(getattr(e, "response", {}).get("Error", {}).get("Code", ""))

    def check(self) -> None:
        self.s3.head_bucket(Bucket=self.bucket)

    def init(self) -> None:
        """버킷이 없으면 만들고 버전 관리를 켠다 (실수로 지우거나 덮어써도 이전 버전이 남는다)"""
        from botocore.exceptions import ClientError

        try:
            self.s3.head_bucket(Bucket=self.bucket)
        except ClientError as e:
            if self._code(e) not in ("404", "NoSuchBucket", "NotFound"):
                raise
            self.s3.create_bucket(Bucket=self.bucket)
        self.s3.put_bucket_versioning(Bucket=self.bucket, VersioningConfiguration={"Status": "Enabled"})

    def put_new(self, key: str, src: Path, sha256: str) -> bool:
        from botocore.exceptions import ClientError

        with open(src, "rb") as f:
            try:
                self.s3.put_object(Bucket=self.bucket, Key=self._k(key), Body=f, IfNoneMatch="*",
                                   Metadata={"sha256": sha256}, ContentType="application/vnd.sqlite3")
            except ClientError as e:
                if self._code(e) in ("PreconditionFailed", "412", "ConditionalRequestConflict", "409"):
                    return False
                raise
        return True

    def sha256(self, key: str) -> str | None:
        from botocore.exceptions import ClientError

        try:
            h = self.s3.head_object(Bucket=self.bucket, Key=self._k(key))
        except ClientError as e:
            if self._code(e) in ("404", "NoSuchKey", "NotFound"):
                return None
            raise
        sha = h.get("Metadata", {}).get("sha256")
        return sha or file_sha256(self.local_path(key))

    def local_path(self, key: str) -> Path:
        from botocore.exceptions import ClientError

        p = self.cache / key
        if p.is_file():
            return p
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.parent / f".{uuid.uuid4().hex}.tmp"
        try:
            self.s3.download_file(self.bucket, self._k(key), str(tmp))
            os.replace(tmp, p)
        except ClientError as e:
            if self._code(e) in ("404", "NoSuchKey", "NotFound"):
                raise FileNotFoundError(key) from e
            raise
        finally:
            tmp.unlink(missing_ok=True)
        return p

    def delete(self, key: str) -> None:
        self.s3.delete_object(Bucket=self.bucket, Key=self._k(key))
        (self.cache / key).unlink(missing_ok=True)

    def keys(self, prefix: str = "rides/") -> list[str]:
        out, cut = [], len(self._k(""))
        for page in self.s3.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=self._k(prefix)):
            out += [o["Key"][cut:] for o in page.get("Contents", [])]
        return sorted(out)


def open_storage(url: str | None, data_dir: str | Path, cache_dir: str | Path | None = None) -> Storage:
    """url이 비면 data_dir 폴더. 캐시 기본값은 data_dir/cache"""
    if not url:
        return FileStorage(data_dir)
    u = urlparse(url)
    if u.scheme == "file":
        return FileStorage(unquote(u.path))
    if u.scheme == "s3":
        if not u.netloc:
            raise ValueError(f"STORAGE_URL needs a bucket: {url!r}")
        return S3Storage(u.netloc, u.path, cache_dir or Path(data_dir) / "cache")
    raise ValueError(f"STORAGE_URL must be file:// or s3://, got {url!r}")


def from_env() -> Storage:
    data = os.environ.get("MERMAID_DATA") or Path(__file__).resolve().parent / "data"
    return open_storage(os.environ.get("STORAGE_URL"), data, os.environ.get("MERMAID_CACHE"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m server.storage", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pull", help="copy ride files to a local folder")
    p.add_argument("--dest", required=True, type=Path)
    sub.add_parser("init", help="create the s3 bucket and turn on versioning")
    a = ap.parse_args(argv)
    st = from_env()
    if a.cmd == "init":
        if isinstance(st, S3Storage):
            st.init()
        print(f"storage ready: {st.url}", file=sys.stderr)
        return 0
    a.dest.mkdir(parents=True, exist_ok=True)
    got = 0
    for key in st.keys("rides/"):
        out = a.dest / Path(key).name
        if out.exists():
            continue
        tmp = out.with_name(f".{out.name}.tmp")
        shutil.copyfile(st.local_path(key), tmp)
        os.replace(tmp, out)
        got += 1
    print(f"{got} new ride files in {a.dest} (from {st.url})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
