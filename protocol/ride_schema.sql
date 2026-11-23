-- P7 주행 파일(<ride_id>.sqlite)의 표 (docs/controller/DATA_LOGGING.md §6-5).
-- 차의 P7(controller/common/ride_log.cpp)이 이 표로 파일을 만들고, 로그 서버(server/ridefile.py)와
-- 학습 내보내기·시험이 같은 정의를 읽는다. 표나 열을 바꾸면 아래 schema_version을 올린다.
-- C++은 python3 protocol/gen_cpp.py가 controller/common/protocol_gen.hpp에 이 파일을 그대로 넣어 쓴다 (check.py가 최신인지 확인).
-- schema_version: 2
-- 2 (2026-11, #18): gps 표 추가 (M09 GPS: 폰 속도·정확도·방향·고도, 위도·경도 없음). 버전 1 파일은 gps 표만 없다
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE samples (
  seq0 INTEGER PRIMARY KEY, n INTEGER NOT NULL, mono_ns0 INTEGER NOT NULL,
  data BLOB NOT NULL, hold BLOB);
CREATE TABLE infer (
  seq INTEGER NOT NULL, t REAL NOT NULL, mono_ns INTEGER NOT NULL, phone TEXT NOT NULL,
  x BLOB NOT NULL, p2 REAL, p3 REAL, p4 REAL, p5 REAL, p6 REAL,
  cal_a REAL NOT NULL DEFAULT 1, cal_b REAL NOT NULL DEFAULT 0,
  PRIMARY KEY (seq, phone));
CREATE TABLE decision (
  seq INTEGER NOT NULL, t REAL NOT NULL, device TEXT NOT NULL,
  on_ INTEGER NOT NULL, tier INTEGER NOT NULL, changed INTEGER NOT NULL,
  PRIMARY KEY (seq, device));
CREATE TABLE ctx (mono_ns INTEGER NOT NULL, t REAL, sender TEXT NOT NULL, kind TEXT NOT NULL, value TEXT);
CREATE TABLE label (mono_ns INTEGER NOT NULL, t REAL, phone TEXT NOT NULL,
  misc INTEGER NOT NULL CHECK (misc BETWEEN 0 AND 10));
CREATE TABLE health (mono_ns INTEGER NOT NULL, t REAL, key TEXT NOT NULL, value REAL NOT NULL);
CREATE TABLE gps (mono_ns INTEGER NOT NULL, t REAL, phone TEXT NOT NULL,
  spd REAL NOT NULL, brg REAL, acc REAL NOT NULL, alt REAL);
CREATE INDEX label_phone ON label(phone, t);
