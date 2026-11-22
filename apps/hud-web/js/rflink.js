// RF 연결 계층 — 차량 디스플레이·HUD(role "hud")와 앱(role "app")이 함께 씀
//   ble  : Web Bluetooth로 ESP32-S3 브리지(MSAgent-xxxx)에 직접 연결 (Chrome/Edge, Android Chrome)
//   ws   : 로컬 백엔드(backend/server.py) /ws — 같은 한 줄 JSON
//   demo : 브라우저 안 가짜 컨트롤러 (demo.js)
// 규칙 (PROTOCOL.md §2·§3): 연결 후 5초 안에 HELLO, 양쪽 HB 1초, 5초 무수신 = 끊김, 한 메시지 = 한 줄 ≤ 512 B
import { BLE, HB_PERIOD_MS, LINK_TIMEOUT_MS, MAX_MSG, msg, parseIncoming } from './protocol.js';

export class RFLink {
  /**
   * @param {{role:'app'|'hud', dev:string, dbg?:boolean,
   *          onMessage:(m:object)=>void, onState?:(s:string, info:object)=>void,
   *          onOpen?:()=>void, onLog?:(dir:string, line:string)=>void}} o
   */
  constructor(o) {
    Object.assign(this, { onState: () => {}, onOpen: () => {}, onLog: () => {} }, o);
    this.kind = null; this.state = 'idle'; this.buf = '';
    this.stats = { tx: 0, rx: 0, hbTx: 0, hbRx: null, lastRx: 0, openedAt: 0, name: '' };
    this.queue = Promise.resolve();
    this._timers = [];
    this._manualClose = false;
  }

  // ───────── 상태
  _set(state, extra = {}) {
    if (this.state === state && !extra.force) return;
    this.state = state;
    this.onState(state, { kind: this.kind, ...this.stats, ...extra });
  }

  get age() { return this.stats.lastRx ? performance.now() - this.stats.lastRx : Infinity; }

  // ───────── 수신: 조각을 '\n'까지 합친 뒤 JSON (Notify 하나가 여러 조각일 수 있음)
  _feed(chunk) {
    this.buf += chunk;
    let i;
    while ((i = this.buf.indexOf('\n')) >= 0) {
      const line = this.buf.slice(0, i).trim();
      this.buf = this.buf.slice(i + 1);
      if (line) this._line(line);
    }
    if (this.buf.length > MAX_MSG * 2) { this.onLog('!', '버퍼 초과 → 비움'); this.buf = ''; }
  }

  _line(line) {
    let raw;
    try { raw = JSON.parse(line); } catch { this.onLog('!', 'JSON 아님: ' + line.slice(0, 80)); return; }
    this.stats.rx++; this.stats.lastRx = performance.now();
    if (this.state === 'lost') this._set('connected', { recovered: true });
    const m = parseIncoming(raw);
    if (m?.t !== 'MOT' && m?.t !== 'DBG') this.onLog('←', line);
    if (!m) return; // 모르는 t는 무시 (명세)
    if (m.t === 'HB') this.stats.hbRx = m.seq;
    this.onMessage(m);
  }

  // ───────── 송신
  send(obj) {
    const line = JSON.stringify(obj);
    if (new TextEncoder().encode(line).length > MAX_MSG) { this.onLog('!', '512 B 초과 → 안 보냄'); return false; }
    if (obj.t !== 'HB') this.onLog('→', line);
    if (this.kind === 'ws' && this.ws?.readyState === 1) this.ws.send(line + '\n');
    else if (this.kind === 'ble' && this.rx) {
      // 브리지는 Write 하나를 완결된 줄로 처리 → 한 메시지를 나누지 않고 한 번에 쓴다
      const data = new TextEncoder().encode(line + '\n');
      this.queue = this.queue
        .then(() => (this.rx.writeValueWithoutResponse ? this.rx.writeValueWithoutResponse(data) : this.rx.writeValue(data)))
        .catch((e) => this.onLog('!', '쓰기 실패: ' + e.message));
    } else if (this.kind === 'demo' && this.sim) this.sim.receive(this, obj);
    else return false;
    this.stats.tx++;
    return true;
  }

  // ───────── 연결 후 공통: HELLO → (페이지가 SCR·PROF) → HB 1초 · 5초 감시
  _afterOpen() {
    this.buf = ''; this.stats.openedAt = this.stats.lastRx = performance.now();
    const hello = msg.hello(this.role, this.dev);
    if (this.dbg && this.kind === 'ws') hello.dbg = true; // 로컬 백엔드 전용 확장 필드 (실제 Pi는 무시)
    this.send(hello);
    this.onOpen();
    this._clearTimers();
    this._timers.push(setInterval(() => this.send(msg.hb(++this.stats.hbTx)), HB_PERIOD_MS));
    this._timers.push(setInterval(() => {
      if (this.state === 'connected' && this.age > LINK_TIMEOUT_MS) this._set('lost'); // fail-safe: 페이지가 개입을 모두 끔
    }, 200));
    this._set('connected', { force: true });
  }

  _clearTimers() { this._timers.forEach(clearInterval); this._timers = []; }

  _closed(reason) {
    this._clearTimers();
    this._set('closed', { reason });
    if (!this._manualClose && this.kind === 'ws') setTimeout(() => this.state === 'closed' && this.connectWS(this.url).catch(() => {}), 2000);
  }

  async connectBLE() {
    if (!navigator.bluetooth) throw new Error('Web Bluetooth 미지원 브라우저입니다 (Chrome/Edge, https 또는 localhost)');
    this.close(); this._manualClose = false; this.kind = 'ble'; this._set('connecting');
    const device = await navigator.bluetooth.requestDevice({
      filters: [{ namePrefix: BLE.namePrefix }, { services: [BLE.service] }], optionalServices: [BLE.service],
    });
    this.device = device; this.stats.name = device.name || 'MSAgent';
    device.addEventListener('gattserverdisconnected', () => { if (this.device === device) this._closed('BLE 연결 끊김'); });
    await this._gatt(device);
  }

  async _gatt(device) {
    const server = await device.gatt.connect();
    const svc = await server.getPrimaryService(BLE.service);
    this.rx = await svc.getCharacteristic(BLE.rx);
    const tx = await svc.getCharacteristic(BLE.tx);
    const dec = new TextDecoder();
    tx.addEventListener('characteristicvaluechanged', (e) => this._feed(dec.decode(e.target.value, { stream: true })));
    await tx.startNotifications(); // 암호화 특성 → 첫 연결 때 OS 페어링 (Just Works)
    this._afterOpen();
  }

  async reconnectBLE() {
    if (!this.device) throw new Error('이전에 연결한 브리지가 없습니다');
    this.kind = 'ble'; this._manualClose = false; this._set('connecting');
    await this._gatt(this.device);
  }

  connectWS(url) {
    this.close(); this._manualClose = false; this.kind = 'ws'; this.url = url; this.stats.name = new URL(url).host;
    this._set('connecting');
    return new Promise((resolve, reject) => {
      const ws = new WebSocket(url);
      ws.onopen = () => { this.ws = ws; this._afterOpen(); resolve(); };
      ws.onerror = () => { reject(new Error('서버 연결 실패: ' + url)); };
      ws.onclose = () => { if (this.ws === ws || !this.ws) { this.ws = null; this._closed('서버 연결 종료'); } };
      ws.onmessage = async (e) => {
        const s = typeof e.data === 'string' ? e.data : await e.data.text();
        this._feed(s.endsWith('\n') ? s : s + '\n');
      };
    });
  }

  connectDemo(sim) {
    this.close(); this._manualClose = false; this.kind = 'demo'; this.sim = sim; this.stats.name = 'demo';
    sim.attach(this, (line) => this._feed(line + '\n'));
    this._afterOpen();
  }

  close() {
    this._manualClose = true;
    this._clearTimers();
    const ws = this.ws; this.ws = null;
    try { ws?.close(); } catch {}
    try { this.device?.gatt?.disconnect(); } catch {}
    if (this.sim) this.sim.detach(this);
    this.sim = null; this.rx = null; this.buf = '';
    if (this.kind) this._set('closed', { reason: '사용자 종료' });
  }
}

// 기본 서버 주소: 이 페이지를 제공한 백엔드의 /ws
export const defaultWsUrl = () => (location.protocol === 'https:' ? 'wss://' : 'ws://') + (location.host || 'localhost:8765') + '/ws';

// URL 쿼리로 시작 방식 고르기: ?demo → 브라우저 단독 · ?manual → 버튼으로 직접 · (기본) 백엔드 /ws 자동 연결
// BLE는 브라우저가 사용자 클릭을 요구하므로 자동 연결 불가 → 화면의 [BLE] 버튼
export function autoConnect(link, makeSim) {
  const q = new URLSearchParams(location.search);
  if (q.has('demo')) { link.connectDemo(makeSim()); return 'demo'; }
  if (q.has('manual')) return null;
  link.connectWS(q.get('ws') || defaultWsUrl()).catch(() => {});
  return 'ws';
}
