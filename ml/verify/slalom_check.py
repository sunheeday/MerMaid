# 좌우 슬라럼 집단 비교 (Irmak, Pool, Happee 2021). 먼저 oman_tudelft.py로 out/K_sin.npy를 만든 뒤: python3 ml/verify/slalom_check.py
import pathlib, numpy as np
from scipy.signal import lfilter
DT=0.05
def lp2(x,b):
    al=np.exp(-DT/b); f=lambda u: lfilter([1-al],[1,-al],u); return f(f(x))
def oman(c,K): S=lp2(c,502.4); F=lp2(K*S*c,66.2); return np.maximum(F+S,0)**0.4
K=np.load(pathlib.Path(__file__).with_name('out')/'K_sin.npy')
T=1800; t=np.arange(0,T,DT); A=0.4*9.81
def stim(cycle=49.0, stretch=40.0):
    ph=t%cycle; on=ph<stretch
    return np.abs(np.where(on, A*np.sin(2*np.pi*0.2*ph), 0.0))
for name,c in [('continuous',np.abs(A*np.sin(2*np.pi*0.2*t))),('40 s slalom + 9 s turn',stim())]:
    rates=[];means=[];drop=[]
    mins=np.arange(1,31)
    for k in K:
        o=oman(c,k); m=np.round(np.interp(mins*60,t,o))   # 1분마다 정수 응답
        hit=np.where(m>=7)[0]
        if len(hit): i=hit[0]; drop.append(1); rates.append((7-0)/mins[i]); m[i:]=7
        else: drop.append(0); rates.append((m[-1]-0)/30)
        means.append(m.mean())
    print('%-24s median rate %.2f /min | mean MISC %.1f | dropouts %.0f%%'%(name,np.median(rates),np.mean(means),100*np.mean(drop)))
print('paper internal vision : median rate 0.59 /min | mean MISC 4.2 | dropouts 66%')
print('paper external vision : median rate 0.10 /min | mean MISC 2.3 | dropouts 33%')
