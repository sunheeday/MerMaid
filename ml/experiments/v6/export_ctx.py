"""MLP + GRU 문맥 모델을 C/C++ 런타임용 평면 파일로 내보내기.

    python3 export_ctx.py --base ml/out/v6/weak/base.npz --body ml/out/v6/weak/axis_a/mlp_v6_s0.pt \
        --ctx ml/out/v6/weak/context_v6_mlp-gru_k128_s0/ctx_gru_k128.pt --out ctx_mlp_gru_weak [--input v6]

출력  <out>/manifest.json   텐서 이름·모양·오프셋, 표준화 통계, 입력 열 이름, 토큰 배치
      <out>/weights.bin     float32 little-endian, manifest 순서대로 이어 붙임
      <out>/check.npz       무작위 입력 10개에 대한 파이썬 출력 (C 구현 패리티 시험용)

모델 구조 (common6.MLP + context6.Gru(qfirst=False), d2 = 64, NH = 3)
  1단계  z = clip((x - mu) / sd, -5, 5) (NaN → 0),  m = isfinite(x)
         e = L3(gelu(L2(gelu(L1([z ‖ m])))))                    e: 32차원 요약 (body.0, body.3, body.5)
         공통 모델 확률 p0 = sigmoid(head(e))                        (head, 문맥 없을 때 참고용)
  토큰   c_i = [e_i (32) ‖ onehot(device, 3) ‖ label (1)] = 36차원, 과거 탑승 것만, 최근 k 개 (QUOTA 규칙은 context6.select_tokens)
  2단계  tq = pq(e_now) (64)        tc_i = pc(c_i) (64)
         h = GRU(tc_1..tc_n) 의 마지막 은닉 (64, n = 0 이면 0 벡터)
         logit = head_1(gelu(head_0([tq ‖ h])))                  → 3 장치 확률 sigmoid
  GRU 셀 (torch 규약, 게이트 순서 r, z, n):
         r = σ(W_ir x + b_ir + W_hr h + b_hr)   z = σ(W_iz x + b_iz + W_hz h + b_hz)
         n = tanh(W_in x + b_in + r ⊙ (W_hn h + b_hn))            h' = (1 - z) ⊙ n + z ⊙ h
         weight_ih_l0 = [W_ir; W_iz; W_in] (192 × 64), weight_hh_l0 = [W_hr; W_hz; W_hn] (192 × 64)
"""
import argparse
import json
import pathlib
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
for cand in (HERE, HERE / "v6", HERE.parent / "v6"):
    if (cand / "common6.py").exists():
        sys.path.insert(0, str(cand)); break
import common6 as C6  # noqa: E402
import context6 as CX  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True); ap.add_argument("--body", required=True); ap.add_argument("--ctx", required=True)
    ap.add_argument("--out", required=True); ap.add_argument("--input", default="v6"); ap.add_argument("--val-persons", type=int, default=60)
    ap.add_argument("--d2", type=int, default=64); ap.add_argument("--k", type=int, default=128)
    a = ap.parse_args()
    torch, NETS = C6.make_torch(); out = pathlib.Path(a.out); out.mkdir(parents=True, exist_ok=True)

    # 표준화 통계: context6 와 같은 방식 (베이스 세계, 검증 사람 제외)
    cols = C6.input_cols(a.input); base = C6.load_world(a.base); Xb = base["X"][:, cols]
    is_val = C6.person_val_split(base["person_id"], a.val_persons, 0); mu, sd = C6.stats(Xb[~is_val])
    names = [C6.W6.NAMES[c] for c in cols] if hasattr(C6, "W6") else [str(c) for c in cols]

    m0 = NETS["mlp"](len(cols)); m0.load_state_dict(torch.load(a.body, map_location="cpu")); m0.eval()
    net = CX.make_net(torch, m0.dim, d=a.d2, stage2="gru"); net.load_state_dict(torch.load(a.ctx, map_location="cpu")); net.eval()

    tensors = [("mu", mu), ("sd", sd)]
    for prefix, mod in (("stage1.", m0), ("stage2.", net)):
        for k_, v in mod.state_dict().items():
            tensors.append((prefix + k_, v.detach().cpu().numpy().astype(np.float32)))
    off = 0; manifest = {"input": a.input, "n_features": len(cols), "feature_names": names, "d_summary": m0.dim, "d2": a.d2, "k": a.k,
                         "n_heads": C6.NH, "heads": list(C6.HEADS), "token_layout": "[summary(32) | onehot_device(3) | label(1)]",
                         "gru_gate_order": "r,z,n", "dtype": "float32-le", "tensors": []}
    with open(out / "weights.bin", "wb") as f:
        for name, arr in tensors:
            arr = np.ascontiguousarray(arr, dtype="<f4"); f.write(arr.tobytes())
            manifest["tensors"].append({"name": name, "shape": list(arr.shape), "offset_floats": off, "n": int(arr.size)}); off += int(arr.size)
    manifest["total_floats"] = off; (out / "manifest.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False))

    # 패리티 시험용 표본: 무작위 질의 10개, 각각 문맥 토큰 0 / 5 / k 개
    rng = np.random.default_rng(0); X = Xb[rng.choice(len(Xb), 10, replace=False)].copy()
    X[rng.random(X.shape) < 0.1] = np.nan                                   # 결측 섞기
    Z, P = C6.standardize(X, mu, sd), np.isfinite(X)
    with torch.no_grad():
        E = m0(torch.from_numpy(Z), torch.from_numpy(P))[0].numpy()
        p0 = torch.sigmoid(m0.head(torch.from_numpy(E))).numpy()
        results = {}
        for n_tok in (0, 5, a.k):
            C = np.zeros((10, a.k, m0.dim + C6.NH + 1), np.float32); mask = np.ones((10, a.k), bool)
            if n_tok:
                C[:, :n_tok, :m0.dim] = E[rng.integers(0, 10, (10, n_tok))]
                dev = rng.integers(0, C6.NH, (10, n_tok)); C[np.arange(10)[:, None], np.arange(n_tok)[None, :], m0.dim + dev] = 1.0
                C[:, :n_tok, -1] = rng.integers(0, 2, (10, n_tok)); mask[:, :n_tok] = False
            p = torch.sigmoid(net(torch.from_numpy(E), torch.from_numpy(C), torch.from_numpy(mask))).numpy()
            results[f"ctx_{n_tok}"] = C; results[f"mask_{n_tok}"] = mask; results[f"p_{n_tok}"] = p
    np.savez(out / "check.npz", X=X, E=E, p0=p0, **results)
    write_check_bin(out / "check.bin", X, E, p0, results, a.k)
    write_models_json(out, a)
    n1 = sum(t["n"] for t in manifest["tensors"] if t["name"].startswith("stage1.")); n2 = sum(t["n"] for t in manifest["tensors"] if t["name"].startswith("stage2."))
    print(f"썼음 {out}/weights.bin ({off * 4 / 1024:.0f} KB): 1단계 {n1:,} 2단계 {n2:,} 파라미터 · manifest.json · check.npz")


def write_check_bin(path, X, E, p0, results, k):
    """C++ 패리티 시험(controller/tools/ctx_check.cpp)용 평면 파일. check.npz 와 같은 내용.
    int32 LE [n, nf, de, k, ncase, case_0 .. case_{ncase-1}] (case = 문맥 토큰 수)
    float64 X (n, nf) (NaN 그대로), float32 E (n, de), float32 p0 (n, 3),
    case 마다 float32 ctx (n, k, de + 4), float32 pad (n, k) (1 = 빈 칸), float32 p (n, 3)"""
    cases = sorted(int(key.split("_")[1]) for key in results if key.startswith("p_"))
    n, nf = X.shape; de = E.shape[1]
    with open(path, "wb") as f:
        f.write(np.array([n, nf, de, k, len(cases)] + cases, dtype="<i4").tobytes())
        f.write(np.ascontiguousarray(X, dtype="<f8").tobytes())
        f.write(np.ascontiguousarray(E, dtype="<f4").tobytes()); f.write(np.ascontiguousarray(p0, dtype="<f4").tobytes())
        for c in cases:
            f.write(np.ascontiguousarray(results[f"ctx_{c}"], dtype="<f4").tobytes())
            f.write(np.ascontiguousarray(results[f"mask_{c}"], dtype="<f4").tobytes())
            f.write(np.ascontiguousarray(results[f"p_{c}"], dtype="<f4").tobytes())


def write_models_json(out, a):
    """컨트롤러 P3가 읽는 모델 폴더 표지. kind 로 V1(LightGBM 5개)과 V3(문맥 모델)를 가른다."""
    import hashlib
    fo = json.loads((pathlib.Path(__file__).resolve().parents[3] / "protocol" / "feature_order.json").read_text())
    sha = {n: hashlib.sha256((out / n).read_bytes()).hexdigest() for n in ("manifest.json", "weights.bin")}
    (out / "models.json").write_text(json.dumps({
        "kind": "ctx_gru", "feature_order_version": int(fo["version"]), "n_features": len(fo["features"]),
        "heads": list(C6.HEADS), "input": a.input, "k": a.k, "files": {"manifest": "manifest.json", "weights": "weights.bin"},
        "sha256": sha, "source": {"base": a.base, "body": a.body, "ctx": a.ctx}}, indent=1))


if __name__ == "__main__":
    main()
