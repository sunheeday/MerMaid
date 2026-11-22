"""시뮬레이터 설정 보정 루프 (1단계: 공개 데이터 BATON, comma2k19 기준)

    python3 ml/synth/loop.py run [--rounds 2] [--candidates 4] [--trips 40] [--seed 0] [--effort high] [--note TEXT]
    python3 ml/synth/loop.py run --dry-run      # Claude를 부르지 않고 보낼 요청만 request.json으로 남김
    python3 ml/synth/loop.py adopt vNNN         # 그 실행의 best 설정을 ml/simulator/에 복사 (사람이 git diff로 검토)

한 라운드:
  지금 설정으로 작은 데이터셋(--trips, 시드 고정) → calibrate.evaluate (KS)
  → 요약 통계 + 바꿀 수 있는 값 + 이전 결과를 Claude에 보냄 → 후보 N개 (JSON)
  → patch.apply로 검사 (bounds.json) → 후보마다 같은 크기·시드로 데이터셋 → KS
  → BATON KS 평균이 --min-gain 이상 줄고 comma가 --comma-guard 넘게 나빠지지 않은 가장 좋은 후보를 다음 기준으로
출력: 새 버전 폴더 ml/out/runs/vNNN/ (kind synth): synth.json (요약), base/, rK/ (request.json, proposal.json,
      cN/ 설정과 점수), best/ (최종 설정). 데이터셋 npz는 점수를 낸 뒤 지운다 (--keep-data면 남김; 설정과 시드로 다시 만들 수 있음).

ml/simulator/의 설정은 adopt로만 바뀐다. 그 다음 전체 데이터셋(build_dataset.py) → train.py → 비교 → runs.py use는
지금처럼 사람이 한다. 자동 배포는 없다.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
ML = HERE.parent
REPO = ML.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ML))
sys.path.insert(0, str(ML / "realdata"))
import patch  # noqa: E402
import propose as P  # noqa: E402
import runs  # noqa: E402
import summarize  # noqa: E402


class Scorer:
    """설정 폴더 → 작은 데이터셋 → calibrate.evaluate 결과"""

    def __init__(self, trips, seed, jobs, keep_data):
        import calibrate
        self.cal = calibrate
        self.T, self.names = calibrate.targets()
        self.trips, self.seed, self.jobs, self.keep = trips, seed, jobs, keep_data

    def __call__(self, cfg_dir):
        out = pathlib.Path(cfg_dir) / "sim.npz"
        cmd = [sys.executable, str(ML / "simulator" / "build_dataset.py"), "--trips", str(self.trips),
               "--seed", str(self.seed), "--keep-trips", "0", "--out", str(out)]
        if self.jobs:
            cmd += ["--jobs", str(self.jobs)]
        env = {**os.environ, "MERMAID_SIM_DIR": str(cfg_dir)}
        r = subprocess.run(cmd, env=env, capture_output=True, text=True)
        (pathlib.Path(cfg_dir) / "build.log").write_text(r.stdout + r.stderr)
        if r.returncode != 0:
            raise RuntimeError(f"build_dataset 실패 ({cfg_dir}/build.log)")
        res = self.cal.evaluate(out, self.T, self.names)
        if not self.keep:
            out.unlink()
            shutil.rmtree(out.parent / "trips", ignore_errors=True)
        return res


def score_of(cal):
    return {"baton": cal["baton"]["ks_mean"], "comma": cal["comma"]["ks_mean"]}


def better(s, best, min_gain, comma_guard):
    return s["baton"] <= best["baton"] - min_gain and s["comma"] <= best["comma"] + comma_guard


def run(a, client=None, scorer=None):
    bounds = patch.load_bounds()
    start = patch.load_configs()
    scorer = scorer or Scorer(a.trips, a.seed, a.jobs, a.keep_data)
    params = {k: v for k, v in vars(a).items() if k not in ("func", "note", "jobs", "cmd")}
    rd = runs.new("synth", a.note, inputs={"sim_dir": patch.SIM_DIR, "bounds": HERE / "bounds.json"},
                  params={**params, "model": P.MODEL})
    t0 = time.time()

    patch.write_configs(start, rd / "base")
    cal = scorer(rd / "base")
    (rd / "base" / "calibration.json").write_text(json.dumps(cal, indent=1))
    best = {"cfgs": start, "cal": cal, "score": score_of(cal), "from": "base"}
    print(f"[synth] base KS baton {best['score']['baton']:.4f} comma {best['score']['comma']:.4f}", file=sys.stderr)
    history, rounds = [], []

    for k in range(1, a.rounds + 1):
        rk = rd / f"r{k}"
        rk.mkdir()
        req = P.build_request(summarize.from_calibration(best["cal"]), patch.editable(best["cfgs"], bounds),
                              history, a.candidates, bounds, a.effort)
        (rk / "request.json").write_text(json.dumps(req, indent=1))
        if a.dry_run:
            print(f"[synth] dry run: 요청만 남김 -> {runs.rel(rk / 'request.json')}", file=sys.stderr)
            break
        client = client or P.make_client()
        try:
            prop, meta = P.propose(client, req)
        except P.ProposalError as e:
            (rk / "error.txt").write_text(str(e))
            print(f"[synth] r{k}: {e}", file=sys.stderr)
            rounds.append({"round": k, "error": str(e)})
            break
        (rk / "proposal.json").write_text(json.dumps({"meta": meta, **prop.model_dump()}, indent=1))
        results = []
        for i, c in enumerate(prop.candidates):
            cd = rk / f"c{i}"
            new, errors, eff = patch.apply(best["cfgs"], [ch.model_dump() for ch in c.changes], bounds)
            res = {"candidate": f"r{k}/c{i}", "hypothesis": c.hypothesis, "changes": eff or [ch.model_dump() for ch in c.changes]}
            if errors:
                res["rejected"] = errors
            else:
                patch.write_configs(new, cd)
                cc = scorer(cd)
                (cd / "calibration.json").write_text(json.dumps(cc, indent=1))
                res["ks"] = score_of(cc)
                res["_cfgs"], res["_cal"] = new, cc
            results.append(res)
            tag = f"rejected {errors}" if errors else f"KS baton {res['ks']['baton']:.4f} comma {res['ks']['comma']:.4f}"
            print(f"[synth] r{k}/c{i}: {tag}", file=sys.stderr)

        ok = [r for r in results if "ks" in r and better(r["ks"], best["score"], a.min_gain, a.comma_guard)]
        win = min(ok, key=lambda r: r["ks"]["baton"], default=None)
        adopted = win is not None
        if adopted:
            best = {"cfgs": win["_cfgs"], "cal": win["_cal"], "score": win["ks"], "from": win["candidate"]}
        for r in results:
            r.pop("_cfgs", None)
            r.pop("_cal", None)
            r["adopted"] = adopted and r is win
        history.append({"round": k, "baseline_ks": rounds[-1]["best_after"] if rounds else score_of(cal),
                        "candidates": results})
        rounds.append({"round": k, "analysis": prop.analysis, "model": meta["model"], "usage": meta["usage"],
                       "candidates": results, "best_after": best["score"]})
        print(f"[synth] r{k}: {'채택 ' + win['candidate'] if adopted else '채택 없음'}, "
              f"best baton {best['score']['baton']:.4f}", file=sys.stderr)

    patch.write_configs(best["cfgs"], rd / "best")
    summary = {"base": score_of(cal), "best": best["score"], "best_from": best["from"],
               "changes_vs_repo": patch.diff(start, best["cfgs"], bounds), "rounds": rounds,
               "seconds": round(time.time() - t0, 1), "dry_run": a.dry_run}
    (rd / "synth.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps({k: summary[k] for k in ("base", "best", "best_from", "changes_vs_repo")}, indent=1))
    if summary["changes_vs_repo"]:
        print(f"\n검토 후 적용: python3 ml/synth/loop.py adopt {rd.name}  (그 다음 git diff ml/simulator/)", file=sys.stderr)
    return rd


def adopt(a):
    rd = runs.resolve(a.version)
    if runs.meta(rd).get("kind") != "synth" or not (rd / "best").is_dir():
        sys.exit(f"{rd.name}은 synth 실행이 아니거나 best/가 없음")
    bounds = patch.load_bounds()
    cur, new = patch.load_configs(), patch.load_configs(rd / "best")
    d = patch.diff(cur, new, bounds)
    for f in patch.FILES:  # bounds 밖 값이 다르면 (그 사이 사람이 고친 것) 덮어쓰지 않는다
        locked_cur, locked_new = json.loads(json.dumps(cur[f])), json.loads(json.dumps(new[f]))
        for p in bounds["paths"]:
            pf, keys = patch.locate(p)
            if pf == f:
                patch._set({f: locked_cur}, p, None)
                patch._set({f: locked_new}, p, None)
        if locked_cur != locked_new:
            sys.exit(f"{f}: 잠긴 값이 {rd.name} 이후 바뀌었음. 손으로 합쳐야 함")
    if not d:
        print("바뀐 것 없음")
        return
    patch.write_configs(new, patch.SIM_DIR)
    for x in d:
        print(f"{x['path']}: {x['old']} -> {x['new']}")
    print(f"ml/simulator/ 갱신 ({rd.name}). git diff로 확인 후 build_dataset.py → train.py")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--rounds", type=int, default=2)
    r.add_argument("--candidates", type=int, default=4)
    r.add_argument("--trips", type=int, default=40)
    r.add_argument("--seed", type=int, default=0)
    r.add_argument("--jobs", type=int, default=0, help="build_dataset --jobs (0: 기본)")
    r.add_argument("--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"])
    r.add_argument("--min-gain", type=float, default=0.005, help="BATON KS 평균이 이만큼은 줄어야 채택")
    r.add_argument("--comma-guard", type=float, default=0.02, help="comma KS 평균이 이보다 더 나빠지면 거부")
    r.add_argument("--keep-data", action="store_true")
    r.add_argument("--dry-run", action="store_true")
    r.add_argument("--note", default="")
    r.set_defaults(func=run)
    d = sub.add_parser("adopt")
    d.add_argument("version")
    d.set_defaults(func=adopt)
    a = ap.parse_args(argv)
    a.func(a)


if __name__ == "__main__":
    main()
