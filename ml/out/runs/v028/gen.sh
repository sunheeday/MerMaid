#!/bin/sh
# V3 world6 중간 습관 세계 (base 400명 × 20주행, fleet 20대 × 48), Nebius VM에서 생성 — featurize는 고정 복사본 (Linux 빌드)
cd ~/mermaid
R=ml/out/runs/v028
. ~/venv/bin/activate
export OMP_NUM_THREADS=1 MM_FEATURIZE=$PWD/$R/bin/featurize
t0=$(date +%s)
python3 ml/experiments/v6/make_world6.py base --persons 400 --rides 20 --habit medium --seed 0 --jobs 22 --out $R/base.npz > $R/base.log 2>&1; echo "base rc=$? $(( $(date +%s) - t0 ))s" >> $R/gen.status
python3 ml/experiments/v6/make_world6.py fleet --vehicles 20 --trips 48 --habit medium --seed 5 --jobs 22 --out $R/fleet.npz > $R/fleet.log 2>&1; echo "fleet rc=$? $(( $(date +%s) - t0 ))s" >> $R/gen.status
python3 ml/experiments/v6/check_world6.py $R/base.npz > $R/base_check.txt 2>&1; echo "check rc=$?" >> $R/gen.status
echo done >> $R/gen.status
