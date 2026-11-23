"""docs/architecture/ARCHITECTURE.html 생성기 (SVG 좌표를 계산해 한 파일로 씀)

    python3 docs/architecture/make_architecture.py            # docs/architecture/ARCHITECTURE.html을 다시 씀

숫자는 손으로 옮겨 적은 값이다 (출처: docs/architecture/SPEC.md, docs/ml/REALDATA.md, ml/out/runs/v00N/ 리포트).
모델이나 데이터셋 버전이 바뀌면 여기 숫자와 버전 이름을 고친 뒤 다시 돌린다.
"""
import html
import pathlib
import sys

OUT = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path(__file__).resolve().parent / "ARCHITECTURE.html"
E = html.escape


def box(x, y, w, h, cls, title=None, lines=(), title_cls="t", line_cls="s", dy0=20, step=15, anchor="start", pad=12, rx=10):
    s = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" class="{cls}"/>']
    tx = x + pad if anchor == "start" else x + w / 2
    yy = y + dy0
    if title:
        s.append(f'<text x="{tx}" y="{yy}" class="{title_cls}" text-anchor="{anchor}">{E(title)}</text>')
        yy += step + 2
    for ln in lines:
        c = line_cls
        if isinstance(ln, tuple):
            ln, c = ln
        s.append(f'<text x="{tx}" y="{yy}" class="{c}" text-anchor="{anchor}">{E(ln)}</text>')
        yy += step
    return "\n".join(s)


def pill(x, y, w, text, cls="file", h=26):
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" class="{cls}"/>'
            f'<text x="{x + 8}" y="{y + h / 2 + 4}" class="m">{E(text)}</text>')


def arrow(d, cls="a-pc", marker="pc"):
    return f'<path d="{d}" class="{cls}" marker-end="url(#ah-{marker})"/>'


def label(x, y, text, cls="lb", anchor="middle"):
    return f'<text x="{x}" y="{y}" class="{cls}" text-anchor="{anchor}">{E(text)}</text>'


DEFS = """<defs>
<marker id="ah-pc" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="mk-pc"/></marker>
<marker id="ah-pi" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="mk-pi"/></marker>
<marker id="ah-cfg" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="mk-cfg"/></marker>
<marker id="ah-todo" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="mk-todo"/></marker>
</defs>"""


# ------------------------------------------------------------------ 1. 전체 그림
def overview():
    s = [DEFS]
    # 개발 PC
    s.append(box(20, 40, 330, 340, "lane-pc", "개발 PC (Mac)", [("Python 3.12 + C++ · 오프라인, 한 번", "s")], title_cls="lt", dy0=30, step=17, pad=20))
    items = [("TU Delft 적합 (S1)", "K0 = 51.4"), ("시뮬레이터 (S3) → v017", "탑승 1,920회 · 100 Hz"),
             ("피처 featurize (S2, C++)", "10초마다 46개"), ("학습 (S4) → v018", "LightGBM ×5 · 문턱"),
             ("실차 · 사람 MISC 검증", "BATON · comma · TU Delft")]
    for i, (a, b) in enumerate(items):
        y = 98 + i * 50
        s.append(f'<rect x="40" y="{y}" width="290" height="38" rx="8" class="proc-pc"/>')
        s.append(f'<text x="52" y="{y + 24}" class="t2">{E(a)}</text>')
        s.append(f'<text x="318" y="{y + 24}" class="s" text-anchor="end">{E(b)}</text>')
        if i < 4:
            s.append(arrow(f"M185,{y + 38} L185,{y + 49}", "a-pc", "pc"))
    s.append(f'<rect x="40" y="350" width="290" height="22" rx="6" class="store"/>')
    s.append(f'<text x="52" y="365" class="s">결과: ml/out/runs/vNNN (덮어쓰지 않음) · runs.py use</text>')
    # Pi
    s.append(box(420, 40, 450, 340, "lane-pi", "Raspberry Pi 4 · QNX 8", [("C++ 프로세스 P1~P6 구현 (P5·P6 PR #4)", "s")], title_cls="lt", dy0=30, step=17, pad=20))
    procs = [("P1", "수집 100 Hz"), ("P2", "피처 10초"), ("P3", "추론"), ("P4", "정책")]
    for i, (a, b) in enumerate(procs):
        x = 440 + i * 105
        s.append(box(x, 110, 92, 70, "proc-pi", a, [b], title_cls="big", anchor="middle", dy0=32, step=18, rx=8))
        if i < 3:
            s.append(arrow(f"M{x + 92},145 L{x + 104},145", "a-pi", "pi"))
    # P5, P6
    s.append(box(650, 220, 92, 70, "proc-pi", "P5", ["기기 관리"], title_cls="big", line_cls="s", anchor="middle", dy0=32, step=18, rx=8))
    s.append(box(755, 220, 92, 70, "proc-pi", "P6", ["통신 UART"], title_cls="big", line_cls="s", anchor="middle", dy0=32, step=18, rx=8))
    s.append(arrow("M801,180 L801,200 L696,200 L696,219", "a-pi", "pi"))
    s.append(arrow("M742,255 L754,255", "a-pi", "pi"))
    s.append(box(495, 220, 145, 70, "store", "P4가 쓰는 것", ["state/ 허용도·프로필", "cycles.csv · CMD 로그"], title_cls="t2", step=16, rx=8))
    s.append(box(495, 305, 352, 60, "store", "Pi에 내려받은 파일 (~/mermaid/repo)", ["runs/CURRENT · runs/v018/models · controller/config"], title_cls="t2", step=17, rx=8))
    # 차량 입력
    s.append(box(420, 400, 450, 58, "proc-todo", "차량 입력: MPU-6050 IMU (I²C) · CAN 신호", ["실시간 읽기 미구현. 지금은 P1이 trip.bin을 100 Hz로 재생"], title_cls="t2", step=17, rx=10))
    s.append(arrow("M462,400 L462,181", "a-todo", "todo"))
    # 기기
    s.append(box(940, 40, 240, 340, "lane-todo", "브리지 · 기기", [("구현 (PR #4·#5) · Pi와 실연결 시험 전", "s")], title_cls="lt", dy0=30, step=17, pad=20))
    s.append(box(960, 110, 200, 62, "proc-todo", "ESP32-S3 브리지", ["UART ↔ BLE GATT", "동시 연결 최대 9"], title_cls="t2", step=16, rx=8))
    s.append(box(960, 230, 95, 80, "proc-todo", "Android 앱", ["Tier 3 큐", "MSSQ 설문"], title_cls="t2", step=16, rx=8, pad=10))
    s.append(box(1065, 230, 95, 80, "proc-todo", "HUD (웹)", ["Tier 1 송풍", "Tier 2 화면"], title_cls="t2", step=16, rx=8, pad=10))
    s.append(arrow("M1007,172 L1007,229", "a-todo", "todo"))
    s.append(arrow("M1112,172 L1112,229", "a-todo", "todo"))
    s.append(label(1060, 205, "BLE", "lb"))
    s.append(arrow("M847,255 L895,255 L895,141 L959,141", "a-todo", "todo"))
    s.append(label(871, 248, "UART", "lb"))
    s.append(label(871, 272, "115,200", "lb2"))
    # PC <-> Pi
    s.append(arrow("M330,182 L419,182", "a-cfg", "cfg"))
    s.append(label(375, 174, "ride_*.bin"))
    s.append(arrow("M330,286 L419,286", "a-cfg", "cfg"))
    s.append(label(375, 270, "모델 5개"))
    s.append(label(375, 281, "문턱 · 정책"))
    s.append(arrow("M419,336 L331,336", "a-cfg", "cfg"))
    s.append(label(375, 328, "Pi 결과"))
    s.append(label(375, 360, "tar + ssh", "lb"))
    return f'<svg viewBox="0 0 1200 470" role="img" aria-label="전체 구성">{"".join(s)}</svg>'


# ------------------------------------------------------------------ 2. 개발 PC 파이프라인
COLS = [
    ("S1 Oman 적합", ["ml/data/raw/ATDOMS.mat", "ml/oman.py (Oman 식)"],
     ("oman_tudelft.py", ["사람마다 K 하나를 적합", "0.63 Hz 필터 있음/없음"]),
     ["K0 51.4 → constants.json", "η 0.65 → sim_config.json", "out/oman_tudelft.json", "out/K_lpf.npy, K_raw.npy"]),
    ("S3 시뮬레이터", ["sim_config.json (T-23)", "sim_effects.json", "constants.json"],
     ("sim.py", ["경로 · 운전 → IMU · CAN", "승객 → Oman 라벨 MISC"]),
     ["trip 100 Hz × 32열", "MISC(t), score(t)", "v017/trips/ride_000*.bin"]),
    ("S2 피처 (C++)", ["constants.json (Wf·이벤트)", "feature_order.json (v5)"],
     ("featurize", ["FeatureExtractor = P2 코드", "1,000샘플마다 한 행"]),
     ["features.bin [t, 46개]"]),
    ("데이터셋 조립", ["← sim.py 라벨 · 승객", "feature_order.json (v5)"],
     ("build_dataset.py", ["G8 NaN + G10 승객 4개", "y_k: 다음 5분 최대 ≥ k"]),
     ["v017/dataset.npz", "v017/rides.json", "v017/dataset_report.json", "v017/run.json"]),
    ("S4 학습", ["← v017/dataset.npz", "policy.json (재생)"],
     ("train.py", ["5-fold · LightGBM ×5", "문턱: 주행 재생으로"]),
     ["v018/models/model_2~6.txt", "v018/thresholds.json", "v018/train/report.md", "v018/run.json"]),
    ("배포 · 검증", ["runs/CURRENT", "Pi: actual_pi, cycles.csv"],
     ("runs.py use · verify/", ["use v018 → 문턱 복사", "AC-01 · AC-02 · 실차 비교"]),
     ["config/thresholds.json", "v007/parity/ (Pi), v018/verify/", "v018/compare/*.md"]),
]


def offline():
    s = [DEFS]
    W, G, X0 = 185, 20, 20
    for i, (head, ins, (pt, pl), outs) in enumerate(COLS):
        x = X0 + i * (W + G)
        cx = x + W / 2
        s.append(f'<text x="{cx}" y="26" class="t" text-anchor="middle">{E(head)}</text>')
        s.append(label(x, 50, "입력", "cap", "start"))
        y = 58
        for t in ins:
            s.append(pill(x, y, W, t, "cfgpill" if (t.endswith(".json") or "json (" in t) and not t.startswith("←") else "file"))
            y += 32
        s.append(arrow(f"M{cx},{y - 6} L{cx},199", "a-cfg", "cfg"))
        s.append(box(x, 200, W, 92, "proc-pc", pt, pl, title_cls="tm", dy0=24, step=16))
        s.append(arrow(f"M{cx},292 L{cx},321", "a-pc", "pc"))
        s.append(label(x, 316, "출력", "cap", "start"))
        y = 324
        for t in outs:
            s.append(pill(x, y, W, t, "outpill"))
            y += 32
        if i < len(COLS) - 1:
            xm = x + W + G / 2
            s.append(arrow(f"M{x + W},337 L{xm},337 L{xm},246 L{x + W + G - 1},246", "a-pc", "pc"))
    s.append(label(20, 478, "build_dataset.py가 탑승마다 sim.py → trip.bin → featurize를 차례로 부릅니다 (8개 병렬). 앞 8개 탑승의 trip.bin만 새 버전 폴더의 trips/에 남깁니다.", "cap", "start"))
    s.append(label(20, 498, "점선 = 설정 파일을 읽음, 실선 = 데이터가 다음 단계로 감. vNNN은 ml/out/runs/vNNN (실행마다 새로 만들고 덮어쓰지 않음). 배포는 runs.py use, Pi에는 tar + ssh.", "cap", "start"))
    return f'<svg viewBox="0 0 1250 510" role="img" aria-label="개발 PC 파이프라인">{"".join(s)}</svg>'


# ------------------------------------------------------------------ 2b. 검증 · 보정 루프
def validation():
    s = [DEFS]
    for x, t in [(20, "원자료 (ml/data/raw/)"), (270, "변환 · 처리"), (550, "새 버전 폴더 / 설정"), (810, "비교"), (1070, "리포트 · 되먹임")]:
        s.append(label(x, 26, t, "cap", "start"))
    # 실차 두 개
    s.append(box(20, 40, 210, 62, "store", "BATON-Sample", ["운전자 2명 · 34주행 · 19.1 h", "폰 IMU 100 Hz + CAN 속도"], title_cls="t2", step=16, rx=8))
    s.append(box(20, 112, 210, 62, "store", "comma2k19 Chunk 1", ["차 1대 · 19구간 · 2.9 h", "고속도로 66%"], title_cls="t2", step=16, rx=8))
    s.append(box(270, 40, 240, 134, "proc-pc", "build_real.py · convert.py", [
        "장착 방향 추정 (convert.align)", "a_long = d(CAN 속도)/dt, a_lat = v·ω_z",
        "→ trip.bin → featurize (같은 C++)", "승객: 시뮬레이터와 같은 120명 풀", "라벨: sim.label (가상 승객 Oman)", "없는 신호 (페달, EV, 내비)는 NaN"],
        title_cls="tm", step=16, rx=10))
    s.append(arrow("M230,71 L269,71", "a-pc", "pc"))
    s.append(arrow("M230,143 L269,143", "a-pc", "pc"))
    s.append(pill(550, 56, 220, "v019/real_baton.npz  23,348행", "outpill"))
    s.append(pill(550, 128, 220, "v020/real_comma2k19.npz  1,788행", "outpill"))
    s.append(arrow("M510,69 L549,69", "a-pc", "pc"))
    s.append(arrow("M510,141 L549,141", "a-pc", "pc"))
    s.append(box(810, 40, 220, 98, "proc-pc", "compare.py", ["피처 분포 p10 / p50 / p90 · KS", "라벨 비율 (같은 시간 범위)", "CURRENT 모델(v018)을 그대로 적용",
                                                        "시뮬 쪽: 그 모델의 v017 데이터"], title_cls="tm", step=16, rx=10))
    s.append(arrow("M770,69 L790,69 L790,89 L809,89", "a-pc", "pc"))
    s.append(arrow("M770,141 L790,141 L790,100 L809,100", "a-pc", "pc"))
    s.append(box(1070, 40, 160, 98, "outpill", "v018/compare/", ["baton_v019.md", "comma2k19_v020.md", "BATON k4 ROC 0.85", "comma k4 ROC 0.95"], title_cls="tm", step=16, rx=8))
    s.append(arrow("M1030,89 L1069,89", "a-pc", "pc"))
    # 보정 (T-23)
    s.append(pill(550, 214, 220, "sim.npz (작은 시뮬, --out)", "file"))
    s.append(box(810, 190, 220, 80, "proc-pc", "calibrate.py (T-23)", ["피처 15개 KS 평균 (BATON 기준)", "속도대 비율 · 라벨 비율", "comma2k19는 점검용"], title_cls="tm", step=16, rx=10))
    s.append(arrow("M770,227 L809,227", "a-pc", "pc"))
    s.append(arrow("M660,156 L660,178 L840,178 L840,189", "a-pc", "pc"))
    s.append(label(700, 172, "실차 기준", "lb2", "start"))
    s.append(box(1070, 190, 160, 80, "cfgbox", "sim_config.json", ["calibrated 표시 7항목", "KS 0.38 → 0.14", "P(≥4) 0.37 → 0.21"], title_cls="tm", step=16, rx=8))
    s.append(arrow("M1030,230 L1069,230", "a-cfg", "cfg"))
    s.append(arrow("M1150,270 L1150,290 L390,290 L390,272", "a-cfg", "cfg"))
    s.append(label(770, 304, "사람이 값을 고친 뒤 build_dataset.py를 다시 돌림 (S3, 새 버전)", "lb"))
    s.append(box(270, 200, 240, 70, "proc-pc", "build_dataset.py (S3)", ["보정된 설정으로 새 데이터셋", "→ train.py → 새 모델 버전"], title_cls="tm", step=16, rx=10))
    s.append(arrow("M510,227 L549,227", "a-pc", "pc"))
    # TU Delft
    s.append(box(20, 330, 210, 120, "store", "ATDOMS.mat (TU Delft)", ["17명 × 4조건", "앞뒤 0.3 Hz 사인", "1 / 1.5 / 2 / 2.5 m/s²", "MISC 30초마다 · MSSQ", "유일한 실제 사람 MISC"], title_cls="t2", step=16, rx=8))
    s.append(box(270, 322, 240, 56, "proc-pc", "oman_tudelft.py (S1)", ["사람마다 K 하나 적합 → 중앙값"], title_cls="tm", step=16, rx=10))
    s.append(box(270, 392, 240, 70, "proc-pc", "tudelft_replay.py", ["같은 가속도를 내는 가상 주행 (50 km/h)", "실제 MSSQ, 라벨 = 다음 5분 실제 MISC"], title_cls="tm", step=16, rx=10))
    s.append(arrow("M230,350 L269,350", "a-pc", "pc"))
    s.append(arrow("M230,425 L269,425", "a-pc", "pc"))
    s.append(box(550, 322, 220, 56, "cfgbox", "constants · sim_config", ["K0 51.4 · η 0.65 (사람이 옮겨 적음)"], title_cls="tm", step=16, rx=8))
    s.append(arrow("M510,350 L549,350", "a-cfg", "cfg"))
    s.append(box(550, 392, 220, 70, "store", "CURRENT 모델 (v018)", ["models/model_2~6.txt", "thresholds.json (Tier 리드)"], title_cls="t2", step=16, rx=8))
    s.append(arrow("M549,427 L511,427", "a-pc", "pc"))
    s.append(box(810, 392, 245, 70, "outpill", "v018/verify/tudelft_replay.md", ["k4 ROC 0.64 · PR 0.61", "Tier 1 닿기 전 켜짐 86%"], title_cls="tm", step=16, rx=8))
    s.append(arrow("M390,462 L390,476 L920,476 L920,463", "a-pc", "pc"))
    return f'<svg viewBox="0 0 1250 490" role="img" aria-label="검증과 보정 루프">{"".join(s)}</svg>'


# ------------------------------------------------------------------ 3. Pi 런타임
def runtime():
    s = [DEFS]
    # 설정 (위)
    s.append(box(250, 40, 440, 70, "cfgbox", "protocol/*.json → gen_cpp.py → protocol_gen.hpp", ["컴파일 때 고정: 32열 Row, 52개 순서, Oman · Wf · 이벤트 · 휴식 상수, 기본 프로필"], title_cls="tm", step=18, rx=8))
    s.append(box(770, 40, 180, 70, "cfgbox", "runs/v018/models/", ["model_2~6.txt (CURRENT)", "models.json (sha256)"], title_cls="tm", step=16, rx=8))
    s.append(box(1030, 40, 200, 70, "cfgbox", "controller/config/", ["policy.json · thresholds.json", "--rf (P5 명부) 또는 --phone, --hud"], title_cls="tm", step=16, rx=8))
    for x in (340, 600):
        s.append(arrow(f"M{x},110 L{x},199", "a-cfg", "cfg"))
    s.append(arrow("M860,110 L860,199", "a-cfg", "cfg"))
    s.append(arrow("M1130,110 L1130,199", "a-cfg", "cfg"))
    # 입력
    s.append(box(20, 200, 150, 160, "proc-todo", "입력", ["trip.bin 재생 (구현)", "100 Hz × 32열", "", "MPU-6050 · CAN", "실시간 (미구현)"], title_cls="t2", dy0=24, step=17, rx=10))
    P = [
        (250, "P1 acq · 우선순위 40", ["trip.bin → 100 Hz", "clock_nanosleep 절대 시각", "주행 안 seq 번호", "RIDE_START / RIDE_END", "LIVE 모드 미구현"]),
        (510, "P2 feat · 30", ["Wf · Oman · 이벤트 · 휴식", "창 10 s / 1 m / 5 m / 누적", "1,000샘플마다 46개", "seq 빠짐 셈", "RIDE_START에 초기화"]),
        (770, "P3 infer · 20", ["lib_lightgbm 4.6.0", "모델 5개 (로드 95 ms)", "폰마다 52개 행 (최대 9)", "단조 보정 min(p_k, p_k−1)", "--vehicle-type"]),
        (1030, "P4 policy · 15", ["Tier 1·2·3 문턱 + tol", "hold · off · 쿨다운 · 비활성", "프로필은 설문 그대로 저장", "바뀐 상태만 원자 저장", "시각 = 주행 시각"]),
    ]
    for x, t, ls in P:
        w = 190 if x == 1030 else 180
        s.append(box(x, 200, w, 160, "proc-pi", t, ls, title_cls="tm", dy0=24, step=19, rx=10))
    gaps = [(170, 250, ["파일 읽기"], []), (430, 510, ["SAMPLE", "100 Hz", "seq + Row"], ["제한 5 ms"]),
            (690, 770, ["VEH", "10초마다", "t + 46개"], ["제한 1 s"]), (950, 1030, ["RISK", "폰마다", "P(≥2..6)"], ["제한 1 s"])]
    for a, b, top, bot in gaps:
        s.append(arrow(f"M{a},300 L{b - 1},300", "a-pi", "pi"))
        m = (a + b) / 2
        for j, t in enumerate(top):
            s.append(label(m, 258 + j * 13, t, "lb"))
        for j, t in enumerate(bot):
            s.append(label(m, 318 + j * 13, t, "lb2"))
    # PROFILES
    s.append(arrow("M900,360 L900,385 L1079,385 L1079,361", "a-pi", "pi"))
    s.append(arrow("M1100,361 L1100,400 L880,400 L880,361", "a-pi-d", "pi"))
    s.append(label(990, 420, "PROFILES 요청 → 회신: n, tol_display, 폰마다 id · mssq · sens · ev · tol", "lb"))
    # P4 출력
    s.append(arrow("M1180,360 L1180,439", "a-pi", "pi"))
    outs = [(770, 445, "cycles.csv", "seq · t · 지연 · 폰 · p2~p6 · 기기 ON"),
            (1005, 445, "stdout", "CMD <t> <기기> <tier> <on>"),
            (770, 495, "state/tolerance/<기기>.json", "{\"n\": 허용도 카운터}"),
            (1005, 495, "state/profiles/<ID>.json", "{mssq, sens} 앱 설문 그대로")]
    for x, y, a, b in outs:
        s.append(f'<rect x="{x}" y="{y}" width="225" height="42" rx="6" class="outpill"/>')
        s.append(f'<text x="{x + 8}" y="{y + 17}" class="m">{E(a)}</text>')
        s.append(f'<text x="{x + 8}" y="{y + 33}" class="s">{E(b)}</text>')
    # RF 줄: P5·P6 구현, 브리지·폰·HUD는 Pi와 실연결 시험 전
    s.append(box(20, 590, 150, 90, "proc-todo", "폰 ×최대 9 · HUD", ["BLE Central", "INT · MOT · HB 받음", "HELLO · SCR · OVR · PROF"], title_cls="t2", dy0=22, step=16, rx=10, pad=10))
    s.append(box(250, 590, 180, 90, "proc-todo", "ESP32-S3 브리지", ["BLE GATT ↔ UART", "광고 MSAgent-xxxx", "MTU 247 · 512 B 분할"], title_cls="t2", dy0=22, step=16, rx=10))
    s.append(box(510, 590, 180, 90, "proc-pi", "P6 comm", ["/dev/ser3 (UART3) 115,200", "<id>|JSON ↔ QNX 메시지", "HB 1 s · HELLO 5 s · MOT"], title_cls="tm", dy0=22, step=16, rx=10))
    s.append(box(770, 590, 180, 90, "proc-pi", "P5 devmgr", ["HELLO → 명부 (연결 0~8)", "TIER_CMD → 그 기기에 INT", "DEV_SYNC 30 s 없으면 OFF"], title_cls="tm", dy0=22, step=16, rx=10))
    for a, b, t, cls in [(770, 690, "INT · STREAM", "pi"), (510, 430, "<id>|JSON", "todo"), (250, 170, "BLE", "todo")]:
        s.append(f'<path d="M{a - 1},635 L{b + 1},635" class="a-{cls}" marker-end="url(#ah-{cls})" marker-start="url(#ah-{cls})"/>')
        s.append(label((a + b) / 2, 627, t, "lb"))
    s.append(arrow("M1220,320 L1238,320 L1238,635 L951,635", "a-pi", "pi"))
    s.append(label(1100, 627, "TIER_CMD · DEV_SYNC 10 s", "lb"))
    s.append(arrow("M660,360 L660,589", "a-pi", "pi"))
    s.append(label(650, 470, "MOTION 10 Hz (--motion)", "lb", "end"))
    s.append(label(650, 484, "Wf ax·ay + pred_ax·ay_10s", "lb2", "end"))
    s.append(label(20, 570, "아래 줄: P5·P6은 구현 (P4 --rf, P2 --motion으로 연결). ESP32·폰·HUD도 구현됐지만 Pi와 실제로 붙인 시험은 아직 (Pi에서는 가상 터미널 브리지 흉내로 확인).", "cap", "start"))
    return f'<svg viewBox="0 0 1250 700" role="img" aria-label="Pi 런타임">{"".join(s)}</svg>'


# ------------------------------------------------------------------ 4. 한 주기
def cycle():
    s = [DEFS]
    L = {"P1": 110, "P2": 330, "P3": 560, "P4": 790, "F": 1000}
    names = {"P1": "P1 acq", "P2": "P2 feat", "P3": "P3 infer", "P4": "P4 policy", "F": "파일 · 로그"}
    for k, x in L.items():
        s.append(box(x - 75, 16, 150, 36, "proc-pi" if k != "F" else "store", names[k], [], title_cls="t2", anchor="middle", dy0=23, rx=8))
        s.append(f'<path d="M{x},52 L{x},520" class="life"/>')

    def msg(a, b, y, text, dashed=False, sub=None):
        xa, xb = L[a], L[b]
        d = -1 if xb < xa else 1
        s.append(arrow(f"M{xa},{y} L{xb - 2 * d},{y}", "a-pi-d" if dashed else "a-pi", "pi"))
        s.append(label((xa + xb) / 2, y - 7, text, "lb"))
        if sub:
            s.append(label((xa + xb) / 2, y + 15, sub, "lb2"))

    for j in range(3):
        s.append(arrow(f"M110,{84 + j * 9} L327,{84 + j * 9}", "a-pi", "pi"))
    s.append(label(220, 74, "SAMPLE × 1,000 (10 ms마다, seq 0, 1, 2 …)", "lb"))
    s.append(label(220, 125, "깨어남 늦음 중앙값 953 µs (QNX 1 ms 틱) · P2 회신 최대 2.5 ms", "lb2"))
    s.append('<rect x="324" y="140" width="12" height="44" class="act"/>')
    s.append(label(345, 160, "1,000번째 샘플: 창 갱신", "lb", "start"))
    s.append(label(345, 174, "→ 차량 피처 46개 (VehicleVec)", "lb", "start"))
    msg("P2", "P3", 214, "VEH { seq, t, t_emit_ns, x[46] }")
    msg("P3", "P4", 254, "PROFILES 요청")
    msg("P4", "P3", 290, "ProfilesReply { n, tol_display, e[9]: id, mssq, sens, ev, tol }", dashed=True)
    s.append('<rect x="554" y="304" width="12" height="56" class="act"/>')
    s.append(label(545, 320, "n행 × 52개 조립 (폰 없으면 기본 프로필)", "lb", "end"))
    s.append(label(545, 334, "LightGBM 5개 → 단조 보정", "lb", "end"))
    s.append(label(545, 348, "폰 9대 기준 중앙값 0.85 ms, 최대 0.90 ms", "lb2", "end"))
    msg("P3", "P4", 386, "RISK { seq, t, t_emit_ns, t_infer_ns, n, id[n], p[n][5] }")
    s.append('<rect x="784" y="400" width="12" height="46" class="act"/>')
    s.append(label(775, 416, "기기마다 r_d ≥ base_th + tol 확인", "lb", "end"))
    s.append(label(775, 430, "hold · off_hold · 쿨다운 · 게이트", "lb", "end"))
    msg("P4", "F", 466, "cycles.csv 한 줄 (폰마다) · CMD", sub="바뀐 state만 원자 저장")
    s.append(label(550, 548, "P2가 VEH를 낸 때부터 P4가 끝날 때까지: Pi 186주기 중앙값 0.57 ms, 최대 2.03 ms (10초 주기의 0.02%)", "t2", "middle"))
    return f'<svg viewBox="0 0 1100 565" role="img" aria-label="10초 한 주기">{"".join(s)}</svg>'


# ------------------------------------------------------------------ 5. 52개 벡터 배치
def vector():
    groups = [("G1", 33, "IMU 동역학 · Oman", "pi"), ("G2", 7, "속도", "pi"), ("G5", 2, "공조", "pi"), ("G6", 1, "좌석", "pi"),
              ("G9", 3, "시간", "pi"), ("G8", 2, "허용도", "off"), ("G10", 4, "승객", "pc")]
    s = []
    W = 1160 / 52
    x, idx = 20, 0
    for g, n, d, c in groups:
        w = n * W
        s.append(f'<rect x="{x:.1f}" y="40" width="{w - 2:.1f}" height="44" rx="4" class="seg-{c}"/>')
        s.append(f'<text x="{x + w / 2 - 1:.1f}" y="60" class="t2" text-anchor="middle">{g}</text>')
        if w > 40:
            s.append(f'<text x="{x + w / 2 - 1:.1f}" y="76" class="s" text-anchor="middle">{n}</text>')
        s.append(f'<text x="{x + 1:.1f}" y="100" class="lb2">{idx}</text>')
        s.append(f'<text x="{x + w / 2 - 1:.1f}" y="118" class="lb" text-anchor="middle">{E(d) if w > 50 else ""}</text>')
        x += w
        idx += n
    s.append(f'<text x="1180" y="100" class="lb2" text-anchor="end">51</text>')
    s.append(f'<path d="M20,30 L{20 + 46 * W - 2:.1f},30" class="brace-pi"/>')
    s.append(label(20 + 46 * W / 2, 22, "P2가 만듦: 차량 피처 46개 (인덱스 0~45)", "lb"))
    s.append(f'<path d="M{20 + 46 * W:.1f},30 L1180,30" class="brace-pc"/>')
    s.append(label(20 + 49 * W, 22, "P3가 붙임 (46~51)", "lb"))
    s.append(label(20, 140, "G5 2개 · G6 1개는 칸이 좁아 이름을 생략. G8(허용도 2개)은 꺼져 있어 항상 NaN, svc_conflict(32)도 NaN. 피처 순서 v5에서 CAN·내비·소음 등 25개를 지움.", "cap", "start"))
    return f'<svg viewBox="0 0 1200 150" role="img" aria-label="52개 벡터">{"".join(s)}</svg>'


CSS = """
:root{--bg:#f7f6f3;--card:#ffffff;--fg:#1c1c1e;--muted:#6a6a70;--line:#d9d6d0;
--pc:#2f6bd0;--pc-soft:#e7effc;--pi:#178a64;--pi-soft:#e2f4ec;--cfg:#8a6a1f;--cfg-soft:#fbf3df;
--todo:#9a9aa1;--todo-soft:#f1f1f3;--out-soft:#eef6f2;--file:#f1efea;--off:#d7d4ce}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#141416;--card:#1d1d20;--fg:#ececef;--muted:#a3a3aa;--line:#38383e;
--pc:#76a6ff;--pc-soft:#1b2842;--pi:#4fc99c;--pi-soft:#15302a;--cfg:#e0b75a;--cfg-soft:#2e2715;
--todo:#7c7c84;--todo-soft:#222226;--out-soft:#17282255;--file:#26262a;--off:#3a3a40}}
:root[data-theme="dark"]{--bg:#141416;--card:#1d1d20;--fg:#ececef;--muted:#a3a3aa;--line:#38383e;
--pc:#76a6ff;--pc-soft:#1b2842;--pi:#4fc99c;--pi-soft:#15302a;--cfg:#e0b75a;--cfg-soft:#2e2715;
--todo:#7c7c84;--todo-soft:#222226;--out-soft:#17282255;--file:#26262a;--off:#3a3a40}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.6 -apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Pretendard","Noto Sans KR","Segoe UI",sans-serif}
main{max-width:1240px;margin:0 auto;padding:32px 16px 80px}
h1{font-size:28px;margin:0 0 6px;letter-spacing:-.01em}
h2{font-size:20px;margin:48px 0 8px;padding-top:8px;border-top:1px solid var(--line)}
h3{font-size:16px;margin:24px 0 8px}
p{margin:6px 0 10px;max-width:880px}
.sub{color:var(--muted);margin-bottom:18px}
code,.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:.88em}
nav{display:flex;flex-wrap:wrap;gap:6px 14px;font-size:14px;margin:12px 0 4px}
nav a{color:var(--pc);text-decoration:none}
.legend{display:flex;flex-wrap:wrap;gap:8px 18px;font-size:13px;color:var(--muted);margin:10px 0 0}
.legend span{display:inline-flex;align-items:center;gap:6px}
.sw{width:22px;height:12px;border-radius:3px;display:inline-block;border:1.5px solid}
.fig{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px;margin:12px 0;overflow-x:auto}
.fig svg{display:block;width:100%;min-width:980px;height:auto}
.fig.small svg{min-width:760px}
.cap{color:var(--muted);font-size:13px;margin-top:6px}
.tablewrap{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:12px;margin:10px 0}
table{border-collapse:collapse;width:100%;font-size:13.5px}
th,td{text-align:left;vertical-align:top;padding:8px 10px;border-bottom:1px solid var(--line)}
th{font-weight:600;background:var(--file);white-space:nowrap}
tr:last-child td{border-bottom:none}
td ul{margin:0;padding-left:16px}
td li{margin:1px 0}
.tag{display:inline-block;font-size:11.5px;padding:1px 7px;border-radius:999px;border:1px solid;white-space:nowrap}
.tag.ok{color:var(--pi);border-color:var(--pi)}
.tag.no{color:var(--todo);border-color:var(--todo)}
.tag.pc{color:var(--pc);border-color:var(--pc)}
.tag.fit{color:var(--cfg);border-color:var(--cfg)}
a{color:var(--pc)}
.refs{font-size:14px;padding-left:22px;max-width:980px}
.refs li{margin:3px 0}
td a{white-space:normal}
svg text{fill:var(--fg)}
svg .t{font-size:14px;font-weight:650}
svg .t2{font-size:12.5px;font-weight:600}
svg .tm{font-size:12.5px;font-weight:650;font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
svg .lt{font-size:16px;font-weight:700}
svg .big{font-size:18px;font-weight:750;fill:var(--pi)}
svg .big-todo{font-size:18px;font-weight:750;fill:var(--todo)}
svg .s{font-size:11.5px;fill:var(--muted)}
svg .m{font-size:10.5px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
svg .lb{font-size:11px;fill:var(--fg)}
svg .lb2{font-size:10.5px;fill:var(--muted)}
svg .cap{font-size:12px;fill:var(--muted)}
.lane-pc{fill:none;stroke:var(--pc);stroke-width:1.5}
.lane-pi{fill:none;stroke:var(--pi);stroke-width:1.5}
.lane-todo{fill:none;stroke:var(--todo);stroke-width:1.5;stroke-dasharray:6 5}
.proc-pc{fill:var(--pc-soft);stroke:var(--pc);stroke-width:1.2}
.proc-pi{fill:var(--pi-soft);stroke:var(--pi);stroke-width:1.2}
.proc-todo{fill:var(--todo-soft);stroke:var(--todo);stroke-width:1.2;stroke-dasharray:5 4}
.store{fill:var(--file);stroke:var(--line);stroke-width:1.2}
.cfgbox{fill:var(--cfg-soft);stroke:var(--cfg);stroke-width:1.1;stroke-dasharray:3 3}
.file{fill:var(--file);stroke:var(--line)}
.cfgpill{fill:var(--cfg-soft);stroke:var(--cfg);stroke-dasharray:3 3}
.outpill{fill:var(--card);stroke:var(--pi);stroke-width:1.1}
.a-pc{fill:none;stroke:var(--pc);stroke-width:1.6}
.a-pi{fill:none;stroke:var(--pi);stroke-width:1.6}
.a-pi-d{fill:none;stroke:var(--pi);stroke-width:1.6;stroke-dasharray:5 4}
.a-cfg{fill:none;stroke:var(--cfg);stroke-width:1.4;stroke-dasharray:3 3}
.a-todo{fill:none;stroke:var(--todo);stroke-width:1.4;stroke-dasharray:5 4}
.mk-pc{fill:var(--pc)}.mk-pi{fill:var(--pi)}.mk-cfg{fill:var(--cfg)}.mk-todo{fill:var(--todo)}
.life{stroke:var(--line);stroke-width:1.4;stroke-dasharray:4 4;fill:none}
.act{fill:var(--pi-soft);stroke:var(--pi)}
.seg-pi{fill:var(--pi-soft);stroke:var(--pi)}
.seg-pc{fill:var(--pc-soft);stroke:var(--pc)}
.seg-off{fill:var(--off);stroke:var(--todo);stroke-dasharray:3 3}
.brace-pi{stroke:var(--pi);stroke-width:2;fill:none}
.brace-pc{stroke:var(--pc);stroke-width:2;fill:none}
@media (max-width:640px){h1{font-size:23px}body{font-size:14.5px}}
"""


def table(head, rows, cls=""):
    h = "".join(f"<th>{c}</th>" for c in head)
    b = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<div class="tablewrap {cls}"><table><thead><tr>{h}</tr></thead><tbody>{b}</tbody></table></div>'


def ul(*xs):
    return "<ul>" + "".join(f"<li>{x}</li>" for x in xs) + "</ul>"


C = lambda x: f"<code>{E(x)}</code>"
OK, NO = '<span class="tag ok">구현</span>', '<span class="tag no">미구현</span>'
DONE, PART, DROP = '<span class="tag ok">해결</span>', '<span class="tag pc">일부</span>', '<span class="tag no">채택 안 함</span>'
LIT, OWN, PRD, FIT = ('<span class="tag ok">문헌</span>', '<span class="tag no">자체 결정</span>',
                      '<span class="tag pc">PRD 설계</span>', '<span class="tag fit">데이터 적합</span>')

# ------------------------------------------------------------------ 참고 문헌 (링크)
REFS = {
    "oman1990": ("Oman (1990), Motion sickness: a synthesis and evaluation of the sensory conflict theory, Can. J. Physiol. Pharmacol. 68(2)",
                 "https://doi.org/10.1139/y90-044"),
    "irmak2022": ("Irmak, Kotian, Happee, de Winkel, Pool (2022), Amplitude and Temporal Dynamics of Motion Sickness, Front. Syst. Neurosci.",
                  "https://doi.org/10.3389/fnsys.2022.866503"),
    "irmak2021": ("Irmak, Pool, Happee (2021), Objective and subjective responses to motion sickness, Exp. Brain Res. 239",
                  "https://doi.org/10.1007/s00221-020-05986-6"),
    "atdoms": ("ATDOMS 데이터셋 (TU Delft), 4TU.ResearchData", "https://data.4tu.nl/datasets/24d81854-b10b-446b-8798-ae31fb8782d0"),
    "donohew2004": ("Donohew & Griffin (2004), Motion sickness: effect of the frequency of lateral oscillation, Aviat. Space Environ. Med. 75(8)",
                    "https://pubmed.ncbi.nlm.nih.gov/15328780/"),
    "iso2631": ("ISO 2631-1:1997, Mechanical vibration and shock: Evaluation of human exposure to whole-body vibration, Part 1",
                "https://www.iso.org/standard/7612.html"),
    "lawther1987": ("Lawther & Griffin (1987), Prediction of the incidence of motion sickness from the magnitude, frequency, and duration of vertical oscillation, J. Acoust. Soc. Am.",
                    "https://eprints.soton.ac.uk/410880/"),
    "golding2006": ("Golding (2006), Predicting individual differences in motion sickness susceptibility by questionnaire, Pers. Individ. Differ. 41(2)",
                    "https://doi.org/10.1016/j.paid.2006.01.012"),
    "mssq": ("Golding (2006), MSSQ-Short 설문지와 채점법 (PDF)", "https://www.westminster.ac.uk/sites/default/files/MSSQ-short_0.pdf"),
    "bos2005": ("Bos, MacKinnon, Patterson (2005), Motion sickness symptoms in a ship motion simulator: effects of inside, outside, and no view (MISC 척도), Aviat. Space Environ. Med. 76(12)",
                "https://pubmed.ncbi.nlm.nih.gov/16370260/"),
    "golding1997": ("Golding & Stott (1997), Objective and subjective time courses of recovery from motion sickness, J. Vestib. Res. 7(6)",
                    "https://content.iospress.com/articles/journal-of-vestibular-research/ves7-6-01"),
    "he2013": ("He, Yan, Wu, Chu, Peng (2013), ICTIS 2013 (승객 승차감 가감속 문턱)", "https://ascelibrary.com/doi/10.1061/9780784413036.220"),
    "nguyen2019": ("Nguyen, NguyenDinh, Lechner, Wong (2019) (승객이 극도로 불편한 횡가속도)", "https://libmast.utm.my/Record/ntu-10356%2F151551/Description"),
    "frank2001": ("Frank & Hall (2001), A Simple Approach to Ordinal Classification, ECML", "https://doi.org/10.1007/3-540-44795-4_13"),
    "ke2017": ("Ke 등 (2017), LightGBM: A Highly Efficient Gradient Boosting Decision Tree, NeurIPS",
               "https://proceedings.neurips.cc/paper/2017/hash/6449f44a102fde848669bdd9eb6b76fa-Abstract.html"),
    "turner1999": ("Turner & Griffin (1999), Motion sickness in public road transport, Br. J. Psychol. 90(4)", "https://eprints.soton.ac.uk/406260/"),
    "schmidt2020": ("Schmidt 등 (2020), Sick of being driven? (요약)",
                    "https://www.themoonlight.io/review/sick-of-being-driven-prevalence-and-modulating-factors-of-carsickness-in-the-european-population-in-context-of-automated-driving"),
    "arnold2019": ("Arnold 등 (2019), Effect of virtual reality and whole body heating on motion sickness, Displays 60",
                   "https://research-portal.uea.ac.uk/en/publications/effect-of-virtual-reality-and-whole-body-heating-on-motion-sickne/"),
    "damour2017": ("D'Amour, Bos, Keshavarz (2017), The efficacy of airflow and seat vibration on reducing visually induced motion sickness, Exp. Brain Res.",
                   "https://doi.org/10.1007/s00221-017-5009-1"),
    "gu2025": ("Gu 등 (2025), Just 1-min exposure to a pure tone at 100 Hz may improve motion sickness, Environ. Health Prev. Med.",
               "https://doi.org/10.1265/ehpm.24-00247"),
    "baton": ("BATON 데이터셋 (Hugging Face HenryYHW/BATON)", "https://huggingface.co/datasets/HenryYHW/BATON"),
    "comma2k19": ("Schafer 등 (2018), A Commute in Data: The comma2k19 Dataset, arXiv:1812.05752", "https://arxiv.org/abs/1812.05752"),
}


def R(key, short=None):
    """본문 안 인용 링크. short가 없으면 '저자 (연도)'까지만"""
    text, url = REFS[key]
    if short is None:
        short = text.split(",")[0] if "(" in text.split(",")[0] else text
    return f'<a href="{url}" target="_blank" rel="noopener">{E(short)}</a>'


# ------------------------------------------------------------------ 단계별 입력 · 처리 · 출력
OFFLINE_ROWS = [
    ["S0 계약", OK + "<br>" + C("python3 protocol/check.py"),
     ul(C("protocol/constants.json"), C("feature_order.json") + " (version 3)", C("trip_format.json"), C("messages.schema.json"), C("uuids.json")),
     ul("메시지 예시 7개를 스키마로 검증, 잘못된 메시지 8개 거부", "피처 52개(켬 49, NaN 고정 3) 확인", C("gen_cpp.py") + "가 JSON을 C++ 상수로 바꿈 (이벤트 기준, 되돌림 폭 " + C("kEventRearmMargin") + " 포함)",
        C("thresholds.json") + "의 feature_order_version이 빌드와 같은지 확인"),
     ul(C("controller/common/protocol_gen.hpp") + " (Python과 C++가 같은 값)")],
    ["S1 Oman 적합", OK + "<br>" + C("python3 ml/verify/oman_tudelft.py ml/data/raw/ATDOMS.mat"),
     ul(C("ml/data/raw/ATDOMS.mat") + " (" + R("atdoms", "ATDOMS") + "): TU Delft 17명, 0.3 Hz 사인 자극 C1~C4 = 1 / 1.5 / 2 / 2.5 m/s², 1분마다 MISC, MSSQ", C("ml/oman.py") + " (Oman 식, " + R("irmak2022", "Irmak 2022") + ")"),
     ul("100 Hz로 자극 재구성 → Oman (S, F, (F+S)^0.4)", "시간상수 고정, 사람마다 K 하나를 MAE 최소로 적합", "0.63 Hz 입력 필터 있음/없음 두 번", "LOPO: K0만 vs K0 × MSSQ 민감도"),
     ul("K0 = 51.4 (필터 있음), 48.7 (없음), MAE 0.97 (논문 1.01~1.03)", "η 표준편차 0.65 (MSSQ로 설명 안 되는 퍼짐)", "LOPO MAE 1.32 → 1.26", C("ml/verify/out/oman_tudelft.json") + ", " + C("K_lpf.npy") + ", " + C("K_raw.npy") + ", " + C("mssq.npy") + " (원자료에서 결정적으로 나와 버전 폴더 밖)", "사람이 " + C("constants.json") + " K0, " + C("sim_config.json") + " η에 옮겨 적음")],
    ["S3 시뮬레이터", OK + "<br>" + C("python3 ml/simulator/build_dataset.py --note \"…\""),
     ul(C("sim_config.json") + ": 구간 비율·길이·속도, 운전자, 회생, 차량, IMU 잡음, 승객, 실내, 라벨. T-23 보정 항목은 " + C("calibrated") + " 표시", C("sim_effects.json") + ": 뒷자리 1.1~1.5, 온도 1.05~1.2, 풍량 0.85~1.0, 소음 1.0", C("constants.json") + ": K0, MSSQ 규준"),
     ul("경로 480개 (15~40분, 구간 8종, 고속도로 8%) → v² 앞뒤 훑기 → 0.3 Hz 저크 제한", "운전자 가속 0.7~2.0, 감속 0.5~1.6 m/s², 정차 5~120 s", "IMU = 운동학 + g·sinθ + 노면 굴곡 + 차로 유지 흔들림 + 2~15 Hz 진동 + 잡음", "CAN 신호: 페달, 회생, SOC, 배터리, 경로 예측, 공조, 좌석, 소음", "승객 120명: MSSQ ~ N(12.9, 9.9²), true_sens = sens·e^N(0, 0.65²)", "라벨: K = K0·true_sens·m, 운동학 가속도로 Oman, 1분 단위 잡음 0.5, 반올림"),
     ul("탑승마다 trip (100 Hz × 32열, " + C("trip_format.json") + ")", "탑승마다 MISC(t), score(t), 승객 설정", C("ml/out/runs/v017/trips/ride_0000~0007.bin") + " (재생용 8개, 약 55 MB씩)")],
    ["S2 피처", OK + "<br>" + C("build/featurize trip.bin features.bin [--oman-k K]"),
     ul("trip.bin: float64 [version, 32, 100] 머리말 + 100 Hz 행", C("protocol_gen.hpp") + " (Wf 계수, 이벤트 기준과 되돌림 폭, 휴식, Oman 상수, 52개 순서)"),
     ul("C++ " + C("FeatureExtractor") + " (P2와 같은 코드)", "Wf 쌍선형 4구간 → RMS, jerk, MSDV. 필터는 첫 샘플 정상상태로 시작 (T-22)", "0.63 Hz LP → Oman, 이벤트는 기준을 넘는 순간 세고 0.25 m/s² 되돌아와야 다시 셈 (T-07)", "창 10 s / 1 m / 5 m / 누적, 1,000샘플마다 출력"),
     ul("features.bin: float64 [feature_version, 47] 머리말 + 10초마다 [t, 차량 피처 46개]")],
    ["데이터셋 조립", OK + " (S3 안에서)",
     ul("features.bin (46개)", "sim.py의 승객 (MSSQ, sens, EV 경험), 차량 유형, MISC(t)"),
     ul("46개 + G8 2개 (NaN) + G10 4개 = 52개", "y_k = 다음 10초 행부터 300초 안 최대 MISC ≥ k (k = 2..6)", "미래 5분이 다 있는 행만 남김", "8개 프로세스 병렬, 약 7분"),
     ul(C("ml/out/runs/v017/dataset.npz") + ": X (286,788×52), y (×5), t, misc, score, future_max, ride_id, trip_id, person_id, k, feature_names, feature_version (75 MB)", C("rides.json") + ": 탑승 1,920회 설정", C("dataset_report.json") + ": 라벨 비율 0.910 / 0.555 / 0.209 / 0.059 / 0.012, K 중앙값 55.2", C("run.json") + ": 종류, git 커밋, feature_order 5, 설명")],
    ["S4 학습", OK + "<br>" + C("python3 ml/train/train.py [--data vNNN] --note \"…\""),
     ul(C("dataset.npz") + " (기본: 가장 최근 데이터셋 버전)", C("feature_order.json") + ": 단조 +1 7개, 그룹 드롭아웃 비율, droppable", C("ml/train/common.py") + ": LightGBM 파라미터 (잎 7, 잎당 1,000행, L2 10, 학습률 0.03, 최소 100회)", C("controller/config/policy.json") + ": 재생용 hold, off"),
     ul("사람 단위 5-fold, 학습 쪽 15% 사람은 조기 종료 · 문턱용", "원본 + 그룹 드롭아웃 복사본 1벌 (+ 칸 2%)", "이진 LightGBM 5개 (P≥2..6), 단조 제약, 단조 보정 (" + R("frank2001", "Frank & Hall 2001") + " 방식)", "검증용 사람 재생으로 Tier 문턱 고르기 (0.05~0.95, 0.01 간격)", "AC-03 PR-AUC, AC-04 그룹 지우기, AC-05 재생 지표", "전체 사람으로 최종 모델"),
     ul(C("ml/out/runs/v018/models/model_2~6.txt") + " (LightGBM 텍스트, 트리 186/204/159/117/100)", C("models/models.json") + " (트리 수, sha256, feature_order_version 5)", C("v018/thresholds.json") + ": 0.46 / 0.47 / 0.54", C("v018/train/report.md") + ", " + C("report.json"), C("v018/run.json") + ": inputs.dataset = v017")],
    ["배포", OK + "<br>" + C("python3 ml/runs.py use v018"),
     ul(C("v018/thresholds.json"), C("v018/models/models.json") + " (있는지 확인)"),
     ul(C("ml/out/runs/CURRENT") + "에 v018을 씀", "문턱을 배포 설정으로 복사하고 " + C("\"run\": \"v018\"") + "을 붙임 (git으로 이력)"),
     ul(C("controller/config/thresholds.json"), C("run_pipeline.sh") + ", " + C("model_parity.py") + ", " + C("compare.py") + "가 CURRENT를 기본값으로 읽음")],
    ["실차 변환", OK + "<br>" + C("python3 ml/realdata/build_real.py --source baton|comma2k19 --root …"),
     ul(R("baton", "BATON-Sample") + ": 운전자 2명, 34주행, 19.1 h (폰 IMU + CAN 속도)", R("comma2k19", "comma2k19") + " Chunk 1: 차 1대, 19구간, 2.9 h"),
     ul("장착 방향 추정 (convert.align: 기울기, 진행 방향, 정지 구간 중력)", "a_long = CAN 속도 미분, a_lat = v·ω_z로 Oman 라벨 (가상 승객, 시뮬레이터와 같은 120명 풀)", "trip.bin으로 바꿔 같은 featurize", "없는 신호는 NaN"),
     ul(C("v019/real_baton.npz") + " (23,348행)", C("v020/real_comma2k19.npz") + " (1,788행)", C("real_<source>_report.json") + ": 축 정렬, 라벨 비율", C("trips/real/<source>/") + " (--keep-trips)")],
    ["실차 비교", OK + "<br>" + C("python3 ml/realdata/compare.py --source baton"),
     ul("실차 npz (최신 버전)", "CURRENT 모델 (v018)", "그 모델의 학습 데이터 (v017)"),
     ul("운동 피처 분포 (p10/p50/p90, KS)", "라벨 비율 P(≥k), Oman 점수 (같은 시간 범위)", "시뮬레이터 모델을 실차 행에 그대로 적용한 ROC/PR-AUC, 평균 예측 대 실제 비율"),
     ul(C("v018/compare/baton_v019.md") + ": 사람 운전 k4 ROC 0.85, PR 0.59 (평균 예측 0.233, 실제 0.203)", C("v018/compare/comma2k19_v020.md") + ": k4 ROC 0.95, PR 0.66")],
    ["시뮬레이터 보정 (T-23)", OK + "<br>" + C("build_dataset.py --trips 60 --out /tmp/x/sim.npz → calibrate.py /tmp/x/sim.npz"),
     ul("작은 시뮬 데이터셋 (" + C("--out") + ", 버전 폴더 밖)", "실차 npz (BATON 사람 운전 = 목표, comma2k19 = 점검)"),
     ul("피처 15개 중앙값과 KS 거리, 속도대 비율, 라벨 비율", "평균 KS가 점수. 80주행으로 세 번 돌려 정함"),
     ul("요약표 (화면, " + C("--json") + ")", "사람이 " + C("sim_config.json") + "을 고침: KS 0.38 → 0.14, P(≥4) 0.37 → 0.21 (실차 0.20)")],
    ["TU Delft 재생", OK + "<br>" + C("python3 ml/verify/tudelft_replay.py ml/data/raw/ATDOMS.mat"),
     ul(C("ATDOMS.mat") + ": 17명 × 4조건, MISC 30초마다, MSSQ", "CURRENT 모델, 그 문턱"),
     ul("같은 앞뒤 가속도를 내는 가상 주행 (v0 50 km/h에서 3.3초마다 속도 오르내림) → featurize", "승객 피처 = 실제 MSSQ", "라벨 = 10초마다 다음 5분 실제 MISC 최대 ≥ k", "모델, oman_score, oman × sens, 개인 K Oman(상한) 비교"),
     ul(C("v018/verify/tudelft_replay.md") + ", " + C(".json"), "k4 ROC 0.64 / PR 0.61 (oman_score 하나 0.64 / 0.60)", "사람 순위 Spearman 0.19 (MSSQ 0.41, 개인 K 0.65. v007은 0.40)", "Tier 1이 MISC 3 전에 켜짐 86%, 리드 중앙값 6.5분")],
    ["AC-01 동등성", OK + "<br>" + C("model_parity.py make → (Pi) verify_model → check"),
     ul("CURRENT 모델 5개", "데이터 2,000행 + NaN 201 + 0 201 + 분기 기준값 ±1e-9와 그 값 1,800 = 4,202행"),
     ul("Mac Python LightGBM 기대값", "Pi " + C("verify_model") + "이 lib_lightgbm으로 예측, 메모리·시간 측정"),
     ul(C("v007/parity/") + " vectors.bin, expected_p.bin, actual_pi.bin, meta.json, result.json", "Pi 결과 (v007, 2026-11-17): 최대 차이 2.2e-16, Tier 결정 100% 일치, 로드 95 ms, 9행 중앙값 0.85 ms, 약 12.6 MB")],
    ["AC-02 파이프라인", OK + "<br>" + C("(Pi) run_pipeline.sh → pipeline_check.py check"),
     ul("Pi " + C("cycles.csv") + " (P4 로그)", "같은 trip.bin, 폰 프로필 (" + C("spec <ride_id>") + " 또는 " + C("default:<vtype>") + ")"),
     ul("Mac에서 featurize + Python 예측 + Python 정책 재생", "주기 수, 확률, 기기 ON/OFF를 주기마다 비교"),
     ul("JSON: 확률 차이 1.1e-16, ON/OFF 불일치 0 (186주기 × 4기기), 실시간 지연 중앙값 0.57 ms · 최대 2.03 ms", "샘플 186,518개 빠짐 0 (v007, 2026-11-17)")],
    ["CSV 내보내기", OK + "<br>" + C("python3 ml/verify/export_csv.py"),
     ul("CURRENT 모델이 학습한 데이터셋 버전의 " + C("dataset.npz") + ", " + C("rides.json") + ", " + C("trips/*.bin"), C("feature_order.json")),
     ul("엑셀에서 열 수 있게 표로 풀기", "피처마다 빈 비율과 이유"),
     ul(C("<데이터셋 버전>/csv/") + ": rides.csv, dataset_10s.csv (약 155 MB), features.csv, trips_100hz/ride_000X.csv")],
]

# ------------------------------------------------------------------ 결과 버전
VERSION_ROWS = [
    [C("v001"), "데이터셋 + 모델 + 실차 (보관)", "2", "–", "T-24 피처 v2 실험: oman_personal = oman_score × 민감도 추가, mssq·sensitivity 입력 끔. " + C("code.patch") + "로 코드도 보관", DROP],
    [C("v002"), "dataset", "1", "–", "T-23 보정 시뮬레이터, 286,788행", "v1 기준"],
    [C("v003"), "models", "1", "v002", "T-24 학습 설정. 문턱 0.53 / 0.51 / 0.59", "v1 기준"],
    [C("v004"), "real-baton", "1", "–", "BATON-Sample 변환", "v003 비교용"],
    [C("v005"), "real-comma2k19", "1", "–", "comma2k19 Chunk 1 변환", "v003 비교용"],
    [C("v006"), "dataset", "3", "–", "이벤트 히스테리시스 (T-07), 나머지는 v002와 같음", "v3 기준"],
    [C("v007"), "models", "3", "v006", "T-24 설정 그대로. 문턱 0.53 / 0.51 / 0.59", "v3 기준"],
    [C("v008"), "real-baton", "3", "–", "BATON-Sample, 피처 v3", "v007 비교용"],
    [C("v009"), "real-comma2k19", "3", "–", "comma2k19 Chunk 1, 피처 v3", "v007 비교용"],
    [C("v010"), "dataset", "4", "–", "피처 v4 실험: sensitivity 끔, +1 제약을 mssq로", DROP],
    [C("v011") + "~" + C("v013"), "models, real", "4", "v010", "피처 v4 실험의 모델과 실차", DROP],
    [C("v015") + ", " + C("v016"), "dataset", "5", "–", "빌드가 끝나기 전에 멈춤 (dataset.npz 없음)", DROP],
    [C("v017"), "dataset", "5", "–", "피처 v5: 25개 삭제 (C21), 탑승·라벨은 v006과 같음", "현재"],
    [C("v018"), "models", "5", "v017", "T-24 설정 그대로. 문턱 0.46 / 0.47 / 0.54", '<span class="tag ok">CURRENT</span>'],
    [C("v019"), "real-baton", "5", "–", "BATON-Sample, 피처 v5", "현재"],
    [C("v020"), "real-comma2k19", "5", "–", "comma2k19 Chunk 1, 피처 v5", "현재"],
    [C("v011"), "models", "4", "v010", "시뮬 PR k4 0.524, k5 0.252. 실차 BATON k5·k6, comma k4 나빠져 채택 안 함", DROP],
    [C("v012"), "real-baton", "4", "–", "v011 비교용 BATON-Sample", DROP],
    [C("v013"), "real-comma2k19", "4", "–", "v011 비교용 comma2k19 Chunk 1", DROP],
]

# ------------------------------------------------------------------ 고도화 이력
UPGRADE_ROWS = [
    ["T-22<br>시작 과도", DONE,
     "Wf 필터와 0.63 Hz 저역통과를 0에서 시작해, 중력 9.81이 계단 입력이 됨. 주행 첫 1분 rms_z가 1 m/s² 안팎으로 튐",
     ul("필터 상태를 첫 샘플의 정상상태로 시작", "C++ " + C("Biquad::prime") + ", Python " + C("wf.weight") + ", " + C("oman.input_signal(prime=True)"), "테스트 " + C("test_start_has_no_transient")),
     ul("입력: trip.bin (시뮬레이터, 실차)", "출력: 데이터셋 · 모델 다시 만듦"),
     ul("첫 1분 rms_z_1m 중앙값 1.02 → 0.005", "Pi에서 v007로 AC-01, AC-02 다시 통과 (2026-11-17)")],
    ["T-23<br>시뮬레이터 보정", DONE + " 1차",
     "시뮬레이터 운전이 실제보다 훨씬 거칠었음 (rms_x 2배, 급제동 2배, 고속도로 없음, 노면·차로 흔들림 없음). 그 위에서 고른 문턱은 실제에 맞지 않음",
     ul("BATON-Sample 사람 운전을 목표로 " + C("sim_config.json") + " 7항목 수정", "고속도로 구간 추가 (8%, 90~110 km/h)", "가감속 1.0~2.5 → 0.7~2.0 / 0.5~1.6 m/s², 저크 LP 0.5 → 0.3 Hz, 정차 최대 45 → 120 s", "노면 굴곡 (경사 AR(1)), 차로 유지 흔들림 (0.05~0.5 Hz) 추가"),
     ul("입력: BATON-Sample, comma2k19 (" + C("build_real.py") + "), 작은 시뮬 데이터셋", "처리: " + C("calibrate.py") + " (피처 15개 KS)", "출력: 고친 " + C("sim_config.json") + " → 새 데이터셋 · 모델"),
     ul("평균 KS 0.38 → 0.14", "P(≥4) 0.374 → 0.209 (실차 0.203)", "문턱 0.63 / 0.65 / 0.77 → 0.54 / 0.46 / 0.53", "BATON k4 평균 예측 0.333 → 0.231 (실제 0.203)")],
    ["T-24<br>학습 규제", PART,
     "드문 k (P(≥5) 6%, P(≥6) 1%)에서 조기 종료가 너무 일러 k5 · k6 모델이 트리 4개, 1개뿐인 거의 빈 모델",
     ul("잎 7, 잎당 최소 1,000행, L2 10, 학습률 0.03", "최소 100회 (조기 종료가 그 전에 멈추지 못하게)", C("ml/train/common.py")),
     ul("입력: v002 데이터셋", "출력: v003 모델 · 문턱"),
     ul("트리 4, 1 → 100, 102개", "k5 PR-AUC 0.186 → 0.203 (Oman만 0.202)", "k6 0.062 → 0.055 (Oman만 0.086, 남은 문제)", "문턱 0.53 / 0.51 / 0.59")],
    ["T-24 실험<br>곱 피처 (v2)", DROP,
     "시뮬레이터 안에서는 oman_score × 민감도 하나가 모델보다 나았음 (PR k4 0.549 대 0.499)",
     ul("피처 77번 oman_personal = oman_score × sensitivity 추가", "mssq · sensitivity를 모델 입력에서 끔 (사람 ID로 외우는 것 막기)", "feature_order version 2"),
     ul("입력: 같은 시뮬레이터 설정", "출력: " + C("v001") + "에 데이터셋 · 모델 · 실차 · 코드 보관"),
     ul("시뮬 PR k3~k6 0.859/0.499/0.203/0.055 → 0.867/0.520/0.241/0.075", "BATON PR k4 0.52 → 0.48, k5 0.39 → 0.26", "TU Delft PR k3 0.80 → 0.76", "AC-04 기본 프로필 0.481 → 0.471 (기준 0.472 미달) → 되돌림")],
    ["T-07<br>이벤트 히스테리시스", DONE,
     "가속도가 기준 (−0.75 m/s² 등) 근처에서 오르내리면 제동 한 번을 여러 번 셈",
     ul("기준을 넘으면 1번 세고, 기준에서 0.25 m/s² 되돌아와야 다시 셈 (" + C("rearm_margin_mps2") + ")", "C++ " + C("features.cpp") + ", 테스트 " + C("test_event_hysteresis"), "feature_order version 3 (피처 목록은 v1과 같음)"),
     ul("입력: 같은 시뮬레이터 설정, 같은 실차 원자료", "출력: v006 데이터셋, v007 모델 (CURRENT), v008 · v009 실차"),
     ul("hard_brake_count_5m p90: 시뮬 9 → 8, BATON 12 → 10, comma2k19 중앙값 2 → 1", "모델 지표 변화 ±0.01 안, 문턱 그대로", "TU Delft는 변화 없음 (3.3초마다 실제로 세게 감속하는 자극이라)")],
    ["결과 버전 관리", DONE,
     "v2 실험이 v1 데이터셋 · 모델을 덮어써서, 되돌리려면 전부 다시 만들어야 했음",
     ul(C("ml/runs.py") + ": 실행마다 " + C("ml/out/runs/vNNN") + "을 새로 만듦 (이미 있으면 실패)", C("run.json") + "에 종류, 입력 버전, git 커밋, 파라미터, 설명", "배포는 " + C("runs.py use vNNN") + "으로만"),
     ul("입력: 모든 생성 스크립트 (build_dataset, train, build_real)", "출력: 버전 폴더, " + C("CURRENT") + ", " + C("controller/config/thresholds.json")),
     ul("v001~v020 (아래 표)", "리포트는 입력 버전 폴더 안에 씀 (compare/, verify/, parity/, csv/)")],
    ["C21<br>피처 v5 정리", DONE,
     "77개 중 25개는 차에 들어올 출처도 없고 시뮬레이터 라벨에 닿는 경로도 없었음 (페달, 회생·배터리, 내비, 소음, 설정 온도 등). " + C("pred_ax/ay_10s") + "는 정답을 미리 보여 주는 칸",
     ul("25개를 " + C("feature_order.json") + "에서 지움 (enabled=false가 아니라 삭제, 번호 다시 매김)", "C++ " + C("features.cpp") + "에서 G3a·G3b·G4·G7 계산 삭제, 주행 파일 meta에 " + C("feature_version"), "원시 칸과 시뮬레이터 신호는 그대로"),
     ul("입력: 같은 탑승·라벨 (남은 52개 값은 v006과 완전히 같음)", "출력: v017 데이터셋, v018 모델 (CURRENT), v019 · v020 실차"),
     ul("시뮬 PR k3 0.860 → 0.860, k4 0.496 → 0.523, k5 0.210 → 0.246, k6 0.056 → 0.051", "BATON k4 ROC 0.82 → 0.85, comma2k19 0.88 → 0.95", "TU Delft k4 ROC 0.67 → 0.64 (oman_score 하나와 같아짐), 사람 순위 Spearman 0.40 → 0.19")],
]

STAGE_ROWS = [
    ["T-22 뒤 (보정 전)", "0.941 / 0.710", "0.63 / 0.65 / 0.77", "81% · 0.88/h", "0.86 / 0.64", "0.92 / 0.44", "–"],
    ["T-23 보정", "0.856 / 0.504", "0.54 / 0.46 / 0.53", "84% · 1.12/h", "0.81 / 0.55", "0.84 / 0.23", "–"],
    ["T-24 규제 (v003)", "0.859 / 0.499", "0.53 / 0.51 / 0.59", "83% · 1.11/h", "0.82 / 0.52", "0.89 / 0.37", "0.67 / 0.62"],
    ["v2 실험 (v001, 채택 안 함)", "0.867 / 0.520", "–", "–", "– / 0.48", "–", "– / 0.61"],
    ["T-07 히스테리시스 (v007)", "0.860 / 0.496", "0.53 / 0.51 / 0.59", "84% · 1.12/h", "0.82 / 0.52", "0.88 / 0.38", "0.67 / 0.62"],
    ["C21 피처 v5 (v018)", "0.860 / 0.523", "0.46 / 0.47 / 0.54", "85% · 1.16/h", "0.85 / 0.59", "0.95 / 0.66", "0.64 / 0.61"],
]

# ------------------------------------------------------------------ 식과 근거
FORMULA_ROWS = [
    ["Oman 멀미 모델<br>" + C("ml/oman.py") + ", P2",
     C("c = √(ã_x² + ã_y²)") + "<br>" + C("S = LP2(c, β_s)") + "<br>" + C("F = LP2(K·S·c, β_f)") + "<br>" + C("score = (F + S)^0.4"),
     "β_s 502.4 s, β_f 66.2 s, 지수 0.4. LP2 = 1차 저역통과 두 번, α = e^(−Δt/β)",
     LIT + " " + R("oman1990", "Oman 1990") + " (구조), " + R("irmak2022", "Irmak 등 2022") + " (집단 적합 시간상수, 정류 입력 c)"],
    ["Oman 입력",
     "주파수 가중 없음. 축마다 " + C("ã = LP_0.63Hz(a)") + " (2차 버터워스)",
     "0.63 Hz",
     LIT + " 가중 없음: " + R("donohew2004", "Donohew & Griffin 2004") + " (" + R("irmak2021", "Irmak 2021") + " 재인용) · " + OWN + " 0.63 Hz는 " + R("iso2631", "ISO 2631-1") + " Wf의 f2를 빌림 (노면 진동이 정류에서 양수 치우침이 되는 것을 막음)"],
    ["기준 이득 K0",
     "사람마다 K 하나를 1분 MISC MAE 최소로 적합 → 17명 중앙값",
     "K0 51.4 (필터 없이 48.7, MAE 0.97)",
     FIT + " " + R("atdoms", "ATDOMS") + " (" + R("irmak2022", "Irmak 2022") + " 실험 데이터)"],
    ["시뮬레이터 라벨 K",
     C("K = K0 · true_sens · m") + "<br>" + C("true_sens = clip(sens · e^η, 0.1, 10), η ~ N(0, 0.65²)"),
     "η 0.65, m = 좌석·온도·풍량 배수",
     FIT + " η: ATDOMS에서 MSSQ로 설명 안 되는 퍼짐 · " + LIT + " 배수 방향: " + R("turner1999", "Turner & Griffin 1999") + ", " + R("schmidt2020", "Schmidt 2020") + " (뒷자리), " + R("arnold2019", "Arnold 2019") + " (온도), " + R("damour2017", "D'Amour 2017") + " (풍량), " + R("gu2025", "Gu 2025") + " (소음 0으로 둔 이유) · " + OWN + " 크기"],
    ["MISC 척도와 라벨",
     C("y_k = max MISC(t+10 s … t+300 s) ≥ k") + ", k = 2..6",
     "1분 단위 잡음 N(0, 0.5), 반올림",
     LIT + " MISC 0~10: " + R("bos2005", "Bos 등 2005") + " · " + PRD + " 5분 창, k 2..6 (PRD §4.1) · " + OWN + " 잡음을 1분 동안 같은 값으로 (C5)"],
    ["ISO Wf 필터<br>P2 RMS, jerk, MSDV",
     C("Wf = Hh · Hl · Ht · Hs") + "<br>쌍선형 변환 100 Hz, 첫 샘플 정상상태로 시작",
     "f1 0.08, f2 0.63, f4 0.25 (Q 0.86), f5 0.0625, f6 0.1 Hz",
     LIT + " " + R("iso2631", "ISO 2631-1:1997") + " 부속서 A · " + OWN + " 수평 x, y에도 적용 (ISO는 z축용, T-06), 시작 상태 (T-22)"],
    ["MSDV",
     C("msdv(t) = √(Σ h² Δt)") + ", " + C("h = √(a_w,x² + a_w,y²)"),
     "m/s^1.5, 주행 시작부터 누적",
     LIT + " " + R("iso2631", "ISO 2631-1") + ", " + R("lawther1987", "Lawther & Griffin 1987") + " · " + PRD + " 수평 h ([추측], ISO는 z축)"],
    ["MSSQ → 민감도<br>G10, P3 기본 행 (설문값 그대로)",
     C("z = (MSSQ − 12.90) / 9.90") + "<br>" + C("sens = clip(e^(0.3 z), 0.5, 2.0)"),
     "규준 12.90 / 9.90 (n = 257), 기본 프로필 12.90 / 1.0",
     LIT + " 채점과 규준: " + R("golding2006", "Golding 2006") + ", " + R("mssq", "MSSQ-Short") + " · " + PRD + " 계수 0.3, 범위 0.5~2 (근거 문헌 없음, T-09)"],
    ["급가속 · 급제동 · 급회전 횟수<br>피처 19~25",
     "ã_x > 1.5, ã_x < −0.75, |ã_y| > 2.0을 넘는 순간 1회. 기준에서 0.25 되돌아와야 다시 셈",
     "1.5 / −0.75 / 2.0 m/s², 되돌림 0.25",
     LIT + " 1.5, −0.75: " + R("he2013", "He 등 2013") + ", 2.0: " + R("nguyen2019", "Nguyen 등 2019") + " (둘 다 승차감 불편 문턱, 멀미 문턱 아님) · " + OWN + " 되돌림 폭 (T-07)"],
    ["휴식 · 재노출 · 탄 시간<br>피처 68~70",
     "차속 0 (없으면 h < 0.05)이 180 s 연속이면 휴식. 그 뒤 움직이면 reexposure = 1",
     "180 s, 0.05 m/s²",
     PRD + " PRD §P1 · " + LIT + " 개념: " + R("golding1997", "Golding & Stott 1997") + " (회복), " + R("lawther1987", "Lawther & Griffin 1987") + " (노출 시간)"],
    ["순서형 위험도<br>S4, P3",
     "이진 분류기 5개로 P(MISC ≥ k). " + C("p_k ← min(p_k, p_(k−1))"),
     "단조 제약 +1 피처 9개",
     LIT + " 누적 이진 분해: " + R("frank2001", "Frank & Hall 2001") + ", 모델: " + R("ke2017", "LightGBM (Ke 등 2017)") + " · " + PRD + " 단조 피처 목록 (PRD §4.2)"],
    ["Tier 문턱",
     "학습 쪽 15% 사람 재생에서 Tier 1 탐지율 ≥ 0.85, Tier 2 오경보 ≤ 0.5/h, Tier 3 ≤ 0.25/h를 만족하는 가장 큰 문턱",
     "0.53 / 0.51 / 0.59",
     PRD + " 목표값 (AC-05, T-12) · " + OWN + " 재생으로 고르는 방식"],
    ["허용도 갱신<br>P4",
     C("tol_d = 0.4 · tanh(n_d / 6)") + ", n_d ≥ −4<br>민감도는 바꾸지 않음 (사람별 반영은 개인 보정)",
     "hold 1 / 6 / 12주기, off 0.10 · 6주기, 쿨다운 300 s",
     PRD + " PRD §P4 (근거 문헌 없음)"],
    ["실차 검증 데이터",
     "주행만 실제, 승객과 라벨은 시뮬레이터와 같은 방식",
     "BATON 19.1 h, comma2k19 2.9 h",
     R("baton", "BATON (Hugging Face)") + ", " + R("comma2k19", "comma2k19 (Schafer 등 2018)")],
]

RUN_STEPS = [
    ["1", C("python3 ml/simulator/build_dataset.py --note \"…\""), "sim_config.json, constants.json, feature_order.json", C("v017/") + " dataset.npz, rides.json, dataset_report.json, trips/"],
    ["2", C("python3 ml/train/train.py --note \"…\""), "최신 데이터셋 (v017)", C("v018/") + " models/, thresholds.json, train/report.md"],
    ["3", C("python3 ml/runs.py use v018"), "v018", C("ml/out/runs/CURRENT") + ", " + C("controller/config/thresholds.json")],
    ["4", C("python3 ml/realdata/build_real.py --source baton --root ml/data/raw/baton --note \"…\""), "BATON 원자료", C("v019/real_baton.npz")],
    ["5", C("python3 ml/realdata/build_real.py --source comma2k19 --root ml/data/raw/comma2k19/chunk1 --keep-trips --note \"…\""), "comma2k19 원자료", C("v020/real_comma2k19.npz") + ", trips/real/"],
    ["6", C("python3 ml/realdata/compare.py --source baton") + "<br>" + C("python3 ml/realdata/compare.py --source comma2k19"), "CURRENT 모델, 최신 실차, v017", C("v018/compare/*.md, *.json")],
    ["7", C("python3 ml/verify/tudelft_replay.py ml/data/raw/ATDOMS.mat"), "ATDOMS, CURRENT 모델", C("v018/verify/tudelft_replay.md")],
    ["8", C("python3 ml/verify/model_parity.py make") + " → Pi " + C("verify_model") + " → " + C("check"), "CURRENT 모델", C("v018/parity/result.json") + " (v007까지 Pi에서 함)"],
    ["9", "Pi: " + C("controller/qnx/run_pipeline.sh") + " → " + C("pipeline_check.py check"), "CURRENT 모델 (tar로 복사)", "cycles.csv, 비교 JSON"],
]

RUNTIME_ROWS = [
    ["P1 acq<br>" + OK, C("p1_acq <trip.bin> [--speed S] [--timeout-ms 5] [--prio 40]"),
     ul("trip.bin (32열, 100 Hz)", "LIVE: MPU-6050 I²C, CAN " + NO),
     ul("→ P2 SAMPLE {seq, Row[32]} 100 Hz, 제한 5 ms", "→ P2 RIDE_START / RIDE_END {samples}, 제한 1 s"),
     "stdout JSON: samples, send_fail, wall_s, 깨어남 늦음 중앙값·최대·1 ms 초과",
     "186,518개 보냄 · 실패 0 · 늦음 중앙값 953 µs, 최대 993 µs"],
    ["P2 feat<br>" + OK, C("p2_feat [--once] [--prio 30]"),
     ul("← P1 SAMPLE, RIDE_*"),
     ul("→ P3 VEH {seq, t, t_emit_ns, x[46]} 10초마다, 제한 1 s", "→ P3 RIDE_* 그대로", "→ P6 MOTION 10 Hz (" + C("--motion") + ")"),
     "stdout JSON: received, seq_gaps, veh_sent, veh_fail, push_us_max",
     "받음 186,518 · 빠짐 0 · VEH 186 · 받기 처리 최대 2.5 ms"],
    ["P3 infer<br>" + OK, C("p3_infer --models DIR [--vehicle-type 0|1] [--once] [--prio 20]"),
     ul("← P2 VEH, RIDE_*", C("runs/v018/models/model_2~6.txt"), "← P4 ProfilesReply"),
     ul("→ P4 PROFILES 요청 (주기마다)", "→ P4 RISK {seq, t, t_emit_ns, t_infer_ns, n, id[n], p[n][P≥2..6]}, 제한 1 s", "폰 없으면 \"default\" 한 행: MSSQ 12.90, sens 1.0, EV NaN"),
     "stdout JSON: cycles, profiles_fail, risk_fail, infer_ms_max",
     "주기 186 · 실패 0 · 추론 최대 0.55 ms (폰 2대)"],
    ["P4 policy<br>" + OK, C("p4_policy --config DIR --state DIR [--log FILE] [--hud] [--phone ID:MSSQ:SENS0[:EV]]... [--once] [--prio 15]"),
     ul("← P3 PROFILES, RISK", "← P2→P3→P4 RIDE_*", C("policy.json") + ", " + C("thresholds.json") + " (feature_order_version이 빌드와 다르면 시작 안 함)", C("state/") + " (시작 때 읽음)", "CTX_SCREEN · CTX_OVR · CTX_PROF ← P6 (" + C("--rf") + ")"),
     ul("→ P3 ProfilesReply", "stdout " + C("CMD <t> <기기> <tier> <on>"), "→ P5 DEV_SYNC (회신 = 명부), TIER_CMD (" + C("--rf") + ")"),
     ul(C("cycles.csv") + ": seq, t, latency_ms, infer_ms, phone, p2..p6, 기기마다 0/1", C("state/tolerance/<기기>.json") + " {n}", C("state/profiles/<ID>.json") + " {mssq, sens}"),
     "지연 중앙값 0.57 ms · 최대 2.03 ms · RISK 빠짐 0"],
    ["P5 기기 관리<br>" + OK, C("p5_devmgr --state DIR [--safety-s 30]"),
     ul("← P6 DEV_HELLO / DEV_BYE", "← P4 DEV_SYNC, TIER_CMD"),
     ul("→ P4 DevSyncReply (명부)", "→ P6 OUT_INT, OUT_STREAM, OUT_PROF, OUT_DROP", "DEV_SYNC 30 s 없으면 전부 OFF"),
     "로그 (safe off)", "–"],
    ["P6 통신<br>" + OK, C("p6_comm [--dev /dev/ser3]") + " 115,200 8N1",
     ul("UART " + C("<id>|CONNECT · DISCONNECT · JSON"), "← P2 MOTION, ← P5 OUT_*"),
     ul("→ P5 DEV_HELLO, DEV_BYE", "→ P4 CTX_SCREEN, CTX_OVR, CTX_PROF", "UART " + C("<id>|JSON") + ", MOT 100 ms, " + C("ALL|HB") + " 1 s, " + C("<id>|DROP")),
     "–", "–"],
]

FORMAT_ROWS = [
    [C("ml/out/runs/vNNN/run.json"), "JSON", "version, kind (dataset / models / real-…), created, git {commit, dirty}, feature_order_version, note, inputs {dataset: …}, params", "모든 생성 스크립트"],
    [C("ml/out/runs/CURRENT"), "한 줄 텍스트", "배포 중인 모델 버전 (지금 v018)", C("runs.py use") + " → run_pipeline.sh, 리포트 기본값"],
    [C("vNNN/trips/ride_XXXX.bin"), "float64 LE. 머리말 [version=1, n_columns=32, 100] + 100 Hz 행", "t, ax, ay, az (중력 포함, +9.81), gx, gy, gz, speed, accel_pedal, brake_pedal, drive_mode, cruise_on, regen_decel, regen_level, one_pedal, soc, batt_power, motor_torque, dist_next_turn, angle_next_turn, pred_ax_10s, pred_ay_10s, curvature_ahead, cabin_temp, set_temp, fan_level, ac_on, seat_row, seatback_angle, window_open, noise_level, noise_lowfreq_level. 신호가 없으면 그 열 전체 NaN", "S3, build_real → P1, featurize"],
    ["features.bin", "float64. 머리말 [feature_version, 47] + 10초마다 [t, 46개]", "차량 피처 0~45 (G1, G2, G5, G6, G9). feature_version = 5", "featurize → build_dataset, build_real, tudelft_replay"],
    [C("v017/dataset.npz"), "NumPy (allow_pickle=False로 읽음)", "X 286,788×52, y 286,788×5 (k=2..6), t, misc, score, future_max, ride_id, trip_id, person_id, k, feature_names, feature_version", "S3 → S4, compare, CSV"],
    [C("v008/real_baton.npz"), "NumPy", "dataset.npz와 같은 칸 + tag_das (운전 보조 켜짐 여부, 사람 운전 행만 고를 때)", "build_real → compare, calibrate"],
    [C("v018/models/model_k.txt"), "LightGBM 4.6.0 텍스트 모델", "objective binary, 52 입력, 단조 제약 7개. models.json에 트리 수, sha256", "S4 → P3, AC-01, compare, tudelft_replay"],
    [C("v018/thresholds.json") + " → " + C("controller/config/thresholds.json"), "JSON", "base_th {1: 0.46, 2: 0.47, 3: 0.54}, 고른 규칙, feature_order_version 5, 배포 쪽에는 run: v018", "S4 → runs.py use → P4"],
    [C("controller/config/policy.json"), "JSON", "tiers (기기, input_k, shared, hold 1/6/12, gate), off_margin 0.10, off_hold 6, cooldown 300 s, quick_off 30 s, max_quick_offs 3, tolerance {0.4, 6, −4}, safety_timeout 30 s", "사람 → P4, 학습 재생"],
    [C("state/tolerance/<기기>.json"), "JSON, 임시 파일 → fsync → rename → 디렉터리 fsync", "{\"n\": 정수}", "P4 ↔ 디스크"],
    [C("state/profiles/<ID>.json"), "JSON, 같은 방식", "{mssq, sens} (%.17g), 앱 설문 그대로. 옛 형식의 sens_mssq는 설문값으로 읽음. EV 경험, 차량 유형은 아직 없음 (T-17)", "P4 ↔ 디스크"],
    [C("cycles.csv"), "CSV, 주기 × 폰마다 한 줄", "seq, t, latency_ms, infer_ms, phone, p2, p3, p4, p5, p6, airflow-01, display-01, phone-0 …", "P4 → AC-02"],
    [C("vNNN/parity/vectors.bin"), "float64 행렬", "4,202 × 피처 수 시험 벡터 (v007은 77). expected_p.bin, actual_pi.bin은 4,202 × 5 확률", "AC-01"],
]

MSG_ROWS = [
    ["SAMPLE 0x8001", "P1 → P2", "100 Hz", "seq (u64), Row (double × 32)", "회신 없음 (빈 회신으로 풀어 줌)"],
    ["RIDE_START 0x8002 / RIDE_END 0x8003", "P1 → P2 → P3 → P4", "주행마다", "samples (u64, END에 보낸 개수)", "각 단계 상태 초기화 / 요약 출력"],
    ["VEH 0x8004", "P2 → P3", "10초", "seq, t (s), t_emit_ns, x (double × 46)", "–"],
    ["PROFILES 0x8005", "P3 → P4", "10초", "요청은 머리말만", "ProfilesReply {n, tol_display, e[9]: id[64], mssq, sens, ev, tol}"],
    ["RISK 0x8006", "P3 → P4", "10초", "seq, t, t_emit_ns, t_infer_ns, n, id[9][64], p[9][5]", "–"],
]

PARAM_ROWS = [
    ["K0 51.4, β 66.2 / 502.4 s, 지수 0.4, 0.63 Hz 입력 LP", "S1 oman_tudelft.py + " + R("irmak2022", "Irmak 2022") + " → " + C("constants.json"), "시뮬레이터 라벨, featurize, P2 Oman 피처"],
    ["ISO Wf 계수, 이벤트 1.5 / −0.75 / 2.0과 되돌림 0.25, 휴식 180 s · 0.05", C("constants.json") + " (" + R("iso2631", "ISO 2631-1") + ", " + R("he2013", "He 2013") + ", " + R("nguyen2019", "Nguyen 2019") + ")", "featurize, P2"],
    ["피처 52개 순서, 단위, 단조, 드롭아웃, enabled, version 5", C("feature_order.json"), "build_dataset, 학습, P2, P3, 문턱 파일 검사"],
    ["MSSQ → sens = clip(e^(0.3z), 0.5, 2), 기본 프로필 12.90 / 1.0", C("constants.json") + " (" + R("golding2006", "Golding 2006") + ")", "시뮬레이터 승객, P3 기본 행, 앱 설문"],
    ["η 0.65, 환경 배수, 라벨 잡음 0.5", "S1 + " + C("sim_config.json") + ", " + C("sim_effects.json"), "시뮬레이터 라벨만"],
    ["운전 · 도로 분포 (고속도로, 가감속, 노면, 차로 흔들림)", "T-23 " + C("calibrate.py") + " → " + C("sim_config.json"), "시뮬레이터 운동"],
    ["model_2~6.txt", "S4 → " + C("v018/models/"), "P3, AC-01, 실차 비교, TU Delft 재생"],
    ["base_th 0.46 / 0.47 / 0.54", "S4 재생 → " + C("v018/thresholds.json") + " → " + C("runs.py use"), "P4, AC-02, TU Delft Tier 리드"],
    ["hold, off, 쿨다운, 허용도 식", C("policy.json") + " (PRD 설계)", "P4, 학습 재생"],
    ["n[d] → tol = 0.4·tanh(n/6)", "P4 (승객 수동 조작, P6의 CTX_OVR)", "P4 문턱. P3 G8 입력은 꺼짐"],
    ["화면 ON · 응시 (SCR)", "앱, HUD " + NO + " · 지금은 " + C("--hud"), "P4 게이트"],
]


def refs_list():
    items = "".join(f'<li><a href="{u}" target="_blank" rel="noopener">{E(t)}</a></li>' for t, u in REFS.values())
    return f'<ol class="refs">{items}</ol>'


def page():
    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MerMaid 워크플로우 구조</title>
<style>{CSS}</style>
</head>
<body>
<main>
<h1>MerMaid 워크플로우 구조</h1>
<p class="sub">2026-11-24 기준. 피처 순서 version 5, 배포 모델 <code>v018</code>. 시뮬레이션 → 학습 → 검증의 모든 입력과 출력, 지금까지 고친 것, 식과 근거 문헌. 자세한 정의는 <a href="SPEC.md">docs/architecture/SPEC.md</a>, 실차 비교는 <a href="../ml/REALDATA.md">docs/ml/REALDATA.md</a>.</p>
<nav>
<a href="#overview">1. 전체 그림</a><a href="#offline">2. 시뮬레이션 → 학습</a><a href="#validation">3. 검증 · 보정</a><a href="#stages">4. 단계별 입출력</a>
<a href="#versions">5. 결과 버전</a><a href="#upgrades">6. 고도화 이력</a><a href="#formulas">7. 식과 근거</a><a href="#runtime">8. Pi 런타임</a>
<a href="#cycle">9. 10초 한 주기</a><a href="#vector">10. 52개 벡터</a><a href="#formats">11. 파일 형식</a><a href="#params">12. 파라미터 흐름</a><a href="#todo">13. 기기 연결</a><a href="#refs">참고 문헌</a>
</nav>
<div class="legend">
<span><i class="sw" style="background:var(--pc-soft);border-color:var(--pc)"></i>개발 PC 처리</span>
<span><i class="sw" style="background:var(--pi-soft);border-color:var(--pi)"></i>Pi 프로세스 (구현)</span>
<span><i class="sw" style="background:var(--cfg-soft);border-color:var(--cfg);border-style:dashed"></i>설정 · 사람이 옮겨 적는 값</span>
<span><i class="sw" style="background:var(--card);border-color:var(--pi)"></i>출력 파일</span>
<span><i class="sw" style="background:var(--file);border-color:var(--line)"></i>원자료 · 입력</span>
<span><i class="sw" style="background:var(--todo-soft);border-color:var(--todo);border-style:dashed"></i>미구현</span>
</div>

<h2 id="overview">1. 전체 그림</h2>
<p>개발 PC가 시뮬레이터로 학습 데이터를 만들고, 모델 5개와 문턱을 학습한 뒤, 실차 주행과 실제 사람 MISC로 검증합니다. 결과는 모두 버전 폴더에 쌓이고, 배포할 버전 하나를 골라 Pi에 복사합니다. 실행 중에는 Pi 혼자 10초마다 위험도를 계산해 기기를 켜고 끕니다. 폰과 HUD는 ESP32 브리지를 거쳐 P6·P5로 붙습니다 (P4 <code>--rf</code>). 이 경로는 구현과 시뮬레이션 시험까지 끝났고, 실제 기기를 Pi에 붙인 시험은 아직입니다. BLE 없이 돌릴 때는 P4 실행 인자 <code>--phone</code>, <code>--hud</code>가 등록을 대신합니다.</p>
<div class="fig">{overview()}</div>

<h2 id="offline">2. 시뮬레이션 → 학습 → 배포</h2>
<p>왼쪽에서 오른쪽으로 한 번 돌리면 새 데이터셋 버전(v017)과 새 모델 버전(v018)이 생깁니다. 모든 단계가 <code>protocol/</code>의 계약 파일을 읽습니다. 시뮬레이터 정답(MISC)은 Oman 식으로 만들므로, 이 그림 안의 성능은 "구조가 의도대로 도는가"의 근거이고 실제 정확도는 3장에서 따로 봅니다.</p>
<div class="fig">{offline()}</div>

<h2 id="validation">3. 검증 · 보정 루프</h2>
<p>시뮬레이터 밖의 자료 세 가지로 확인합니다. 실차 두 개(BATON, comma2k19)는 운동 분포와 확률 수준을 보고, 시뮬레이터 설정을 고치는 기준(T-23)이 됩니다. 실차 신호와 사람 MISC가 함께 있는 공개 데이터는 없어서, 실제 사람 MISC는 TU Delft 실험을 가상 주행으로 바꿔 시험합니다.</p>
<div class="fig">{validation()}</div>

<h2 id="stages">4. 단계별 입력 · 처리 · 출력</h2>
<p>vNNN은 <code>ml/out/runs/vNNN/</code>입니다. 명령은 저장소 루트에서 실행합니다.</p>
{table(["단계", "상태 · 실행", "입력", "처리", "출력"], OFFLINE_ROWS)}
<h3>처음부터 다시 만드는 순서</h3>
<p>지금 버전(v017~v020)은 이 순서로 만들었습니다. 각 단계가 새 버전 번호를 받으므로, 실제 번호는 실행할 때 다를 수 있습니다.</p>
{table(["#", "명령", "읽는 것", "만드는 것"], RUN_STEPS)}

<h2 id="versions">5. 결과 버전 (<code>ml/runs.py</code>)</h2>
<p>데이터셋, 모델, 실차 데이터셋은 덮어쓰지 않습니다. 만드는 스크립트가 실행마다 새 폴더를 만들고(이미 있으면 실패), 리포트는 입력이 된 버전 폴더 안 하위 폴더(<code>compare/</code>, <code>verify/</code>, <code>parity/</code>, <code>csv/</code>)에 씁니다. 배포할 모델은 <code>python3 ml/runs.py use vNNN</code>으로만 바꿉니다. 목록은 <code>python3 ml/runs.py list</code>.</p>
{table(["버전", "종류", "피처 순서", "입력", "내용", "상태"], VERSION_ROWS)}

<h2 id="upgrades">6. 고도화 이력</h2>
<p>첫 구현 뒤 고친 것을 순서대로 적었습니다. 매번 무엇이 문제였고, 무엇을 바꿨고, 어떤 데이터가 들어가서 무엇이 나왔고, 숫자가 어떻게 바뀌었는지입니다.</p>
{table(["항목", "상태", "문제", "한 일", "입력 → 출력", "결과"], UPGRADE_ROWS)}
<h3>단계별 지표</h3>
<p>시뮬 CV는 사람 단위 5-fold PR-AUC. BATON은 사람 운전 행, comma2k19는 전체, TU Delft는 실제 사람 MISC입니다. 시뮬 PR이 T-23에서 크게 떨어진 것은 양성 비율이 0.37에서 0.21로 줄었기 때문이고(지나치게 거친 운전을 고친 결과), 성능이 나빠진 것이 아닙니다.</p>
{table(["단계", "시뮬 CV PR k3 / k4", "문턱 Tier 1 / 2 / 3", "Tier 1 탐지 · 오경보", "BATON k4 ROC / PR", "comma2k19 k4 ROC / PR", "TU Delft k4 ROC / PR"], STAGE_ROWS)}

<h2 id="formulas">7. 식과 근거</h2>
<p>근거 표시: {LIT} 논문이나 표준에서 가져옴, {FIT} 데이터로 직접 맞춤, {PRD} 팀 PRD의 설계값 (논문 근거 없음), {OWN} 구현하면서 정함. 링크는 새 창으로 열립니다.</p>
{table(["무엇", "식", "값", "근거"], FORMULA_ROWS)}

<h2 id="runtime">8. Pi 런타임 (S6)</h2>
<p>QNX 프로세스 4개가 메시지 전달(MsgSend / MsgReceive / MsgReply)로 이어집니다. 받는 쪽은 바로 복사하고 회신해서 100 Hz의 P1이 막히지 않습니다. 각 프로세스는 다음 단계에 연결된 뒤에야 자기 이름을 붙여서, 시작 순서는 P4 → P3 → P2 → P1입니다. Pi에는 <code>ml/out/runs/CURRENT</code>, 그 버전의 <code>models/</code>, <code>controller/config/</code>를 tar로 복사합니다 (<code>controller/bringup/README.md</code>).</p>
<div class="fig">{runtime()}</div>
<h3>프로세스별 입력 · 출력</h3>
<p class="cap">측정값은 모델 v007로 Pi에서 실시간(1배속) 재생해 잰 값입니다 (2026-11-17).</p>
{table(["프로세스", "실행", "받는 것", "보내는 것", "쓰는 것", "Pi 측정값 (v006 ride_0001, 186,518 샘플)"], RUNTIME_ROWS)}
<h3>QNX 메시지 (<code>controller/qnx/messages.hpp</code>)</h3>
{table(["타입", "방향", "주기", "내용", "회신"], MSG_ROWS)}

<h2 id="cycle">9. 10초 한 주기</h2>
<p>P2가 1,000번째 샘플을 받는 순간부터 P4가 결정을 쓰기까지의 순서입니다. 숫자는 Pi에서 잰 값입니다.</p>
<div class="fig small">{cycle()}</div>

<h2 id="vector">10. 모델 입력 52개 벡터</h2>
<p>P2가 차량 피처 46개를 만들고, P3가 폰마다 허용도(G8)와 승객 프로필(G10)을 붙입니다. 학습 데이터(<code>dataset.npz</code>의 X)도 같은 순서입니다. 피처 하나하나의 정의는 <a href="SPEC.md#4-피처-52개-현황">SPEC.md 4장</a>.</p>
<div class="fig small">{vector()}</div>

<h2 id="formats">11. 파일 형식</h2>
{table(["파일", "형식", "내용", "만드는 곳 → 쓰는 곳"], FORMAT_ROWS)}

<h2 id="params">12. 파라미터가 어디서 나와 어디로 가는가</h2>
{table(["파라미터", "만드는 곳", "쓰는 곳"], PARAM_ROWS)}

<h2 id="todo">13. 기기 연결 (PRD §5 기준)</h2>
<p>P5·P6, ESP32 브리지, 앱, HUD 모두 구현돼 있습니다 (PR #4, #5). P4·P2와 연결했고, Mac 시험(<code>pipeline_rf_sim</code>)과 Pi의 가상 터미널 브리지 흉내로 확인했습니다. 실제 ESP32·폰·HUD를 Pi에 붙인 시험은 아직입니다. Pi 안의 메시지는 <code>docs/controller/RF_MESSAGES.md</code>, 기기 메시지와 UUID는 <code>protocol/</code>이 기준입니다.</p>
{table(["구간", "물리 · 형식", "내용"], [
    ["Pi P6 ↔ ESP32-S3", "UART3 " + C("/dev/ser3") + " GPIO4/5 (핀 7·29), 115,200 bps 8N1. 구현, Pi 실연결 시험 전", "한 줄 " + C("<id>|<payload>\\n") + ", id = 연결 0~8 또는 ALL, 최대 512 B. 제어 토큰 CONNECT, DISCONNECT (ESP32 → Pi), DROP (Pi → ESP32)"],
    ["ESP32-S3 ↔ 폰 · HUD", "BLE GATT, ESP32 = Peripheral, 광고 " + C("MSAgent-xxxx"), "서비스 " + C("cd820001-…") + ", RX " + C("cd820002") + " (Write Without Response), TX " + C("cd820003") + " (Notify), MTU 247, 최대 9 연결, LE Secure Connections"],
    ["M00 HELLO", "기기 → 컨트롤러", "role (app / hud), dev (영구 ID), v. 연결 후 5초 안에 없으면 DROP"],
    ["M01 INT", "컨트롤러 → 기기", "tier, on"],
    ["M02 MOT", "컨트롤러 → 기기, 10 Hz", "ax, ay (가중 가속도), 경로가 있으면 pax, pay"],
    ["M03 SCR", "기기 → 컨트롤러", "on (화면), att (응시) → P4 게이트"],
    ["M05 OVR", "기기 → 컨트롤러", "dev, on (수동 조작) → P4 허용도, 쿨다운"],
    ["M06 PROF", "앱 ↔ 컨트롤러", "mssq, sens → P4 프로필"],
    ["M07 HB", "양쪽, 1초", "seq. 5초 동안 아무것도 못 받으면 끊김"],
])}

<h2 id="refs">참고 문헌</h2>
<p>이 문서와 <a href="SPEC.md">SPEC.md</a>에서 식, 상수, 데이터의 근거로 쓴 자료입니다. 확인 수준(직접 확인, 초록, 재인용)은 SPEC.md의 참고 자료 표에 있습니다. 피처별 근거 등급은 팀 문서 「근거 없는 피처 50개 근거 자료 통합 v2」.</p>
{refs_list()}
<p class="cap">이 파일은 <code>docs/architecture/ARCHITECTURE.html</code>이고 <code>python3 docs/architecture/make_architecture.py</code>로 다시 만듭니다. 브라우저로 바로 열 수 있고, 외부 파일을 쓰지 않습니다.</p>
</main>
</body>
</html>
"""


OUT.write_text(page(), encoding="utf-8")
print("wrote", OUT, OUT.stat().st_size, "bytes")
