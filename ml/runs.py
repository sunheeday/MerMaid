"""결과물 버전 폴더 (ml/out/runs/vNNN/). 데이터셋, 모델, 실차 데이터셋은 덮어쓰지 않고 실행마다 새 버전을 만든다.

    python3 ml/runs.py list            # 버전, 종류, 설명
    python3 ml/runs.py current         # 배포 중인 모델 버전 폴더 (저장소 기준 경로)
    python3 ml/runs.py current-v1      # V1(LightGBM) 도구의 기본 모델 (배포가 V3여도 V1)
    python3 ml/runs.py dataset         # 그 모델이 학습한 데이터셋 버전 폴더
    python3 ml/runs.py use v003        # 배포할 모델 버전 지정: CURRENT를 바꾸고 그 문턱을 controller/config/thresholds.json에 복사

규칙
- build_dataset.py, train.py, build_real.py는 실행마다 새 vNNN을 만든다. run.json에 종류, 입력, git 커밋, 설명을 남긴다.
- 리포트(compare, tudelft_replay, model_parity, CSV)는 입력이 된 버전 폴더 안 하위 폴더에 쓴다.
  같은 입력이면 같은 내용이라 다시 만들어도 사라지는 정보가 없다.
- 읽을 때 기본값: 모델은 CURRENT (없으면 모델이 있는 최신 버전), 데이터셋은 그 모델 run.json의 inputs.dataset,
  실차 데이터셋은 그 파일이 있는 최신 버전. 스크립트마다 인자로 바꿀 수 있다.
- 모델 종류는 models.json의 kind (없으면 V1 LightGBM "misc5", V3는 ctx_gru / ctx_attn / ctx_xattn).
  V1 전용 도구(train, verify, realdata, 4TU 실험)는 current_v1_models(): 배포가 V1이면 그것, 아니면 마지막으로 배포한 V1
  (CURRENT_V1, use가 V1을 배포할 때 적는다), 그것도 없으면 V1 모델이 있는 최신 버전.
- controller/config/thresholds.json은 배포 설정이라 `use`로만 바뀐다 (git으로 이력 관리).
"""
import datetime
import json
import pathlib
import re
import shutil
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
ROOT = REPO / "ml" / "out" / "runs"
CURRENT = ROOT / "CURRENT"
CURRENT_V1 = ROOT / "CURRENT_V1"
THRESHOLDS = REPO / "controller" / "config" / "thresholds.json"


def versions():
    return sorted(p for p in ROOT.glob("v[0-9][0-9][0-9]") if p.is_dir()) if ROOT.exists() else []


def resolve(v):
    """'v003', 'v3', 3 또는 경로 → 버전 폴더"""
    p = pathlib.Path(str(v))
    if p.is_dir():
        return p.resolve()
    m = re.fullmatch(r"v?(\d+)", str(v))
    if not m or not (ROOT / f"v{int(m.group(1)):03d}").is_dir():
        sys.exit(f"버전 {v} 없음 (python3 ml/runs.py list)")
    return ROOT / f"v{int(m.group(1)):03d}"


def meta(run):
    f = pathlib.Path(run) / "run.json"
    return json.loads(f.read_text()) if f.exists() else {"version": pathlib.Path(run).name}


def _git():
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True, check=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=REPO,
                                    capture_output=True, text=True, check=True).stdout.strip())
        return {"commit": head, "dirty": dirty}
    except (OSError, subprocess.CalledProcessError):
        return {"commit": None, "dirty": None}


def new(kind, note="", inputs=None, params=None):
    """다음 번호로 새 버전 폴더를 만든다 (이미 있으면 실패하므로 덮어쓰지 않는다)."""
    ROOT.mkdir(parents=True, exist_ok=True)
    n = max([int(p.name[1:]) for p in versions()], default=0) + 1
    run = ROOT / f"v{n:03d}"
    run.mkdir()
    fo = json.loads((REPO / "protocol" / "feature_order.json").read_text())
    (run / "run.json").write_text(json.dumps({
        "version": run.name, "kind": kind, "created": datetime.datetime.now().isoformat(timespec="seconds"),
        "git": _git(), "feature_order_version": fo["version"], "note": note,
        "inputs": {k: rel(v) for k, v in (inputs or {}).items()}, "params": params or {}}, indent=1))
    print(f"[runs] new {run.name} ({kind}) -> {rel(run)}", file=sys.stderr)
    return run


def rel(p):
    try:
        return str(pathlib.Path(p).resolve().relative_to(REPO))
    except ValueError:
        return str(p)


def latest_with(name):
    """name(버전 폴더 안 상대 경로)이 있는 가장 최근 버전의 그 파일"""
    for run in reversed(versions()):
        if (run / name).exists():
            return run / name
    sys.exit(f"ml/out/runs 어디에도 {name} 없음 (먼저 만들어야 함)")


def current_models():
    """배포 중인 모델 버전 폴더. CURRENT가 없으면 모델이 있는 최신 버전"""
    if CURRENT.exists():
        return resolve(CURRENT.read_text().strip())
    return latest_with("models/models.json").parents[1]


def model_kind(run):
    """모델 버전의 종류 (models.json kind, 없으면 V1 "misc5"). P3의 model_kind와 같은 규칙"""
    f = pathlib.Path(run) / "models" / "models.json"
    return json.loads(f.read_text()).get("kind", "misc5") if f.exists() else None


def is_v1(run):
    return model_kind(run) == "misc5"


def current_v1_models():
    """V1(LightGBM 5개) 도구의 기본 모델 버전: 배포가 V1이면 CURRENT, 아니면 CURRENT_V1, 아니면 V1 모델이 있는 최신 버전"""
    for ptr in (CURRENT, CURRENT_V1):
        if ptr.exists():
            run = resolve(ptr.read_text().strip())
            if is_v1(run):
                return run
    for run in reversed(versions()):
        if is_v1(run):
            return run
    sys.exit("ml/out/runs 어디에도 V1 모델(models.json kind 없음 = misc5)이 없음")


def dataset_for(run):
    """모델 버전이 학습에 쓴 데이터셋 (run.json inputs.dataset). 데이터셋 버전이면 그 안의 dataset.npz"""
    run = pathlib.Path(run)
    d = meta(run).get("inputs", {}).get("dataset")
    return (REPO / d) if d else run / "dataset.npz"


def use(v):
    run = resolve(v)
    th = run / "thresholds.json"
    if not (run / "models" / "models.json").exists() or not th.exists():
        sys.exit(f"{run.name}에 모델 또는 문턱이 없음")
    t = json.loads(th.read_text())
    t["run"] = run.name
    THRESHOLDS.write_text(json.dumps(t, indent=2) + "\n")
    if is_v1(run):
        CURRENT_V1.write_text(run.name + "\n")
    elif CURRENT.exists() and is_v1(resolve(CURRENT.read_text().strip())) and not CURRENT_V1.exists():
        CURRENT_V1.write_text(CURRENT.read_text())  # V1 → V3로 바꿀 때 마지막 V1을 기억
    CURRENT.write_text(run.name + "\n")
    print(f"CURRENT = {run.name} ({model_kind(run)}), 문턱 {t['base_th']} -> {rel(THRESHOLDS)}")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "list":
        cur = CURRENT.read_text().strip() if CURRENT.exists() else None
        v1 = current_v1_models().name if any(is_v1(r) for r in versions()) else None
        for run in versions():
            m = meta(run)
            star = "*" if run.name == cur else ("1" if run.name == v1 else " ")
            print(f"{star} {run.name}  {m.get('kind', '?'):24s} {m.get('created', ''):19s}  {m.get('note', '')[:90]}")
    elif cmd == "current":
        print(rel(current_models()))
    elif cmd == "current-v1":
        print(rel(current_v1_models()))
    elif cmd == "dataset":
        print(rel(dataset_for(current_models()).parent))
    elif cmd == "use" and len(sys.argv) == 3:
        use(sys.argv[2])
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
