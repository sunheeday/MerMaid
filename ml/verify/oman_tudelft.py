# TU Delft ATDOMS 재현 검증: python3 ml/verify/oman_tudelft.py data/raw/ATDOMS.mat
# 결과(2026-11-11): 개인별 K 중앙값 48.7, MAE 0.97 (sin), 공통 K=70.8이면 1.58. 개인별 K는 out/K_<mode>.npy
# Oman 모델(Irmak 2022 구조), 입력 |a(t)|, 0.3 Hz 사인파 재구성, MAE 출력
import sys, itertools, pathlib, numpy as np, scipy.io as sio
from scipy.signal import lfilter
from scipy.optimize import minimize_scalar
DT=0.05; F=0.3
OUT=pathlib.Path(__file__).with_name('out'); OUT.mkdir(exist_ok=True)
A=sio.loadmat(sys.argv[1],squeeze_me=True,struct_as_record=False)['ATDOMS']
P=A._fieldnames; C=['C1','C2','C3','C4']
def arr(x): return np.atleast_1d(x).astype(float)
def session(p,c):
    S=getattr(getattr(A,p),c)
    ph={k:(arr(getattr(S,k).misc.time),arr(getattr(S,k).misc.score)) for k in ['Phase1','Rest1','Phase2','Rest2']}
    if len(ph['Rest1'][0])==0: seg=[(0.0,ph['Phase1'][0][-1]+30)]
    else:
        seg=[(0.0,ph['Rest1'][0][0])]
        if len(ph['Phase2'][0]): seg.append((ph['Phase2'][0][0], ph['Rest2'][0][0] if len(ph['Rest2'][0]) else ph['Phase2'][0][-1]+30))
    t=np.concatenate([ph[k][0] for k in ph]); y=np.concatenate([ph[k][1] for k in ph])
    return seg,t,y
def stim(seg,T,amp,mode):
    t=np.arange(0,T,DT); a=np.zeros_like(t)
    for s,e in seg:
        m=(t>=s)&(t<e); r=np.minimum(1,np.minimum((t[m]-s)/10,(e-t[m])/10))
        a[m]=r*amp*(np.sin(2*np.pi*F*(t[m]-s)) if mode=='sin' else 1.0)
    return t,np.abs(a)
def lp2(x,b):
    al=np.exp(-DT/b); f=lambda u: lfilter([1-al],[1,-al],u); return f(f(x))
def oman(c,K,b1=66.2,b2=502.4,po=0.4):
    S=lp2(c,b2); Fp=lp2(K*S*c,b1); return np.maximum(Fp+S,0)**po
SES=[(p,c,*session(p,c)) for p in P for c in C]
CACHE={}
def pred(p,c,seg,t,amp,mode,K):
    key=(p,c,amp,mode)
    if key not in CACHE: CACHE[key]=stim(seg,t[-1]+60,amp,mode)
    tt,x=CACHE[key]; o=oman(x,K); return np.interp(t,tt,o)
amps=[1.0,1.5,2.0,2.5]
for mode in ['sin','env']:
    print('== input mode',mode)
    # 1) group-median params, test condition mappings
    best=None
    for perm in itertools.permutations(amps):
        e=[];
        for p,c,seg,t,y in SES:
            e.append(np.abs(pred(p,c,seg,t,perm[C.index(c)],mode,70.8)-y))
        m=np.nanmean(np.concatenate(e))
        if best is None or m<best[0]: best=(m,perm)
        if perm==(1.0,1.5,2.0,2.5): ordered=m
    print(' group K=70.8: MAE ordered C1..C4=1..2.5 -> %.2f ; best mapping %s -> %.2f'%(ordered,best[1],best[0]))
    # 2) per-person K (b1,b2,po fixed), joint over 4 sessions, ordered mapping
    Ks=[];E=[]
    for p in P:
        ss=[s for s in SES if s[0]==p]
        f=lambda lk: np.nanmean(np.concatenate([np.abs(pred(p,c,seg,t,amps[C.index(c)],mode,np.exp(lk))-y) for _,c,seg,t,y in ss]))
        r=minimize_scalar(f,bounds=(np.log(0.1),np.log(1e5)),method='bounded'); Ks.append(np.exp(r.x)); E.append(r.fun)
    Ks=np.array(Ks); print(' per-person K: median %.1f (IQR %.1f-%.1f), MAE mean %.2f'%(np.median(Ks),*np.percentile(Ks,[25,75]),np.nanmean(E)))
    np.save(OUT/('K_%s.npy'%mode),Ks)
