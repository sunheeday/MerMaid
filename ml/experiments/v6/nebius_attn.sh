#!/usr/bin/env bash
# V3 문맥 모델(FT 요약 + 어텐션)을 GPU 서버(Nebius VM)에서 학습하고, Mac에서 내보내 Pi용 모델 폴더로 만든다.
# 저장소 루트에서 실행. 같은 파일을 Mac과 VM 양쪽에서 쓴다.
#
#   Mac:  NEBIUS=user@host bash ml/experiments/v6/nebius_attn.sh push      코드 + 데이터를 VM ~/mermaid로 보냄 (약 240 MB)
#   VM:   bash ml/experiments/v6/nebius_attn.sh setup                       한 번: 의존성 (v7/gpu_setup.sh)
#   VM:   bash ml/experiments/v6/nebius_attn.sh train                       학습 (백그라운드, ssh가 끊겨도 계속). 로그 ~/mermaid/$V/train.log
#   VM:   bash ml/experiments/v6/nebius_attn.sh status                      진행 확인 (끝나면 "done")
#   Mac:  NEBIUS=user@host bash ml/experiments/v6/nebius_attn.sh fetch     결과 3개 + 로그를 Mac의 $V로 가져옴
#   Mac:  bash ml/experiments/v6/nebius_attn.sh finish                      내보내기 → C++ 패리티(ctx_check) → 임계값 → run.json
#
# 환경 변수
#   D=ml/out/runs/v023        데이터 (base.npz, fleet.npz). world7 세계로 하려면 그 run 폴더
#   V=ml/out/runs/v025        새 모델 run 폴더 (덮어쓰지 않도록 새 번호)
#   STAGE1=ft STAGE2=attn     1단계 mlp|ft, 2단계 gru|attn
#   K=128 STEPS=10000 BODY_STEPS=4000 SEED=0
#   THREADS=$(nproc)          1단계 FT 몸통 학습은 CPU (context6 --device는 2단계만 GPU)
#   REMOTE=mermaid            VM 쪽 저장소 폴더 (홈 기준)
# 가져오는 결과: $V/cache/body_${STAGE1}_v5.pt, $V/train/ctx_${STAGE2}_k$K.pt, $V/train/fleet_preds.npz (+ log, summary)
set -eu
D=${D:-ml/out/runs/v023}
V=${V:-ml/out/runs/v025}
STAGE1=${STAGE1:-ft}; STAGE2=${STAGE2:-attn}
K=${K:-128}; STEPS=${STEPS:-10000}; BODY_STEPS=${BODY_STEPS:-4000}; SEED=${SEED:-0}
REMOTE=${REMOTE:-mermaid}
E=ml/experiments/v6
cmd=${1:-}

need_host() { [ -n "${NEBIUS:-}" ] || { echo "NEBIUS=user@host 를 주세요 (VM ssh 주소)" >&2; exit 2; }; }

case "$cmd" in
push)
    need_host
    [ -f "$D/base.npz" ] && [ -f "$D/fleet.npz" ] || { echo "데이터 없음: $D/base.npz, $D/fleet.npz" >&2; exit 2; }
    # 학습에 필요한 것만: ml 파이썬(시뮬레이터·trip은 world6 import 때문), protocol, v6/v7 스크립트, 데이터 npz 2개
    COPYFILE_DISABLE=1 tar --no-xattrs --no-mac-metadata --exclude='__pycache__' -czf - \
        ml/*.py ml/simulator ml/train ml/experiments/v6 ml/experiments/v7 protocol \
        "$D/base.npz" "$D/fleet.npz" \
        | ssh "$NEBIUS" "mkdir -p ~/$REMOTE && cd ~/$REMOTE && tar xzf - && echo 'pushed to ~/$REMOTE'"
    echo "다음: ssh $NEBIUS  →  cd ~/$REMOTE && bash $E/nebius_attn.sh setup && bash $E/nebius_attn.sh train"
    ;;
setup)
    bash ml/experiments/v7/gpu_setup.sh
    ;;
train)
    [ -d ~/venv ] && . ~/venv/bin/activate
    python3 -c "import torch; assert torch.cuda.is_available(), 'no CUDA'; print('GPU', torch.cuda.get_device_name(0))"
    mkdir -p "$V"
    rm -f "$V/train.status"
    THREADS=${THREADS:-$(nproc)}
    nohup sh -c "OMP_NUM_THREADS=1 python3 $E/context6.py --base $D/base.npz --fleet $D/fleet.npz --cache $V/cache --input v5 \
        --stage1 $STAGE1 --stage2 $STAGE2 --k $K --steps $STEPS --body-steps $BODY_STEPS --seed $SEED --threads $THREADS \
        --device cuda --out $V/train > $V/train.log 2>&1; echo \"train rc=\$?\" > $V/train.status; echo done >> $V/train.status" \
        > /dev/null 2>&1 &
    echo "학습 시작 (pid $!). 진행: bash $E/nebius_attn.sh status   로그: $V/train.log"
    ;;
status)
    tail -3 "$V/train.log" 2>/dev/null || echo "로그 없음: $V/train.log"
    cat "$V/train.status" 2>/dev/null || echo "(아직 도는 중)"
    ;;
fetch)
    need_host
    mkdir -p "$V"
    ssh "$NEBIUS" "cd ~/$REMOTE && grep -q 'train rc=0' $V/train.status" || { echo "VM의 학습이 아직 안 끝났거나 실패 ($V/train.status)" >&2; exit 1; }
    ssh "$NEBIUS" "cd ~/$REMOTE && tar czf - $V/cache/body_${STAGE1}_v5.pt $V/train/ctx_${STAGE2}_k$K.pt $V/train/fleet_preds.npz \
        $V/train/log.txt $V/train/summary.md $V/train/oneline.csv $V/train/per_car.csv $V/train.log" | tar xzf -
    ls -la "$V/cache" "$V/train"
    echo "다음: bash $E/nebius_attn.sh finish"
    ;;
finish)
    BODY=$V/cache/body_${STAGE1}_v5.pt; CTX=$V/train/ctx_${STAGE2}_k$K.pt
    [ -f "$BODY" ] && [ -f "$CTX" ] || { echo "학습 결과 없음: $BODY, $CTX (fetch 먼저)" >&2; exit 2; }
    [ -x build/ctx_check ] || { echo "build/ctx_check 없음: cmake -S controller -B build && cmake --build build" >&2; exit 2; }
    OMP_NUM_THREADS=1 python3 $E/export_ctx.py --base $D/base.npz --body "$BODY" --ctx "$CTX" --out "$V/models" --input v5 \
        --stage1 "$STAGE1" --stage2 "$STAGE2" --k "$K"
    mkdir -p "$V/verify"
    build/ctx_check "$V/models" | tee "$V/verify/ctx_check_mac.txt"
    python3 $E/pick_thresholds_ctx.py --preds "$V/train/fleet_preds.npz" --out "$V/thresholds.json" --run "$(basename "$V")"
    python3 - "$V" "$D" "$STAGE1" "$STAGE2" "$K" "$STEPS" "$BODY_STEPS" "$SEED" <<'EOF'
import datetime, json, pathlib, subprocess, sys
v, d, s1, s2, k, steps, bsteps, seed = sys.argv[1:]
run = pathlib.Path(v)
if not (run / "run.json").exists():
    c = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    meta = {"version": run.name, "kind": "models", "created": datetime.datetime.now().isoformat(timespec="seconds"),
            "git": {"commit": c, "dirty": True}, "feature_order_version": 5,
            "note": f"V3 context model ({s1} + {s2}), trained on a GPU server (Nebius), exported on the Mac",
            "inputs": {"dataset": d},
            "params": {"trainer": "ml/experiments/v6/context6.py", "input": "v5", "stage1": s1, "stage2": s2, "k": int(k),
                       "steps": int(steps), "body_steps": int(bsteps), "seed": int(seed), "device": "cuda",
                       "export": "ml/experiments/v6/export_ctx.py", "thresholds": "ml/experiments/v6/pick_thresholds_ctx.py"}}
    (run / "run.json").write_text(json.dumps(meta, indent=1) + "\n")
    print(f"wrote {run}/run.json")
EOF
    echo "Pi로: COPYFILE_DISABLE=1 tar --no-xattrs --no-mac-metadata -czf - controller protocol $V/models $V/thresholds.json | ssh qnxuser@192.168.2.2 'cd ~/mermaid/repo && tar xzf - && cmake --build build && ./build/ctx_check $V/models'"
    ;;
*)
    sed -n '2,20p' "$0"
    exit 2
    ;;
esac
