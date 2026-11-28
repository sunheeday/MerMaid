#!/usr/bin/env bash
# Nebius VM (Ubuntu 24.04, CUDA) 에서 한 번: 의존성 + featurize 빌드.  저장소 루트에서  bash ml/experiments/v7/gpu_setup.sh
set -eu
sudo apt-get update -qq && sudo apt-get install -y -qq python3-venv python3-pip cmake g++ tmux > /dev/null
[ -d ~/venv ] || python3 -m venv ~/venv
source ~/venv/bin/activate
pip install -q --upgrade pip
pip install -q torch --index-url https://download.pytorch.org/whl/cu128
pip install -q numpy scipy scikit-learn pandas
python3 -c "import torch; print('torch', torch.__version__, 'cuda', torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else '-')"
# 세계를 GPU 머신에서도 만들 수 있게 featurize 빌드 (노트북에서 만든 npz 만 쓸 거면 생략 가능)
if [ -d controller ] && [ ! -x build/featurize ]; then
  cmake -S controller -B build -DCMAKE_BUILD_TYPE=Release > /dev/null && cmake --build build --target featurize -j"$(nproc)" > /dev/null && echo "featurize 빌드 끝"
fi
echo "준비 끝.  tmux new -s v7  →  source ~/venv/bin/activate  →  PHASES='p1 sum' DEVICE=cuda THREADS=16 bash ml/experiments/v7/run_v7.sh"
