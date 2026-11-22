// 브라우저 단독 데모용 가짜 컨트롤러 (P4·P5·P6 대역). ?demo 일 때만 사용.
// 실제 P5 규칙을 따른다 (docs/controller/RF_MESSAGES.md §4):
//   Tier 1·2 (공용) → HUD와 모든 폰에 INT, HUD의 MOT 스트림 = Tier 2
//   Tier 3 → 그 폰에만 INT + MOT 스트림
// 시나리오: 90초 반복 — 직선 → 와인딩(좌우 가속) → 직선. 판단 시각은 고정값(시연용 합성)
import { DEV } from './protocol.js';

const LOOP_S = 90;
const PLAN = [ // [초, 기기, on]
  [14, DEV.airflow, true], [24, DEV.display, true], [34, 'phone', true],
  [70, 'phone', false], [76, DEV.display, false], [82, DEV.airflow, false],
];

export function motionAt(t) {
  const s = ((t % LOOP_S) + LOOP_S) % LOOP_S;
  const w = s < 10 ? 0 : s < 16 ? (s - 10) / 6 : s < 66 ? 1 : s < 72 ? 1 - (s - 66) / 6 : 0;
  const ay = w * (2.1 * Math.sin(s * 0.42) + 0.6 * Math.sin(s * 1.3));
  const ax = 0.35 * Math.sin(s * 0.21) + (s < 6 ? 1.2 : 0) - (s > 84 ? 0.9 : 0);
  return { ax, ay };
}

export class DemoController {
  constructor() {
    this.links = new Set(); this.t0 = performance.now();
    this.st = { [DEV.airflow]: false, [DEV.display]: false };
    this.cues = new Map();   // phone dev → on
    this.cooldown = new Map(); // dev → 끝나는 시각(s)
    this.silentUntil = 0; this.seq = 0; this.lastPlan = -1; this.profiles = new Map();
    this.timer = setInterval(() => this.tick(), 100);
    this.hb = setInterval(() => this.broadcast({ t: 'HB', seq: ++this.seq }), 1000);
  }
  get t() { return (performance.now() - this.t0) / 1000; }
  get loopT() { return this.t % LOOP_S; }

  attach(link, deliver) { link._demoDeliver = deliver; this.links.add(link); }
  detach(link) { this.links.delete(link); }

  _tx(link, obj) { if (performance.now() >= this.silentUntil) link._demoDeliver?.(JSON.stringify(obj)); }
  broadcast(obj, filter = () => true) { for (const l of this.links) if (l._reg && filter(l)) this._tx(l, obj); }

  /** 연결 끊김 재현: s초 동안 아무것도 안 보냄 (기기 쪽 5초 감시 → 안전 OFF) */
  drop(s = 6) { this.silentUntil = performance.now() + s * 1000; }

  _sendState(link) {
    this._tx(link, { t: 'INT', tier: 1, on: this.st[DEV.airflow] });
    this._tx(link, { t: 'INT', tier: 2, on: this.st[DEV.display] });
    if (link._reg.role === 'app') this._tx(link, { t: 'INT', tier: 3, on: !!this.cues.get(link._reg.dev) });
  }

  set(dev, on, why = 'auto') {
    if (why === 'auto' && on && (this.cooldown.get(dev) || 0) > this.t) return; // 승객이 끈 기기는 쿨다운
    if (dev === DEV.airflow || dev === DEV.display) {
      this.st[dev] = on;
      this.broadcast({ t: 'INT', tier: dev === DEV.airflow ? 1 : 2, on });
    } else {
      this.cues.set(dev, on);
      this.broadcast({ t: 'INT', tier: 3, on }, (l) => l._reg.dev === dev);
    }
  }

  receive(link, m) {
    if (m.t === 'HELLO') {
      link._reg = { role: m.role, dev: m.role === 'hud' ? DEV.hud : m.dev };
      if (m.role === 'app' && this.profiles.has(m.dev)) this._tx(link, this.profiles.get(m.dev));
      this._sendState(link);
      return;
    }
    if (!link._reg) return; // HELLO 전 메시지 버림
    if (m.t === 'OVR') {
      const shared = m.dev === DEV.airflow || m.dev === DEV.display;
      if (!shared && !(link._reg.role === 'app' && m.dev === link._reg.dev)) return; // 폰 큐는 그 폰 자신만
      if (!m.on) this.cooldown.set(m.dev, this.t + 30);
      this.set(m.dev, m.on, 'user');
    } else if (m.t === 'PROF' && link._reg.role === 'app') {
      this.profiles.set(link._reg.dev, { t: 'PROF', mssq: m.mssq, sens: m.sens });
    }
  }

  tick() {
    const s = this.loopT;
    if (s < this.lastPlan) { this.cooldown.clear(); } // 새 바퀴
    for (const [at, dev, on] of PLAN) {
      if (this.lastPlan < at && s >= at) {
        if (dev === 'phone') for (const l of this.links) { if (l._reg?.role === 'app') this.set(l._reg.dev, on); }
        else this.set(dev, on);
      }
    }
    this.lastPlan = s;
    const now = motionAt(this.t), ahead = motionAt(this.t + 2);
    const r = (v) => Math.round(v * 1000) / 1000;
    const mot = { t: 'MOT', ax: r(now.ax), ay: r(now.ay), pax: r(ahead.ax), pay: r(ahead.ay) };
    this.broadcast(mot, (l) => (l._reg.role === 'hud' ? this.st[DEV.display] : !!this.cues.get(l._reg.dev)));
  }
}
