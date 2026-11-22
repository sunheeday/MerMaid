// 시각 모션 큐(점) — 화면 가장자리의 점들이 차의 가속과 반대로(관성 방향) 밀려 시각·전정 감각의 충돌을 줄인다.
// 축 규약 (PROTOCOL.md §5, RF_MESSAGES.md §5 임시): ax + = 앞으로 가속, ay + = 왼쪽 가속 (ISO 8855)
//   ay + (좌회전) → 점은 오른쪽으로, ax + (가속) → 점은 아래로, 제동 → 위로
// 예측값(pax·pay)이 있으면 30% 섞어 조금 먼저 움직인다.
export class MotionDots {
  /** @param {HTMLCanvasElement} canvas  @param {{color?:string, gain?:number, cols?:number, rows?:number}} opt */
  constructor(canvas, opt = {}) {
    this.c = canvas; this.g = canvas.getContext('2d');
    this.opt = { color: '255,255,255', gain: 26, cols: 2, rows: 7, ...opt };
    this.on = false; this.alpha = 0; this.a = { x: 0, y: 0 }; this.target = { x: 0, y: 0 };
    this._raf = 0; this._last = performance.now();
    new ResizeObserver(() => this._resize()).observe(canvas);
    this._resize();
    this._loop = this._loop.bind(this);
    this._raf = requestAnimationFrame(this._loop);
  }

  _resize() {
    const r = this.c.getBoundingClientRect(), d = Math.min(devicePixelRatio || 1, 2);
    this.c.width = Math.max(1, r.width * d); this.c.height = Math.max(1, r.height * d); this.dpr = d;
  }

  setOn(on) { this.on = !!on; }

  /** MOT 한 개 (m/s²) */
  feed(m) {
    const ax = m.pax != null ? 0.7 * m.ax + 0.3 * m.pax : m.ax;
    const ay = m.pay != null ? 0.7 * m.ay + 0.3 * m.pay : m.ay;
    this.target = { x: ay, y: ax };
  }

  zero() { this.target = { x: 0, y: 0 }; }

  _loop(now) {
    const dt = Math.min(0.1, (now - this._last) / 1000); this._last = now;
    const k = 1 - Math.exp(-dt * 6);
    this.a.x += (this.target.x - this.a.x) * k; this.a.y += (this.target.y - this.a.y) * k;
    this.alpha += ((this.on ? 1 : 0) - this.alpha) * (1 - Math.exp(-dt * 8));
    this._draw();
    this._raf = requestAnimationFrame(this._loop);
  }

  _draw() {
    const { g, c, dpr } = this, W = c.width, H = c.height;
    g.clearRect(0, 0, W, H);
    if (this.alpha < 0.01) return;
    const { cols, rows, color, gain } = this.opt;
    // 최대 이동 = gain px (±3 m/s²에서). 가장자리 여백을 그만큼 더 두어 점이 화면 밖으로 나가지 않게
    const clampA = (v) => Math.max(-3, Math.min(3, v)) / 3;
    const maxOff = Math.min(gain * dpr, Math.min(W, H) * 0.08);
    const dx = clampA(this.a.x) * maxOff, dy = clampA(this.a.y) * maxOff;
    const r = Math.max(3, Math.min(W, H) * 0.012), pad = r * 2.4 + maxOff, gap = (H - pad * 2) / (rows - 1);
    g.fillStyle = `rgba(${color},${0.78 * this.alpha})`;
    g.shadowColor = `rgba(${color},${0.5 * this.alpha})`; g.shadowBlur = r * 1.6;
    const dot = (x, y) => { g.beginPath(); g.arc(x + dx, y + dy, r, 0, Math.PI * 2); g.fill(); };
    for (let ci = 0; ci < cols; ci++) {
      const off = pad + ci * r * 4.2;
      for (let ri = 0; ri < rows; ri++) {
        const y = pad + ri * gap + (ci % 2 ? gap / 2 : 0);
        if (y > H - pad + 1) continue;
        dot(off, y); dot(W - off, y);
      }
    }
    // 위·아래 가장자리에 한 줄씩 (전후 가속이 잘 보이게)
    const n = Math.max(4, Math.round((W - pad * 2) / (gap * 1.4)));
    for (let i = 1; i < n; i++) { const x = pad + i * (W - pad * 2) / n; dot(x, pad * 0.9); dot(x, H - pad * 0.9); }
    g.shadowBlur = 0;
  }
}
