"""개인 보정 B (docs/DATA_LOGGING.md §12): C++ 식과 Python 식이 같은지, 가짜 주행 파일로 a, b를 다시 찾는지

가짜 주행 파일은 P7과 같은 스키마(controller/common/ride_log.cpp의 kSchema를 그대로 읽음)로 만든다.
사람마다 정해 둔 (a*, b*)로 "진짜" 확률 σ(a*·logit(p) + b*)를 만들고, 그 확률로 MISC 라벨을 뽑는다.
"""
import json
import pathlib
import re
import sqlite3
import subprocess
import sys

import numpy as np
import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "ml" / "personal"))
sys.path.insert(0, str(REPO / "ml"))
import fit_calib as FC  # noqa: E402

SCHEMA = re.search(r'kSchema = R"SQL\((.*?)\)SQL"', (REPO / "controller" / "common" / "ride_log.cpp").read_text(),
                   re.S).group(1)
CFG = FC.load_config()
OFFSETS = np.array([-1.5, -0.5, 0.3, 1.0, 1.8])  # p_k = σ(z − c_k): k가 클수록 작음


# ---------------- C++과 같은 식 ----------------
@pytest.fixture(scope="module")
def tool():
    b = REPO / "build"
    subprocess.run(["cmake", "-S", str(REPO / "controller"), "-B", str(b)], check=True, capture_output=True)
    subprocess.run(["cmake", "--build", str(b), "--target", "calib_apply"], check=True, capture_output=True)
    return b / "calib_apply"


def run_tool(tool, rows):
    text = "\n".join(" ".join(f"{v:.17g}" for v in r) for r in rows) + "\n"
    out = subprocess.run([str(tool)], input=text, capture_output=True, text=True, check=True).stdout
    return np.array([[float(x) for x in line.split()] for line in out.splitlines()])


def test_cpp_matches_python(tool):
    rng = np.random.default_rng(3)
    rows = []
    for _ in range(500):
        a, b = rng.uniform(0.5, 2.0), rng.uniform(-4, 4)
        p = np.sort(rng.uniform(0, 1, 5))[::-1]
        rows.append([a, b, *p])
    rows.append([1.3, 0.7, 1.0, 1.0, 0.5, 0.0, 0.0])  # 끝값 (logit 자르기)
    got = run_tool(tool, rows)
    want = np.array([FC.apply_calib(r[2:], r[0], r[1]) for r in rows])
    assert np.max(np.abs(got - want)) < 1e-12
    assert np.all(np.diff(got, axis=1) <= 0), "보정 뒤에도 p2 >= ... >= p6"


def test_identity_and_invalid_are_bit_identical(tool):
    p = [0.912345678901234, 0.7, 0.5, 0.3, 1e-9]
    got = run_tool(tool, [[1.0, 0.0, *p], [9.0, 0.0, *p], [1.0, 7.0, *p]])
    for row in got:
        assert row.tolist() == p


# ---------------- 가짜 주행 파일 ----------------
def make_ride(path, ride_id, phones, rng, minutes=40, extra=None):
    """phones: {phone: (a*, b*)}. 10초 주기 infer, 300초마다 MISC 라벨 하나"""
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    meta = {"ride_id": ride_id, "schema_version": "1", "closed_reason": "ride_end", "columns": "[]"}
    con.executemany("INSERT INTO meta VALUES (?, ?)", meta.items())
    n = minutes * 6
    for ph, (a, b) in phones.items():
        z = np.cumsum(rng.normal(0, 0.15, n)) + rng.uniform(-2.0, 1.0)
        P = 1 / (1 + np.exp(-(z[:, None] - OFFSETS)))
        for i in range(n):
            t = 10.0 * (i + 1)
            con.execute("INSERT INTO infer (seq, t, mono_ns, phone, x, p2, p3, p4, p5, p6) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (i, t, int(t * 1e9), ph, b"\0" * 8, *P[i]))
        for j in range(30, n, 30):  # 300초마다: 그 앞 300초 가운데쯤의 진짜 확률로 MISC를 뽑는다
            true = FC.apply_calib(P[j - 15], a, b)
            u = rng.uniform()
            misc = max([k for k, q in zip(FC.K, true) if u < q], default=int(rng.integers(0, 2)))
            t = 10.0 * (j + 1)
            con.execute("INSERT INTO label VALUES (?, ?, ?, ?)", (int(t * 1e9), t, ph, misc))
    if extra:
        extra(con)
    con.commit()
    con.close()
    return path


PEOPLE = {
    "sensitive": (1.0, 1.0),   # 모델보다 더 민감
    "matching": (1.0, 0.0),    # 모델 그대로
    "steep": (1.6, 0.0),       # 모델보다 확신이 강함
    "tolerant": (1.0, -2.0),   # 훨씬 덜 민감 → 안전 한계에 걸림
}


@pytest.fixture(scope="module")
def fitted(tmp_path_factory):
    d = tmp_path_factory.mktemp("rides")
    rng = np.random.default_rng(7)
    paths = []
    for r in range(12):  # 주행 12개 = 사람마다 라벨 약 84개 (6개면 뽑기 운으로 '모델 그대로'인 사람도 0.1 넘게 어긋남)
        paths.append(make_ride(d / f"car-00001-{r:03d}.sqlite", f"car-00001-{r:03d}", PEOPLE, rng))
    paths.append(make_ride(d / "car-00002-000.sqlite", "car-00002-000", {"once": (1.0, 1.0)}, rng))
    res, sa, sb = FC.fit_paths(paths, CFG)
    return res, sa, sb, paths


def test_sensitive_person_is_raised(fitted):
    r = fitted[0]["sensitive"]
    assert r["adopted"] and r["loo_gain"] > 0
    assert 0.5 < r["b"] < 1.5, r


def test_matching_person_stays_near_identity(fitted):
    r = fitted[0]["matching"]
    assert abs(r["b"]) < 0.3 and abs(np.log(r["a"])) < 0.2, r


def test_steep_person_gets_a_above_one(fitted):
    r = fitted[0]["steep"]
    assert r["adopted"] and r["a"] > 1.15, r


def test_safety_limit_near_thresholds(fitted):
    r = fitted[0]["tolerant"]
    th = [t["th"] for t in CFG["tiers"].values()]
    assert r["adopted"] and np.all(th - FC.apply_calib(np.array(th), r["a"], r["b"]) > 0.05), r  # 낮추기는 하되
    for t in CFG["tiers"].values():
        p = np.linspace(t["th"] - 0.05, t["th"] + 0.05, 41)
        drop = p - FC.apply_calib(p, r["a"], r["b"])
        assert np.max(drop) <= 0.10 + 1e-9, (t, np.max(drop))  # 문턱 근처에서 0.10보다 많이 낮추지 않음
    assert FC.A_MIN <= r["a"] <= FC.A_MAX


def test_one_ride_is_not_adopted(fitted):
    r = fitted[0]["once"]
    assert not r["adopted"] and r["status"] == "few_rides" and (r["a"], r["b"]) == (1.0, 0.0)


def test_sigmas_estimated_from_people(fitted):
    _, sa, sb, _ = fitted
    assert FC.SIGMA_A_RANGE[0] <= sa <= FC.SIGMA_A_RANGE[1]
    assert FC.SIGMA_B_RANGE[0] <= sb <= FC.SIGMA_B_RANGE[1]


# ---------------- 끄기·켜기 신호 ----------------
def test_quick_off_and_manual_on_signals(tmp_path):
    rng = np.random.default_rng(1)

    def extra(con):
        con.execute("DELETE FROM label")
        # 자동 ON (display-01, Tier 2) 100초 → phone-a가 112초에 끔, 그 뒤 300초 안 라벨은 MISC 1뿐
        con.execute("INSERT INTO decision VALUES (9, 100.0, 'display-01', 1, 2, 1)")
        con.execute("INSERT INTO ctx VALUES (?, 112.0, 'phone-a', 'OVR', ?)",
                    (112 * 10**9, json.dumps({"device": "display-01", "on": 0})))
        con.execute("INSERT INTO label VALUES (?, 300.0, 'phone-a', 1)", (300 * 10**9,))
        # 늦은 끄기 (자동 ON 뒤 60초): 신호 아님
        con.execute("INSERT INTO decision VALUES (60, 610.0, 'airflow-01', 1, 1, 1)")
        con.execute("INSERT INTO ctx VALUES (?, 670.0, 'phone-a', 'OVR', ?)",
                    (670 * 10**9, json.dumps({"device": "airflow-01", "on": 0})))
        # phone-b가 꺼져 있는 자기 폰을 직접 켬, 그 뒤 라벨 없음 → y4 = 1
        con.execute("INSERT INTO decision VALUES (100, 1010.0, 'phone-b', 0, 3, 0)")
        con.execute("INSERT INTO ctx VALUES (?, 1015.0, 'phone-b', 'OVR', ?)",
                    (1015 * 10**9, json.dumps({"device": "phone-b", "on": 1})))

    p = make_ride(tmp_path / "car-00003-000.sqlite", "car-00003-000",
                  {"phone-a": (1, 0), "phone-b": (1, 0)}, rng, minutes=30, extra=extra)
    obs = {}
    FC.ride_obs(p, CFG, obs)
    a, b = obs["phone-a"], obs["phone-b"]
    assert a.n_ovr == 1 and a.y[-1] == 0 and a.w[-1] == FC.W_OVR
    assert a.n_misc > 0  # 300초 라벨이 MISC 관측도 만든다
    assert b.n_ovr == 1 and b.y == [1.0] and b.n_misc == 0


# ---------------- 버전 폴더와 내려보내기 ----------------
def test_write_run_and_push(fitted, tmp_path, monkeypatch):
    import runs
    monkeypatch.setattr(runs, "ROOT", tmp_path / "runs")
    res, sa, sb, paths = fitted
    run = FC.write_run(res, sa, sb, paths, note="test")
    meta = json.loads((run / "run.json").read_text())
    assert meta["kind"] == "calib" and len(meta["params"]["rides"]) == len(paths)
    c = json.loads((run / "calib.json").read_text())
    assert set(c["phones"]) == set(PEOPLE) | {"once"}
    once = json.loads((run / "calib" / "once.json").read_text())
    assert (once["a"], once["b"], once["adopted"]) == (1.0, 0.0, False)  # 예전 값을 지우도록 1, 0도 보낸다

    sys.path.insert(0, str(REPO / "tools"))
    import collect_logs
    state = tmp_path / "state"
    (state / "calib").mkdir(parents=True)
    (state / "calib" / "once.json").write_text('{"a": 1.5, "b": 0.5}')
    assert collect_logs.main(["--push-calib", str(run), "--state", str(state), "--local", str(tmp_path)]) == 0
    for ph in c["phones"]:
        got = json.loads((state / "calib" / f"{ph}.json").read_text())
        assert (got["a"], got["b"]) == (c["phones"][ph]["a"], c["phones"][ph]["b"])
    assert not list((state / "calib").glob("*.tmp"))
