// HUD — 가속 방향 앰비언트 큐 (Tier 2, display-01). 도로에 정합할 필요 없는 방향 표시만.
// 연결: 같은 브라우저에 차량 디스플레이 창이 있으면 그 연결을 따름(중계), 없으면 직접 role "hud"로 연결.
import { DEV, MOT_STALE_MS, msg } from './protocol.js';
import { RFLink, autoConnect, defaultWsUrl } from './rflink.js';
import { DemoController } from './demo.js';
import { $, HUD_CHANNEL, linkLabel } from './common.js';

// screen: 차량 디스플레이의 화면 ON/OFF(= Tier 2 게이트). HUD는 차량 화면 앱의 다른 보기라서 함께 꺼짐
const S = { link: 'idle', t1: false, t2: false, screen: true, mot: { ax: 0, ay: 0, pax: null, pay: null }, lastMot: 0, mode: 'self', lastRelay: 0 };
const opt = { mirror: 'none', bright: 0.85, gain: 1 };
try { Object.assign(opt, JSON.parse(localStorage.getItem('mermaid-hud-opt') || '{}')); } catch {}
const saveOpt = () => { try { localStorage.setItem('mermaid-hud-opt', JSON.stringify(opt)); } catch {} };

// ───────── 상태 반영
function setLink(state, info = {}) {
  S.link = state;
  if (state !== 'connected') { S.t1 = false; S.t2 = false; alpha = 0; } // 안전 OFF: 페이드 없이 즉시 (NFR-01)
  $('linkChip').dataset.s = state; $('ld').dataset.s = state;
  $('linkText').textContent = S.mode === 'follow' ? (state === 'connected' ? '디스플레이 연결 공유 중' : linkLabel(state, info)) : linkLabel(state, info);
  showAir();
}
function showAir() { $('air').dataset.on = S.t1 && S.screen && S.link === 'connected'; }
function onMessage(m) {
  if (m.t === 'INT') { if (m.tier === 1) S.t1 = m.on; if (m.tier === 2) S.t2 = m.on; showAir(); }
  else if (m.t === 'MOT') { S.mot = m; S.lastMot = performance.now(); }
}

// ───────── 직접 연결
let link = null;
function standalone() {
  S.mode = 'self'; $('connRow').hidden = false;
  link = new RFLink({ role: 'hud', dev: DEV.hud, onMessage, onState: setLink, onOpen: () => link.send(msg.scr(true, false)) });
  $('bBle').onclick = () => link.connectBLE().catch((e) => setLink('closed', { reason: e.message.slice(0, 30) }));
  $('bWs').onclick = () => link.connectWS(defaultWsUrl()).catch(() => {});
  $('bDemo').onclick = () => link.connectDemo(new DemoController());
  autoConnect(link, () => new DemoController());
}

// ───────── 디스플레이 창 따르기 (BroadcastChannel)
// ?demo · ?ws · ?manual 을 직접 주면 따르지 않고 혼자 연결 (다른 기기에서 HUD만 띄울 때)
const forced = ['demo', 'ws', 'manual'].some((k) => new URLSearchParams(location.search).has(k));
const ch = 'BroadcastChannel' in window && !forced ? new BroadcastChannel(HUD_CHANNEL) : null;
let decided = false;
if (ch) {
  ch.onmessage = (e) => {
    const d = e.data || {};
    if (S.mode === 'self' && decided) return;
    if (!decided && (d.snap || d.link)) { decided = true; S.mode = 'follow'; $('connRow').hidden = true; }
    S.lastRelay = performance.now();
    if (d.snap) { S.t1 = d.snap.t1; S.t2 = d.snap.t2; if (d.snap.screen != null) S.screen = d.snap.screen; setLink(d.snap.link); }
    if (d.link && d.link !== S.link) setLink(d.link);
    if (d.screen != null) { S.screen = d.screen; if (!S.screen) alpha = 0; showAir(); }
    if (d.m) onMessage(d.m);
  };
  ch.postMessage({ hello: true });
}
setTimeout(() => { if (!decided) { decided = true; standalone(); } }, ch ? 700 : 0);
setInterval(() => { // 디스플레이 창이 닫히면 5초 뒤 끊김 처리
  if (S.mode === 'follow' && performance.now() - S.lastRelay > 5000 && S.link === 'connected') setLink('lost');
}, 500);

// ───────── 그리기
const cv = $('cue'), g = cv.getContext('2d');
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
let alpha = 0, px = 0, py = 0, last = performance.now();
function resize() { const d = Math.min(devicePixelRatio || 1, 2); cv.width = innerWidth * d; cv.height = innerHeight * d; }
addEventListener('resize', resize); resize();

function frame(now) {
  const dt = Math.min(0.1, (now - last) / 1000); last = now;
  if (now - S.lastMot > MOT_STALE_MS) S.mot = { ax: 0, ay: 0, pax: null, pay: null };
  const on = S.t2 && S.link === 'connected' && S.screen;
  alpha +=((on ? 1 : 0) - alpha) * (1 - Math.exp(-dt * 6));
  // 예측값이 있으면 그쪽으로 먼저 (빛이 차보다 먼저 움직임). ay + = 왼쪽 가속 → 화면 왼쪽
  const { ax, ay, pax, pay } = S.mot;
  const ly = pay ?? ay, lx = pax ?? ax;
  const k = 1 - Math.exp(-dt * 5);
  px += (clamp(-ly / 2.5, -1, 1) - px) * k; py += (clamp(lx / 2, -1, 1) - py) * k;
  draw();
  requestAnimationFrame(frame);
}

function draw() {
  const W = cv.width, H = cv.height;
  g.clearRect(0, 0, W, H);
  if (alpha < 0.01) return;
  const a = alpha * opt.bright, mag = Math.min(1, Math.hypot(px, py));
  const cx = W / 2 + px * W * 0.36 * opt.gain, cy = H * 0.62 - py * H * 0.16 * opt.gain;
  const base = H * 0.62;
  // 1) 바닥의 기준선 (항상 희미하게)
  g.strokeStyle = `rgba(255,79,139,${0.18 * a})`; g.lineWidth = Math.max(2, H * 0.006); g.lineCap = 'round';
  g.beginPath(); g.moveTo(W * 0.12, base); g.lineTo(W * 0.88, base); g.stroke();
  // 2) 가속 방향 쪽으로 미끄러지는 빛 덩어리
  const rad = Math.min(W, H) * (0.22 + 0.14 * mag) * opt.gain;
  // 가로로 늘린 원형 그라데이션 = 가장자리가 부드러운 타원
  g.save(); g.translate(cx, cy); g.scale(1.7, 0.6);
  const grd = g.createRadialGradient(0, 0, 0, 0, 0, rad);
  grd.addColorStop(0, `rgba(255,120,170,${0.95 * a})`);
  grd.addColorStop(0.35, `rgba(255,79,139,${0.5 * a})`);
  grd.addColorStop(1, 'rgba(255,79,139,0)');
  g.fillStyle = grd; g.fillRect(-rad, -rad, rad * 2, rad * 2);
  g.restore();
  // 3) 그쪽 가장자리 띠 (세기 = 좌우 가속 크기)
  const side = px < 0 ? 0 : W, edge = Math.abs(px);
  if (edge > 0.05) {
    const eg = g.createLinearGradient(side, 0, side + (px < 0 ? 1 : -1) * W * 0.22, 0);
    eg.addColorStop(0, `rgba(255,79,139,${0.6 * edge * a})`); eg.addColorStop(1, 'rgba(255,79,139,0)');
    g.fillStyle = eg; g.fillRect(px < 0 ? 0 : W * 0.78, 0, W * 0.22, H);
  }
  // 4) 방향 갈매기표 (크기 0.25 이상일 때)
  if (Math.abs(px) > 0.25) {
    const dir = Math.sign(px), n = Math.min(3, Math.ceil(Math.abs(px) * 3));
    g.strokeStyle = `rgba(255,214,228,${0.7 * a})`; g.lineWidth = Math.max(3, H * 0.012);
    for (let i = 0; i < n; i++) {
      const x = W / 2 + dir * (W * 0.06 + i * W * 0.045), s = H * 0.05;
      g.beginPath(); g.moveTo(x - dir * s * 0.6, base - s); g.lineTo(x + dir * s * 0.4, base); g.lineTo(x - dir * s * 0.6, base + s); g.stroke();
    }
  }
}
requestAnimationFrame(frame);

// ───────── 설정 패널 (움직이면 보이고 3초 뒤 숨김)
const panel = $('panel'); let hideT = 0;
const poke = () => { panel.dataset.hide = 'false'; clearTimeout(hideT); hideT = setTimeout(() => (panel.dataset.hide = 'true'), 3500); };
['mousemove', 'touchstart', 'keydown'].forEach((ev) => addEventListener(ev, poke, { passive: true }));
poke();
function applyOpt() {
  $('view').dataset.mirror = opt.mirror;
  for (const b of $('mirror').children) b.setAttribute('aria-checked', b.dataset.v === opt.mirror);
  $('bright').value = Math.round(opt.bright * 100); $('gain').value = Math.round(opt.gain * 100);
  saveOpt();
}
$('mirror').onclick = (e) => { const v = e.target.dataset?.v; if (v) { opt.mirror = v; applyOpt(); } };
$('bright').oninput = (e) => { opt.bright = e.target.value / 100; applyOpt(); };
$('gain').oninput = (e) => { opt.gain = e.target.value / 100; applyOpt(); };
addEventListener('keydown', (e) => {
  if (e.key === 'm' || e.key === 'M') { opt.mirror = { none: 'x', x: 'y', y: 'none' }[opt.mirror]; applyOpt(); }
  if (e.key === 'f' || e.key === 'F') document.fullscreenElement ? document.exitFullscreen() : document.documentElement.requestFullscreen();
});
applyOpt();
// 화면이 켜져 있는 동안 잠들지 않게 (지원 브라우저만)
navigator.wakeLock?.request('screen').catch(() => {});
