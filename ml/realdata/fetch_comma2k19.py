"""comma2k19 원본 청크에서 신호 파일만 받기 (영상·raw_log 제외, 청크 하나 ~9 GB 대신 ~140 MB)

    python3 -I ml/realdata/fetch_comma2k19.py --chunk 1 --out ml/data/raw/comma2k19/chunk1

HF commaai/comma2k19의 raw_data/Chunk_N.zip을 HTTP range 요청으로 읽어, 연속 세그먼트가
--min-run개 이상인 구간의 IMU·CAN·global_pose 파일만 푼다. CRC를 확인한다. 이미 받은 파일은 건너뛴다.
zip 안 경로의 '|'는 '_'로 바꾼다. 받은 파일은 numpy .npy (np.load(..., allow_pickle=False)).
"""
import argparse
import collections
import concurrent.futures as cf
import io
import pathlib
import ssl
import struct
import urllib.request
import zipfile
import zlib

URL = "https://huggingface.co/datasets/commaai/comma2k19/resolve/main/raw_data/Chunk_{}.zip"
KEEP = ["processed_log/IMU/accelerometer/", "processed_log/IMU/gyro/", "processed_log/CAN/speed/",
        "processed_log/CAN/steering_angle/", "processed_log/CAN/wheel_speed/",
        "global_pose/frame_times", "global_pose/frame_velocities", "global_pose/frame_orientations"]

try:
    import certifi
    CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    CTX = ssl.create_default_context()


def http(url, rng=None, method="GET"):
    req = urllib.request.Request(url, method=method, headers={"Range": f"bytes={rng[0]}-{rng[1]}"} if rng else {})
    return urllib.request.urlopen(req, context=CTX)


class RangeFile(io.RawIOBase):
    """zipfile이 목차만 읽을 수 있게 하는 읽기 전용 원격 파일"""
    def __init__(self, url):
        with http(url, method="HEAD") as r:
            self.url, self.size = r.geturl(), int(r.headers["Content-Length"])
        self.pos = 0

    def seekable(self):
        return True

    def readable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, off, whence=0):
        self.pos = [off, self.pos + off, self.size + off][whence]
        return self.pos

    def readinto(self, b):
        if self.pos >= self.size:
            return 0
        with http(self.url, (self.pos, min(self.pos + len(b), self.size) - 1)) as r:
            d = r.read()
        b[:len(d)] = d
        self.pos += len(d)
        return len(d)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--chunk", type=int, default=1)
    ap.add_argument("--out", required=True)
    ap.add_argument("--min-run", type=int, default=5)
    a = ap.parse_args()
    out = pathlib.Path(a.out)
    rf = RangeFile(URL.format(a.chunk))
    infos = zipfile.ZipFile(io.BufferedReader(rf, buffer_size=1 << 20)).infolist()
    seg = collections.defaultdict(set)
    for i in infos:
        p = i.filename.split("/")
        if len(p) >= 4 and p[2].isdigit():
            seg[p[1]].add(int(p[2]))
    want = set()
    for r, s in seg.items():
        s = sorted(s)
        run = [s[0]]
        for x in s[1:] + [None]:
            if x is not None and x == run[-1] + 1:
                run.append(x)
                continue
            if len(run) >= a.min_run:
                want |= {(r, k) for k in run}
            if x is not None:
                run = [x]
    jobs = [i for i in infos if not i.is_dir() and any(k in i.filename for k in KEEP)
            and (i.filename.split("/")[1], int(i.filename.split("/")[2])) in want]

    def get(i):
        dst = out / i.filename.replace("|", "_")
        if dst.exists():
            return 0
        start = i.header_offset
        with http(rf.url, (start, start + 30 + len(i.filename.encode()) + 1024 + i.compress_size)) as r:
            d = r.read()
        n, m = struct.unpack("<HH", d[26:30])
        body = d[30 + n + m: 30 + n + m + i.compress_size]
        data = zlib.decompress(body, -15) if i.compress_type == zipfile.ZIP_DEFLATED else body
        assert len(data) == i.file_size and zlib.crc32(data) == i.CRC, i.filename
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
        return len(data)

    with cf.ThreadPoolExecutor(16) as ex:
        total = sum(ex.map(get, jobs))
    print(f"chunk {a.chunk}: {len(want)} segments, {len(jobs)} files, {total / 1e6:.1f} MB new -> {out}")


if __name__ == "__main__":
    main()
