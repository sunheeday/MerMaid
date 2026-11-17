// 화면 공용 도우미: RF 로그, 연결 칩, 수동 조작(OVR) 대기 처리, HUD 중계
export const $ = (id) => document.getElementById(id);

export function makeLog(el, max = 300) {
  const rows = [];
  return (dir, line) => {
    const d = new Date(), p = (n) => String(n).padStart(2, '0');
    const t = `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
    rows.push({ cls: dir === '←' ? 'rx' : dir === '→' ? 'tx' : 'err', s: `${t} ${dir} ${line}` });
    if (rows.length > max) rows.shift();
    if (!el || el.closest('[hidden]')) return;
    el.innerHTML = rows.map((r) => `<div class="${r.cls}">${r.s.replace(/</g, '&lt;')}</div>`).join('');
    el.scrollTop = el.scrollHeight;
  };
}

const KIND = { ble: 'BLE', ws: '서버', demo: '데모' };
export function linkLabel(state, info = {}) {
  const k = KIND[info.kind] || '';
  switch (state) {
    case 'connected': return `${k} 연결됨${info.name && info.kind !== 'demo' ? ' · ' + info.name : ''}`;
    case 'connecting': return `${k} 연결 중…`;
    case 'lost': return '신호 없음 5초 · 안전 OFF';
    case 'closed': return `연결 끊김${info.reason ? ' · ' + info.reason : ''}`;
    default: return '연결 안 됨';
  }
}

/**
 * 수동 조작: 누르면 바로 바뀐 것처럼 보이고(낙관적), 컨트롤러의 INT 회신이 오면 확정.
 * 3초 안에 회신이 없으면 원래 상태로 되돌림. 실제 결정은 컨트롤러가 한다 (P4).
 */
export class Overrides {
  constructor(link, onChange) { this.link = link; this.onChange = onChange; this.pending = new Map(); }
  request(dev, on) {
    if (this.link.state !== 'connected') return false;
    clearTimeout(this.pending.get(dev)?.timer);
    const timer = setTimeout(() => { this.pending.delete(dev); this.onChange(); }, 3000);
    this.pending.set(dev, { on, timer });
    this.link.send({ t: 'OVR', dev, on });
    this.onChange();
    return true;
  }
  /** 컨트롤러에서 이 기기의 INT가 오면 대기 해제 */
  confirm(dev) { const p = this.pending.get(dev); if (p) { clearTimeout(p.timer); this.pending.delete(dev); } }
  view(dev, actual) { const p = this.pending.get(dev); return p ? { on: p.on, pending: true } : { on: actual, pending: false }; }
  clear() { for (const p of this.pending.values()) clearTimeout(p.timer); this.pending.clear(); }
}

// 차량 디스플레이 ↔ HUD 창 중계. HUD 연결(hud-01)은 하나만 등록되므로(RF_MESSAGES.md §2-1)
// 같은 PC에서 HUD 창을 따로 띄우면 디스플레이의 연결을 함께 쓴다.
export const HUD_CHANNEL = 'mermaid-hud-relay';
