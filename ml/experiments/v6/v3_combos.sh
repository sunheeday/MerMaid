#!/usr/bin/env bash
# V3 문맥 모델 네 조합을 한 세계에서 같이 학습하고(GPU 서버), Pi용 모델 폴더까지 만든다. 저장소 루트에서 실행.
#
#   VM:   bash ml/experiments/v6/v3_combos.sh train     학습 8개 (조합 4 × 시드 2), 백그라운드. 진행 status
#   VM:   bash ml/experiments/v6/v3_combos.sh status
#   VM:   bash ml/experiments/v6/v3_combos.sh export    조합마다 검증 ROC가 높은 시드를 골라 내보내기 → <run>/models
#   Mac:  NEBIUS=nebius-gpu bash ml/experiments/v6/v3_combos.sh fetch    세계(npz + 기록)와 모델·예측·로그를 Mac으로
#   Mac:  bash ml/experiments/v6/v3_combos.sh finish    ctx_check → 임계값 → run.json, 비교표 <D>/combos.md
#
# 조합 (run 번호는 RUNS로 바꾼다)
#   v029 mlp + gru     context7 --stage2 gru      (= v024 구조)
#   v030 mlp + attn    context7 --stage2 enc      (= v026 구조, [질의 ‖ 토큰] 인코더)
#   v031 mlp + xattn   context7 --stage2 attn     (= v027 구조, 교차 어텐션)
#   v032 ft  + attn    context6 --stage1 ft --stage2 attn
# context7 세 개는 사람 분할·토큰·1단계 몸통이 같다 (몸통 캐시 v029/cache). ft는 context6에만 있어 분할이 조금 다르다.
# 학습 출력 <run>/train/s<seed>/, 고른 시드는 <run>/train/picked.
set -eu
D=${D:-ml/out/runs/v028}
RUNS=${RUNS:-"v029 v030 v031 v032"}
SEEDS=${SEEDS:-"0 1"}
K=${K:-128}; STEPS=${STEPS:-10000}; BODY_STEPS=${BODY_STEPS:-4000}; VALP=${VALP:-60}; ADAPT=${ADAPT:-24}
THREADS=${THREADS:-4}
REMOTE=${REMOTE:-mermaid}
E6=ml/experiments/v6; E7=ml/experiments/v7
cmd=${1:-}

combo() {  # combo <run> → "trainer stage1 stage2(학습 코드 이름) stage2(내보내기 이름)"
    case $(echo "$RUNS" | tr ' ' '\n' | grep -n "^$1$" | cut -d: -f1) in
        1) echo "c7 mlp gru gru" ;; 2) echo "c7 mlp enc attn" ;; 3) echo "c7 mlp attn xattn" ;; 4) echo "c6 ft attn attn" ;;
        *) echo "unknown run $1" >&2; exit 2 ;;
    esac
}
first_run() { echo "$RUNS" | cut -d' ' -f1; }

train_one() {  # train_one <run> <seed>
    set -- "$1" "$2" $(combo "$1")
    local run=ml/out/runs/$1 seed=$2 tr=$3 s1=$4 s2=$5 out rc=0
    out=$run/train/s$seed; mkdir -p "$out"
    if [ "$tr" = c7 ]; then
        python3 $E7/context7.py --world $D/base.npz --fleet $D/fleet.npz --cache ml/out/runs/$(first_run)/cache --val-persons $VALP \
            --adapt-trips $ADAPT --input v5 --stage1 mlp --d1 32 --stage2 "$s2" --k $K --seed "$seed" --steps $STEPS --body-steps $BODY_STEPS \
            --threads $THREADS --device cuda --out "$out" > "$out.log" 2>&1 || rc=$?
    else
        python3 $E6/context6.py --base $D/base.npz --fleet $D/fleet.npz --cache $run/cache --input v5 --stage1 "$s1" --stage2 "$s2" \
            --k $K --steps $STEPS --body-steps $BODY_STEPS --seed "$seed" --val-persons $VALP --adapt-trips $ADAPT \
            --threads $THREADS --device cuda --out "$out" > "$out.log" 2>&1 || rc=$?
    fi
    echo "$1 s$seed rc=$rc" >> $D/combos.status
}

val_of() { grep -o "조기 종료[^(]*(검증 [0-9.]*" "$1/log.txt" | grep -o "[0-9.]*$" | tail -1; }

case "$cmd" in
train)
    [ -d ~/venv ] && . ~/venv/bin/activate
    python3 -c "import torch; assert torch.cuda.is_available(), 'no CUDA'"
    [ -f $D/base.npz ] && [ -f $D/fleet.npz ] || { echo "세계 없음: $D" >&2; exit 2; }
    rm -f $D/combos.status
    export OMP_NUM_THREADS=1 D RUNS SEEDS K STEPS BODY_STEPS VALP ADAPT THREADS
    # 1단계 몸통 캐시를 먼저 한 번씩 (context7 mlp, context6 ft), 그다음 나머지를 동시에
    nohup sh -c "
        bash $0 _one $(first_run) 0 & bash $0 _one $(echo $RUNS | cut -d' ' -f4) 0 & wait
        for r in $RUNS; do for s in $SEEDS; do
            [ \"\$r\" = $(first_run) ] && [ \$s = 0 ] && continue
            [ \"\$r\" = $(echo $RUNS | cut -d' ' -f4) ] && [ \$s = 0 ] && continue
            bash $0 _one \$r \$s &
        done; done; wait; echo done >> $D/combos.status" > $D/combos.nohup 2>&1 < /dev/null &
    echo "학습 시작 (pid $!). 진행: bash $0 status"
    ;;
_one)
    [ -d ~/venv ] && . ~/venv/bin/activate
    train_one "$2" "$3"
    ;;
status)
    cat $D/combos.status 2>/dev/null || echo "(아직 끝난 것 없음)"
    for r in $RUNS; do for s in $SEEDS; do
        f=ml/out/runs/$r/train/s$s.log; [ -f "$f" ] && printf '%s s%s: %s\n' "$r" "$s" "$(grep -v Warning "$f" | grep -E '스텝|조기|채점|Error' | tail -1 | cut -c1-120)"
    done; done
    ;;
export)
    [ -d ~/venv ] && . ~/venv/bin/activate
    grep -q done $D/combos.status || { echo "학습이 아직 안 끝남 ($D/combos.status)" >&2; exit 1; }
    for r in $RUNS; do
        set -- $(combo $r); tr=$1; s1=$2; s2t=$3; s2=$4; run=ml/out/runs/$r; best=""; bv=-1
        for s in $SEEDS; do
            v=$(val_of $run/train/s$s 2>/dev/null || true); [ -n "$v" ] || { echo "$r s$s: 결과 없음" >&2; continue; }
            if python3 -c "import sys; sys.exit(0 if float('$v') > float('$bv') else 1)"; then best=$s; bv=$v; fi
        done
        [ -n "$best" ] || { echo "$r: 쓸 시드가 없음" >&2; exit 1; }
        echo "$best $bv" > $run/train/picked; t=$run/train/s$best
        if [ $tr = c7 ]; then
            C=ml/out/runs/$(first_run)/cache
            OMP_NUM_THREADS=4 python3 $E6/export_ctx.py --base $D/base.npz --body $C/body_mlp32_v5_fl.pt --ctx $t/ctx_${s2t}_k$K.pt \
                --out $run/models --input v5 --stage1 mlp --stage2 $s2 --k $K --v7 $E7 --val-persons $VALP --e-cache $C/E_mlp32_v5_fl.npy
        else
            OMP_NUM_THREADS=4 python3 $E6/export_ctx.py --base $D/base.npz --body $run/cache/body_${s1}_v5.pt --ctx $t/ctx_${s2t}_k$K.pt \
                --out $run/models --input v5 --stage1 $s1 --stage2 $s2 --k $K --val-persons $VALP
        fi 2>&1 | grep -v -i "warning\|nanmean\|nanvar\|mu = np" | tail -2
        [ -f $run/models/models.json ] || { echo "$r: 내보내기 실패" >&2; exit 1; }
        echo "$r: seed $best (val $bv) → $run/models"
    done
    ;;
fetch)
    [ -n "${NEBIUS:-}" ] || { echo "NEBIUS=user@host 를 주세요" >&2; exit 2; }
    ssh "$NEBIUS" "cd ~/$REMOTE && grep -q done $D/combos.status && ls ml/out/runs/{$(echo $RUNS | tr ' ' ',')}/models/models.json >/dev/null" \
        || { echo "VM에서 학습·export가 아직 안 끝남" >&2; exit 1; }
    files="$D/base.npz $D/fleet.npz $D/gen.sh $D/gen.status $D/combos.status $D/bin $D/base_check.txt $D/*.log $D/*_report.json $D/*_persons.json $D/*_rides.json $D/*.png"
    for r in $RUNS; do
        files="$files ml/out/runs/$r/models ml/out/runs/$r/train/picked ml/out/runs/$r/train/s*.log"
        files="$files ml/out/runs/$r/train/s*/*.pt ml/out/runs/$r/train/s*/fleet_preds.npz ml/out/runs/$r/train/s*/*.md ml/out/runs/$r/train/s*/*.json ml/out/runs/$r/train/s*/*.csv ml/out/runs/$r/train/s*/log.txt"
    done
    files="$files ml/out/runs/$(first_run)/cache/body_*.pt ml/out/runs/$(echo $RUNS | cut -d' ' -f4)/cache/body_*.pt"
    ssh "$NEBIUS" "cd ~/$REMOTE && tar czf - \$(ls -d $files 2>/dev/null)" | tar xzf -
    du -sh $D $(for r in $RUNS; do echo ml/out/runs/$r; done)
    ;;
finish)
    [ -x build/ctx_check ] || { echo "build/ctx_check 없음" >&2; exit 2; }
    for r in $RUNS; do
        set -- $(combo $r); run=ml/out/runs/$r; read -r best bv < $run/train/picked
        mkdir -p $run/verify
        build/ctx_check $run/models > $run/verify/ctx_check_mac.txt || { cat $run/verify/ctx_check_mac.txt; exit 1; }
        python3 $E6/pick_thresholds_ctx.py --preds $run/train/s$best/fleet_preds.npz --out $run/thresholds.json --run $r > $run/thresholds.log
        python3 - "$r" "$D" "$1" "$2" "$4" "$best" "$bv" "$K" "$STEPS" "$SEEDS" <<'EOF'
import datetime, json, pathlib, subprocess, sys
r, d, tr, s1, s2, best, bv, k, steps, seeds = sys.argv[1:]
run = pathlib.Path("ml/out/runs") / r
c = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
dirty = bool(subprocess.run(["git", "status", "--porcelain", "--", "ml", "controller"], capture_output=True, text=True).stdout.strip())
trainer = "ml/experiments/v7/context7.py" if tr == "c7" else "ml/experiments/v6/context6.py"
meta = {"version": r, "kind": "models", "created": datetime.datetime.now().isoformat(timespec="seconds"),
        "git": {"commit": c, "dirty": dirty}, "feature_order_version": 5,
        "note": f"V3 context model ctx_{s2}: {s1} summary + {s2}, trained on the Nebius GPU on world {d} (habit medium), seed {best} of {seeds} picked by validation ROC",
        "inputs": {"dataset": d},
        "params": {"trainer": trainer, "input": "v5", "stage1": s1, "stage2": s2, "k": int(k), "steps": int(steps), "val_persons": 60,
                   "seed": int(best), "seeds_tried": [int(x) for x in seeds.split()], "val_roc": float(bv), "device": "cuda",
                   "export": "ml/experiments/v6/export_ctx.py" + (" --v7" if tr == "c7" else ""), "thresholds": "ml/experiments/v6/pick_thresholds_ctx.py",
                   "runner": "ml/experiments/v6/v3_combos.sh"},
        "verify": {"mac": "verify/ctx_check_mac.txt"}}
(run / "run.json").write_text(json.dumps(meta, indent=1) + "\n")
EOF
        echo "$r: $(tail -1 $run/verify/ctx_check_mac.txt), thresholds $(python3 -c "import json; print(json.load(open('$run/thresholds.json'))['base_th'])")"
    done
    python3 $E6/compare_combos.py --dataset $D --runs $RUNS --out $D/combos.md
    ;;
*)
    sed -n '2,17p' "$0"; exit 2 ;;
esac
