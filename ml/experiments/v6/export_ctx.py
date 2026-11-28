"""문맥 모델(1단계 MLP/FT + 2단계 GRU/어텐션)을 C/C++ 런타임용 평면 파일로 내보내기.

    python3 export_ctx.py --base ml/out/v6/weak/base.npz --body ml/out/v6/weak/axis_a/mlp_v6_s0.pt \
        --ctx ml/out/v6/weak/context_v6_mlp-gru_k128_s0/ctx_gru_k128.pt --out ctx_mlp_gru_weak [--input v6] \
        [--stage1 mlp|ft] [--stage2 gru|attn|xattn] [--v7 <context7.py 폴더>]
  models.json kind: 2단계 gru → ctx_gru, attn → ctx_attn, xattn → ctx_xattn. 컨트롤러 구현은 controller/common/ctx_model.cpp.
  --v7: ml/experiments/v7/context7.py 로 학습한 2단계 (--fleet 방식). 이름이 다르다: v7 enc = 여기 attn, v7 attn(XAttn) = 여기 xattn.
        표준화 통계는 context7 분할(베이스 사람 permutation(rng 0) 앞 --val-persons 명 제외), 토큰은 그냥 최근 k개 (token_quota 0).
        --e-cache 로 context7 이 캐시한 요약 E (E_<body>_fl.npy)를 주면 그 앞 행들과 다시 계산한 E를 비교해 통계가 맞는지 본다.
  1단계 ft (common6.FT): 피처 토큰 t_i = present_i ? z_i·w_i + b_i : miss_i, [cls ‖ t] → nn.TransformerEncoder → 첫 칸
  2단계 xattn (context7.XAttn): m = LN_c([null ‖ pc(c_i)]), q = pq(e), 층마다 q += MHA(LN_q(q), m, m), q += FF(LN(q)) (GELU) → head(LN(q))
  2단계 attn (context6.Attn): [pq(e) + tok_type0 ‖ pc(c_i) + tok_type1] → nn.TransformerEncoder (빈 칸은 key padding) → head(첫 칸)
  인코더 층은 PyTorch 기본값 (post-norm, ReLU, LayerNorm eps 1e-5, 마지막 norm 없음). 헤드 수는 manifest stage1/stage2.heads.

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
    ap.add_argument("--stage1", choices=["mlp", "ft"], default="mlp"); ap.add_argument("--stage2", choices=["gru", "attn", "xattn"], default="gru")
    ap.add_argument("--v7", default=None, help="context7.py 가 있는 폴더 (친구 v7 학습 결과를 내보낼 때)")
    ap.add_argument("--e-cache", default=None, help="context7 의 E 캐시 npy (통계 확인용)")
    a = ap.parse_args()
    if a.stage2 == "xattn" and not a.v7:
        ap.error("--stage2 xattn 은 context7 모델이라 --v7 이 필요하다")
    torch, NETS = C6.make_torch(); out = pathlib.Path(a.out); out.mkdir(parents=True, exist_ok=True)

    # 표준화 통계: context6 와 같은 방식 (베이스 세계, 검증 사람 제외)
    cols = C6.input_cols(a.input); base = C6.load_world(a.base); Xb = base["X"][:, cols]
    if a.v7:  # context7.main 의 --fleet 분할 그대로
        persons = np.unique(base["person_id"]); val_p = np.random.default_rng(0).permutation(persons)[:a.val_persons]
        is_val = np.isin(base["person_id"], val_p)
    else:
        is_val = C6.person_val_split(base["person_id"], a.val_persons, 0)
    mu, sd = C6.stats(Xb[~is_val])
    names = [C6.W6.NAMES[c] for c in cols] if hasattr(C6, "W6") else [str(c) for c in cols]

    m0 = NETS[a.stage1](len(cols)); m0.load_state_dict(torch.load(a.body, map_location="cpu")); m0.eval()
    if a.v7:
        sys.path.insert(0, str(pathlib.Path(a.v7).resolve()))
        import context7 as C7  # noqa: E402
        net = C7.make_net(torch, m0.dim, m0.dim + C6.NH + 1, d=a.d2, stage2={"gru": "gru", "attn": "enc", "xattn": "attn"}[a.stage2])
    else:
        net = CX.make_net(torch, m0.dim, d=a.d2, stage2=a.stage2)
    net.load_state_dict(torch.load(a.ctx, map_location="cpu")); net.eval()
    heads1 = m0.enc.layers[0].self_attn.num_heads if a.stage1 == "ft" else 0
    heads2 = {"attn": lambda: net.enc.layers[0].self_attn.num_heads, "xattn": lambda: net.att[0].num_heads}.get(a.stage2, lambda: 0)()
    if a.stage2 == "xattn":  # C++는 이 모양만 구현 (LayerNorm eps 1e-5, FF = LN, Linear, GELU, Dropout, Linear)
        assert all(abs(n.eps - 1e-5) < 1e-12 for n in [net.ln_c, net.head[0], *net.ln_q, *[f[0] for f in net.ff]])
        assert all(isinstance(f[2], torch.nn.GELU) and f[2].approximate == "none" for f in net.ff)
    if a.e_cache:
        Ec = np.load(a.e_cache, mmap_mode="r"); nchk = min(20000, len(Xb))
        with torch.no_grad():
            Er = m0(torch.from_numpy(C6.standardize(Xb[:nchk], mu, sd)), torch.from_numpy(np.isfinite(Xb[:nchk])))[0].numpy()
        de_ = float(np.abs(Er - Ec[:nchk]).max()); print(f"E 캐시 비교 (앞 {nchk} 행): 최대 차이 {de_:.2e}")
        assert de_ < 1e-4, "표준화 통계가 학습 때와 다르다"
    for enc in [m.enc for m in (m0, net) if hasattr(m, "enc")]:  # C++는 PyTorch 기본 층만 구현
        L = enc.layers[0]
        assert not L.norm_first and L.activation_relu_or_gelu == 1 and enc.norm is None, "encoder layer is not the default post-norm ReLU"

    tensors = [("mu", mu), ("sd", sd)]
    for prefix, mod in (("stage1.", m0), ("stage2.", net)):
        for k_, v in mod.state_dict().items():
            tensors.append((prefix + k_, v.detach().cpu().numpy().astype(np.float32)))
    off = 0; manifest = {"input": a.input, "n_features": len(cols), "feature_names": names, "d_summary": m0.dim, "d2": a.d2, "k": a.k,
                         "n_heads": C6.NH, "heads": list(C6.HEADS), "token_layout": "[summary(32) | onehot_device(3) | label(1)]",
                         "stage1": {"type": a.stage1, "heads": heads1}, "stage2": {"type": a.stage2, "heads": heads2},
                         "token_select": "recent", "token_quota": 0 if a.v7 else 3, "trainer": "context7" if a.v7 else "context6",
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
        "kind": "ctx_" + a.stage2, "stage1": a.stage1, "stage2": a.stage2, "feature_order_version": int(fo["version"]), "n_features": len(fo["features"]),
        "heads": list(C6.HEADS), "input": a.input, "k": a.k, "files": {"manifest": "manifest.json", "weights": "weights.bin"},
        "sha256": sha, "source": {"base": a.base, "body": a.body, "ctx": a.ctx}}, indent=1))


if __name__ == "__main__":
    main()
