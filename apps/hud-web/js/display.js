// 차량 디스플레이 — RF role "hud" (dev "hud-01"). HUD 화면과 한 연결을 공유하고 BroadcastChannel로 HUD 창에 중계.
// 받는 것: INT tier 1·2, MOT (Tier 2 ON일 때만 스트림), HB, (로컬 백엔드만) DBG
// 보내는 것: HELLO, SCR(on, att=false), OVR(airflow-01 | display-01), HB
import { DEV, MOT_STALE_MS, msg } from './protocol.js';
import { RFLink, autoConnect, defaultWsUrl } from './rflink.js';
import { DemoController } from './demo.js';
import { MotionDots } from './motiondots.js';
import { $, HUD_CHANNEL, Overrides, linkLabel, makeLog } from './common.js';

const S = {
  link: 'idle', t1: false, t2: false, screen: true,
  mot: { ax: 0, ay: 0, pax: null, pay: null }, lastMot: 0,
  cues: null, phones: null, speed: null, everConnected: false,
  onSince: { [DEV.airflow]: null, [DEV.display]: null }, // 학습 안내용: 켜진 시각
};
const NAME = { [DEV.airflow]: '송풍을', [DEV.display]: '앰비언트를' }; // 조사 포함
const hist = []; // {ax, ay} 10 Hz, 30초

// ───────── 무대 맞춤
const stage = $('stage');
function fit() {
  const s = Math.min(innerWidth / 1920, innerHeight / 720);
  stage.style.transform = `translate(-50%, -50%) scale(${s})`;
}
addEventListener('resize', fit); fit();

// ───────── 연결
const log = makeLog($('log'));
const relay = 'BroadcastChannel' in window ? new BroadcastChannel(HUD_CHANNEL) : null;
const link = new RFLink({
  role: 'hud', dev: DEV.hud, dbg: true, // HUD는 항상 hud-01 (RF_MESSAGES.md §5)
  onMessage, onState,
  onLog: (d, line) => { if (!line.includes('"t":"HB"')) log(d, line); }, // HB는 개수만 (로그 상단 지표)
  onOpen: () => link.send(msg.scr(S.screen, false)),
});
const ovr = new Overrides(link, render);
const dots = new MotionDots($('dots'), { color: '255,214,228', gain: 22, rows: 6 });
let sim = null;
const makeSim = () => (sim = new DemoController());

function onMessage(m) {
  if (m.t === 'INT') {
    const dev = m.tier === 1 ? DEV.airflow : m.tier === 2 ? DEV.display : null;
    if (dev) {
      const was = m.tier === 1 ? S.t1 : S.t2;
      if (m.on && !was) S.onSince[dev] = performance.now();
      if (!m.on) S.onSince[dev] = null;
      if (m.tier === 1) S.t1 = m.on; else S.t2 = m.on;
      ovr.confirm(dev);
    }
    relay?.postMessage({ m });
    render();
  } else if (m.t === 'MOT') {
    S.mot = m; S.lastMot = performance.now(); dots.feed(m);
    relay?.postMessage({ m });
  } else if (m.t === 'DBG') {
    S.cues = m.cues || null; S.phones = m.phones || null; S.speed = m.speed;
    const ev = (m.ev || []).filter((e) => e.tag).pop();
    if (ev) ticker(ev.tag, ev.title || ev.text || '');
    render();
  }
}

function onState(state, info) {
  S.link = state;
  if (state === 'connected') S.everConnected = true;
  if (state === 'lost' || state === 'closed') safeOff(); // NFR-01: 통신이 끊기면 화면 개입은 무조건 끔
  $('linkChip').dataset.s = state; $('linkText').textContent = linkLabel(state, info);
  $('bDrop').hidden = info.kind !== 'demo';
  relay?.postMessage({ link: state });
  render();
}

function safeOff() { S.t1 = false; S.t2 = false; S.cues = null; S.onSince[DEV.airflow] = S.onSince[DEV.display] = null; ovr.clear(); dots.zero(); }

relay && (relay.onmessage = (e) => {
  if (e.data?.hello) relay.postMessage({ snap: { link: S.link, t1: S.t1, t2: S.t2, screen: S.screen } });
});
setInterval(() => relay?.postMessage({ link: S.link }), 1000); // HUD 창이 디스플레이가 살아 있는지 알게

// ───────── 화면
function headline() {
  if (S.link === 'lost' || (S.link === 'closed' && S.everConnected)) return ['연결이 끊겨 개입을 모두 껐어요', '통신이 돌아오면 컨트롤러가 현재 상태를 다시 보내 줘요.'];
  if (S.link !== 'connected') return ['컨트롤러에 연결해 주세요', 'BLE 브리지(MSAgent) 또는 백엔드 서버에 연결하면 에이전트가 동작해요.'];
  if (S.t2) return ['빛이 차의 움직임을 미리 알려 드려요', '커브에 들어가기 전에 앰비언트 빛과 화면 가장자리 점이 먼저 움직여요.'];
  if (S.t1) return ['흔들림이 쌓이고 있어 송풍을 먼저 켰어요', '가장 가볍고 먼저 켜지는 개입이에요. 필요 없으면 꺼 주세요 — 다음부터 덜 켜져요.'];
  return ['편안한 주행이에요', '흔들림이 쌓이면 멀미가 오기 전에 먼저 움직여요.'];
}

function render() {
  const [h, s] = headline();
  $('headline').textContent = h; $('subline').textContent = s;
  const live = S.link === 'connected';
  // 연결 전·끊김: 공용 장치 상태는 '알 수 없음', 스위치 비활성 (화면정의서 APP-05 ⑦과 같은 규칙)
  for (const [n, dev, actual] of [[1, DEV.airflow, S.t1], [2, DEV.display, S.t2]]) {
    const v = ovr.view(dev, actual);
    $('tier' + n).dataset.on = live && v.on;
    $('tier' + n).dataset.unknown = !live;
    $('pill' + n).textContent = !live ? '알 수 없음' : v.pending ? '요청 중' : v.on ? '켜짐' : '꺼짐';
    const sw = $('sw' + n);
    sw.setAttribute('aria-checked', live && v.on); sw.dataset.pending = v.pending; sw.disabled = !live;
  }
  const known = !!S.cues;
  $('tier3').dataset.on = known && (S.cues.A || S.cues.B);
  for (const el of document.querySelectorAll('.seat')) {
    const k = el.dataset.seat;
    el.dataset.known = known && (!S.phones || S.phones[k]);
    el.dataset.on = known && !!S.cues[k];
    el.title = !known ? '폰 상태는 각 폰에서 관리 (HUD는 Tier 3 INT를 받지 않음)' : S.phones && !S.phones[k] ? `${k} 폰 미연결` : `${k} 모션 큐 ${S.cues[k] ? 'ON' : 'OFF'}`;
  }
  $('fan').dataset.on = S.t1; $('fanText').textContent = !live ? '송풍 —' : S.t1 ? '송풍 작동 중' : '송풍 대기';
  dots.setOn(S.t2 && live);
  $('safe').hidden = !(S.link === 'lost' || (S.link === 'closed' && S.everConnected));
  $('screenOff').hidden = S.screen;
  if (performance.now() >= labelLockUntil) $('bScr').textContent = S.screen ? '화면 ON · 길게 눌러 끄기' : '화면 OFF';
  $('bScr').setAttribute('aria-pressed', S.screen);
  $('speed').hidden = S.speed == null; if (S.speed != null) $('speed').textContent = `${Math.round(S.speed)} km/h`;
}

let tickTimer = 0;
function ticker(tag, text) {
  $('ticker').hidden = false; $('tickTag').textContent = tag; $('tickText').textContent = text;
  clearTimeout(tickTimer); tickTimer = setTimeout(() => ($('ticker').hidden = true), 9000);
}

// ───────── 애니메이션 (glow, G-미터, 그래프)
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const spark = $('spark'), sg = spark.getContext('2d');
let lastSample = 0;
function frame(now) {
  if (now - S.lastMot > MOT_STALE_MS) S.mot = { ax: 0, ay: 0, pax: null, pay: null };
  const { ax, ay, pax, pay } = S.mot;
  const on = S.t2 && S.link === 'connected';
  // glow: 예측값이 있으면 그쪽을 먼저 (빛이 차보다 먼저 움직임). ay + = 왼쪽
  const lead = pay ?? ay;
  $('glowL').style.opacity = on ? clamp(lead / 2.2, 0, 1) * 0.95 : 0;
  $('glowR').style.opacity = on ? clamp(-lead / 2.2, 0, 1) * 0.95 : 0;
  // G-미터: 가속 방향으로 공이 움직임 (위 = 앞 가속, 왼쪽 = 왼쪽 가속)
  const k = 30;
  $('gBall').setAttribute('cx', clamp(-ay * k, -88, 88)); $('gBall').setAttribute('cy', clamp(-ax * k, -88, 88));
  const g = $('gGhost');
  g.style.opacity = pax != null ? 0.9 : 0;
  if (pax != null) { g.setAttribute('cx', clamp(-pay * k, -88, 88)); g.setAttribute('cy', clamp(-pax * k, -88, 88)); }
  $('vAx').textContent = ax.toFixed(2); $('vAy').textContent = ay.toFixed(2);
  $('vPred').textContent = pax != null ? `${pax.toFixed(1)} / ${pay.toFixed(1)}` : '—';
  if (now - lastSample > 100) { lastSample = now; drawSpark(); }
  $('mediaProg').style.width = ((now / 1000) % 240) / 2.4 + '%';
  requestAnimationFrame(frame);
}

function drawSpark() {
  const d = Math.min(devicePixelRatio || 1, 2), r = spark.getBoundingClientRect();
  if (spark.width !== Math.round(r.width * d)) { spark.width = r.width * d; spark.height = r.height * d; }
  const W = spark.width, H = spark.height, mid = H / 2, sy = H / 2 / 3;
  sg.clearRect(0, 0, W, H);
  sg.strokeStyle = 'rgba(246,243,240,0.08)'; sg.lineWidth = d;
  for (const v of [-2, 0, 2]) { sg.beginPath(); sg.moveTo(0, mid - v * sy); sg.lineTo(W, mid - v * sy); sg.stroke(); }
  const line = (key, color) => {
    sg.strokeStyle = color; sg.lineWidth = 2 * d; sg.beginPath();
    hist.forEach((p, i) => { const x = W - (hist.length - 1 - i) * (W / 299); const y = mid - clamp(p[key], -3, 3) * sy; i ? sg.lineTo(x, y) : sg.moveTo(x, y); });
    sg.stroke();
  };
  line('ax', 'rgba(167,157,153,0.8)'); line('ay', '#ff4f8b');
  sg.fillStyle = '#a79d99'; sg.font = `${12 * d}px sans-serif`;
  sg.fillText('좌우 ay', 6 * d, 16 * d); sg.fillStyle = '#ff4f8b'; sg.fillRect(56 * d, 9 * d, 14 * d, 3 * d);
  sg.fillStyle = '#a79d99'; sg.fillText('전후 ax   최근 30초 · m/s²', 80 * d, 16 * d);
}
requestAnimationFrame(frame);
// 그래프 표본은 화면 그리기와 상관없이 10 Hz (탭이 가려져도 기록이 끊기지 않게)
setInterval(() => {
  const fresh = performance.now() - S.lastMot <= MOT_STALE_MS;
  hist.push(fresh ? { ax: S.mot.ax, ay: S.mot.ay } : { ax: 0, ay: 0 });
  if (hist.length > 300) hist.shift();
}, 100);

setInterval(() => { $('clock').textContent = new Date().toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit', hour12: false }); }, 1000);
$('clock').textContent = new Date().toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit', hour12: false });

// ───────── 조작
// 수동 조작 + 학습 안내 (controller/common/POLICY.md: 켜진 지 30초 안에 끄면 허용도 +1, 끈 기기는 300초 쿨다운,
// 꺼진 것을 직접 켜면 −1. HUD 버튼은 민감도를 바꾸지 않음). 판단은 컨트롤러가 하고 여기서는 설명만 보여 줌
function manual(dev, actual) {
  const want = !ovr.view(dev, actual).on;
  if (!ovr.request(dev, want)) return;
  const since = S.onSince[dev];
  if (!want && since != null && performance.now() - since < 30000) ticker('LEARN', `켜진 지 30초 안에 ${NAME[dev]} 껐어요 · 다음부터 덜 켜지고, 5분 동안 다시 켜지지 않아요`);
  else if (!want) ticker('LEARN', `${NAME[dev]} 껐어요 · 5분 동안 자동으로 다시 켜지지 않아요`);
  else ticker('LEARN', `${NAME[dev]} 직접 켰어요 · 다음부터 조금 더 일찍 켜져요`);
}
$('sw1').onclick = () => manual(DEV.airflow, S.t1);
$('sw2').onclick = () => manual(DEV.display, S.t2);
$('bBle').onclick = () => link.connectBLE().catch((e) => { log('!', e.message); onState('closed', { reason: e.message.slice(0, 40) }); });
$('bWs').onclick = () => link.connectWS(defaultWsUrl()).catch((e) => log('!', e.message));
$('bDemo').onclick = () => link.connectDemo(makeSim());
$('bDrop').onclick = () => sim?.drop(7);
$('bHud').onclick = () => open('hud.html', 'mermaid-hud', 'width=900,height=420');
// 화면 ON/OFF = Tier 2 게이트 (SCR on). 끄기는 1.2초 길게 눌러야 함 — 시연 중 실수 방지 (DSP-F-07)
const setScreen = (on) => {
  S.screen = on; if (link.state === 'connected') link.send(msg.scr(on, false));
  relay?.postMessage({ screen: on }); render();
};
const HOLD_MS = 1200;
let holdStart = 0, holdRaf = 0, labelLockUntil = 0;
function holdTick() {
  const p = Math.min(1, (performance.now() - holdStart) / HOLD_MS);
  $('bScr').style.setProperty('--hold', p * 100 + '%');
  if (p >= 1) { endHold(true); setScreen(false); }
}
function startHold(e) {
  if (!S.screen) return;
  e.preventDefault();
  holdStart = performance.now(); $('bScr').dataset.holding = 'true';
  labelLockUntil = Infinity; $('bScr').textContent = '계속 누르면 꺼져요';
  holdRaf = setInterval(holdTick, 16); // rAF 대신 타이머: 화면이 가려져도 동작
}
function endHold(done = false) {
  clearInterval(holdRaf); holdRaf = 0;
  const short = !done && performance.now() - holdStart < HOLD_MS;
  $('bScr').dataset.holding = 'false'; $('bScr').style.setProperty('--hold', '0%');
  labelLockUntil = short ? performance.now() + 1500 : 0;
  if (short) { $('bScr').textContent = '길게 눌러 끄기'; setTimeout(render, 1550); }
  else render();
}
$('bScr').addEventListener('pointerdown', startHold);
['pointerup', 'pointerleave', 'pointercancel'].forEach((ev) => $('bScr').addEventListener(ev, () => { if (holdRaf) endHold(); }));
$('bScr').addEventListener('keydown', (e) => { if ((e.key === 'Enter' || e.key === ' ') && !e.repeat && !holdRaf) startHold(e); });
$('bScr').addEventListener('keyup', (e) => { if ((e.key === 'Enter' || e.key === ' ') && holdRaf) endHold(); });
$('screenOff').onclick = () => setScreen(true);
$('bLog').onclick = () => { $('drawer').hidden = !$('drawer').hidden; log('·', '로그 열림'); };
$('bLogClose').onclick = () => ($('drawer').hidden = true);
setInterval(() => {
  const st = link.stats;
  $('stat').textContent = `TX ${st.tx} · RX ${st.rx} · HB↑${st.hbTx} HB↓${st.hbRx ?? '-'} · 마지막 수신 ${isFinite(link.age) ? (link.age / 1000).toFixed(1) + 's' : '-'}`;
}, 500);
addEventListener('keydown', (e) => {
  if (e.key === 'l' || e.key === 'L') $('bLog').click();
  if (e.key === 'f' || e.key === 'F') document.fullscreenElement ? document.exitFullscreen() : document.documentElement.requestFullscreen();
  if (e.key === 'h' || e.key === 'H') $('bHud').click();
});

render();
autoConnect(link, makeSim);
