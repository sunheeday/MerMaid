// 탑승객 앱 (웹 시연판) — 화면정의서 MM-UI-001 v0.1의 APP-01~07 · OVL-01/02 · SYS-01을 같은 규격(384 × 832 dp)으로.
// 실제 앱은 android/ (Kotlin). 이 웹판은 시연·백엔드 시험용: 권한은 흐름만, 타 앱 위 오버레이·알림창은 시뮬레이션.
// RF: role "app", dev = 이 브라우저의 UUID. 받는 것 INT·MOT·PROF·HB / 보내는 것 HELLO·SCR·OVR·PROF·HB
import { DEV, MOT_STALE_MS, MSSQ, msg, persistentId, sensFromMssq } from './protocol.js';
import { RFLink, defaultWsUrl } from './rflink.js';
import { DemoController } from './demo.js';
import { $, Overrides, makeLog } from './common.js';

const ME = persistentId('mermaid-app-dev');
const Q = new URLSearchParams(location.search);

// ───────── 저장 (이 브라우저에만)
const KEY = 'mermaid-app-v2';
const DEF = {
  onboarded: false, perms: { bt: false, notif: false, overlay: false, battery: false },
  bridge: null, auto: true,
  profile: { has: false, mssq: MSSQ.def.mssq, sens: MSSQ.def.sens, answers: '' },
  cue: { gain: 24, alpha: 0.8, amber: false, pill: true },
};
let S = load();
function load() {
  try { const v = JSON.parse(localStorage.getItem(KEY) || 'null'); return v ? { ...DEF, ...v, perms: { ...DEF.perms, ...v.perms }, cue: { ...DEF.cue, ...v.cue }, profile: { ...DEF.profile, ...v.profile } } : structuredClone(DEF); }
  catch { return structuredClone(DEF); }
}
function save() { try { localStorage.setItem(KEY, JSON.stringify(S)); } catch {} }

// ───────── 실시간 상태
const L = { link: 'idle', kind: '', t1: false, t2: false, t3: false, ctrl: null, scr: { on: null, att: null }, ever: false };
const mot = { x: 0, y: 0, at: 0 };

// ───────── RF 연결
const log = makeLog($('dgLog'));
// 화면정의서 APP-07: MOT·HB는 개수만 (HB 보냄/받음 칸) → 로그에서는 뺌
const link = new RFLink({ role: 'app', dev: ME, onMessage, onState, onOpen, onLog: (d, line) => { if (!line.includes('"t":"HB"')) log(d, line); } });
const ovr = new Overrides(link, render);
let sim = null;

function onOpen() {
  if (S.profile.has) link.send(msg.prof(S.profile.mssq, S.profile.sens));
  L.scr = { on: null, att: null }; sendScr();
}

function onMessage(m) {
  if (m.t === 'INT') {
    const key = ['t1', 't2', 't3'][m.tier - 1], was = L[key];
    L[key] = m.on;
    ovr.confirm(m.tier === 1 ? DEV.airflow : m.tier === 2 ? DEV.display : ME);
    if (m.tier === 3 && m.on && !was && S.cue.pill) showPill();
    render();
  } else if (m.t === 'MOT') {
    // 관성 방향: ay+(왼쪽 가속) → 오른쪽, ax+(가속) → 아래. 예측값 30% 섞음
    mot.x = m.pay != null ? 0.7 * m.ay + 0.3 * m.pay : m.ay;
    mot.y = m.pax != null ? 0.7 * m.ax + 0.3 * m.pax : m.ax;
    mot.at = performance.now();
  } else if (m.t === 'PROF') {
    L.ctrl = { mssq: m.mssq, sens: m.sens }; renderResult();
  }
}

function onState(state, info) {
  L.kind = info.kind || L.kind;
  if (state === 'connected') L.ever = true;
  if (state === 'lost' || state === 'closed') { L.t1 = L.t2 = L.t3 = false; ovr.clear(); cueAlpha = 0; } // fail-safe: 점 즉시 제거
  L.link = state === 'closed' ? (S.bridge && S.auto && L.kind === 'ws' ? 'connecting' : 'idle') : state;
  render();
}

function connectCar() {
  const b = S.bridge; if (!b) return;
  if (b.kind === 'demo') { sim = new DemoController(); link.connectDemo(sim); }
  else if (b.kind === 'ws') link.connectWS(Q.get('ws') || defaultWsUrl()).catch(() => {});
  else if (b.kind === 'ble') link.connectBLE().then(() => { S.bridge.name = link.stats.name; save(); render(); }).catch((e) => { log('!', e.message); render(); });
}

// 화면 상태 M03: on = 화면 보임, att = 보임 ∧ 포커스 ∧ "다른 앱 위에 표시" 권한
function sendScr() {
  const on = document.visibilityState === 'visible';
  const att = on && document.hasFocus() && S.perms.overlay;
  if (L.scr.on === on && L.scr.att === att) return;
  L.scr = { on, att };
  if (link.state === 'connected') link.send(msg.scr(on, att));
}
['visibilitychange', 'focus', 'blur'].forEach((e) => addEventListener(e, sendScr));

// ───────── 무대 맞춤 (같은 비율로 확대/축소)
const stage = $('stage'), ph = $('ph');
let scale = 1;
function fit() {
  const land = ph.classList.contains('land');
  const bare = innerWidth < 520 && innerHeight > innerWidth * 1.2 || (land && innerHeight < 520 && innerWidth > innerHeight);
  document.body.classList.toggle('bare', bare);
  if (bare) {
    // 실제 폰: 화면을 꽉 채우되 dp 배율은 같게 (세로 384 dp 폭 / 가로 384 dp 높이)
    scale = land ? innerHeight / 384 : innerWidth / 384;
    ph.style.width = land ? innerWidth / scale + 'px' : '';
    ph.style.height = land ? '' : innerHeight / scale + 'px';
  } else {
    ph.style.width = ''; ph.style.height = '';
    const W = land ? 832 : 384, H = land ? 384 : 832;
    scale = Math.min((innerWidth - 60) / W, (innerHeight - 120) / H); // 아래 시연 도구 자리 비움
  }
  stage.style.top = bare ? '50%' : `calc(50% - 26px)`;
  stage.style.transform = `translate(-50%, -50%) scale(${scale})`;
}
addEventListener('resize', fit);

// ───────── 화면 전환
let cur = 'welcome';
function go(name) {
  if (name === 'survey') resetSurvey();
  if (name === 'perms' && S.onboarded) permReturn = 'settings';
  cur = name;
  document.querySelectorAll('.screen').forEach((s) => s.classList.toggle('on', s.id === 's-' + name));
  ph.classList.toggle('vid', name === 'app2' || name === 'land');
  ph.classList.toggle('land', name === 'land');
  $('pull').hidden = !(name === 'app2' || name === 'land');
  closeShade(); fit(); render();
  if (name === 'perms') renderPerms();
}
document.addEventListener('click', (e) => { const t = e.target.closest('[data-go]'); if (t) go(t.dataset.go); });

// ───────── APP-02 권한 (웹: 흐름만 재현)
const STEPS = [
  { k: 'bt', icon: 'ᛒ', title: '근처 기기 (블루투스)', sub: '차와 연결', head: '차와 연결할 수 있게<br>해 주세요', why: '차 안의 MerMaid 장치(MSAgent)와 연결하려면 블루투스 권한이 필요해요. 위치는 쓰지 않아요.', btn: '허용하기' },
  { k: 'notif', icon: '🔔', title: '알림', sub: '연결 상태 · 빠른 조작', head: '알림창에서 바로<br>조절할 수 있게 해 주세요', why: '연결 상태를 알림창에 조용히 보여 드리고, 거기서 바로 모션 큐를 켜고 끌 수 있어요.', btn: '허용하기' },
  { k: 'overlay', icon: '▣', title: '다른 앱 위에 표시', sub: '모션 큐', head: '다른 앱 위에<br>점을 그릴 수 있게 해 주세요', why: 'YouTube·SNS를 볼 때 화면 가장자리에만 점이 나타나요. 화면을 누르는 데는 영향이 없어요.', btn: '설정에서 허용하기' },
  { k: 'battery', icon: '⚡', title: '배터리 제한 없음', sub: '주행 중 연결 유지', head: '주행 중에 연결이<br>끊기지 않게 해 주세요', why: "절전 기능이 앱을 멈추면 주행 중 연결이 끊길 수 있어요. '제한 없음'으로 두면 안정적이에요.", btn: '허용하기' },
];
let skipped = new Set(), permReturn = 'register';
function curStep() { return STEPS.findIndex((s) => !S.perms[s.k] && !skipped.has(s.k)); }
function renderPerms() {
  const i = curStep();
  if (i < 0) { skipped = new Set(); go(permReturn); return; }
  const st = STEPS[i];
  [...$('permStep').children].forEach((el, j) => el.classList.toggle('on', j <= i));
  $('permCount').textContent = `${i + 1} / 4 단계`;
  $('permTitle').innerHTML = st.head; $('permWhy').textContent = st.why; $('permBtn').textContent = st.btn;
  $('permPreview').hidden = st.k !== 'overlay';
  $('permList').innerHTML = STEPS.map((s, j) => {
    const state = S.perms[s.k] ? '<span class="ok">허용됨</span>' : j === i ? '<span class="tag">지금</span>' : skipped.has(s.k) ? '<span class="mut">건너뜀</span>' : '<span class="mut">다음</span>';
    return `<div class="perm${j === i ? ' cur' : ''}"><span class="pi">${s.icon}</span><div class="lbl"><b>${s.title}</b><span>${s.sub}</span></div>${state}</div>`;
  }).join('');
}
$('permBtn').onclick = async () => {
  const st = STEPS[curStep()]; if (!st) return;
  if (st.k === 'notif' && 'Notification' in window && Notification.permission === 'default') { try { await Notification.requestPermission(); } catch {} }
  S.perms[st.k] = true; save(); renderPerms(); sendScr();
};
$('permSkip').onclick = () => { const st = STEPS[curStep()]; if (st) skipped.add(st.k); renderPerms(); };

// ───────── APP-03 차량 등록 (시스템 기기 선택 창 모양)
let regReturn = 'registered', regSel = 0, regDevs = [];
function openSheet() {
  regDevs = [];
  if (location.protocol.startsWith('http') && !Q.has('demo')) regDevs.push({ kind: 'ws', name: 'MSAgent-서버', sub: '근처 · 로컬 백엔드' });
  regDevs.push({ kind: 'demo', name: 'MSAgent-데모', sub: '브라우저 단독 시연' });
  if (navigator.bluetooth) regDevs.push({ kind: 'ble', name: '실제 브리지 (BLE)', sub: 'Web Bluetooth로 검색' });
  regSel = 0; renderSheet();
  $('regDim').hidden = $('regSheet').hidden = false;
}
function renderSheet() {
  $('regDevs').innerHTML = regDevs.map((d, i) => `<button class="dev row${i === regSel ? ' sel' : ''}" data-i="${i}"><div class="lbl"><b>${d.name}</b><span>${d.sub}</span></div>${i === regSel ? '<span style="color:#3e7bfa; font-weight:700">●</span>' : ''}</button>`).join('');
}
function closeSheet() { $('regDim').hidden = $('regSheet').hidden = true; }
$('regDevs').onclick = (e) => { const b = e.target.closest('[data-i]'); if (b) { regSel = +b.dataset.i; renderSheet(); } };
$('regFind').onclick = openSheet;
$('regCancel').onclick = () => { closeSheet(); $('regError').hidden = false; $('regError').textContent = '등록을 취소했어요'; };
$('regDim').onclick = closeSheet;
$('regAllow').onclick = () => {
  const d = regDevs[regSel]; closeSheet(); $('regError').hidden = true;
  S.bridge = { kind: d.kind, name: d.kind === 'ble' ? 'MSAgent' : d.name }; save();
  connectCar();
  $('regName').textContent = S.bridge.name;
  $('regPair').textContent = { ble: '암호화 완료', ws: '로컬 서버', demo: '데모' }[d.kind];
  $('regDev').textContent = ME.slice(0, 8) + '…';
  $('regNext').textContent = regReturn === 'registered' ? '다음: 멀미 프로필' : '완료';
  go('registered');
};
$('regLater').onclick = () => go(regReturn === 'registered' ? 'survey' : 'home');
$('regAuto').onclick = () => { S.auto = !S.auto; save(); render(); };
$('regNext').onclick = () => go(regReturn === 'registered' ? 'survey' : 'home');

// ───────── APP-04 MSSQ-Short
const ITEMS = ['자동차', '버스', '기차', '비행기', '작은 배', '큰 배', '그네', '회전기구', '롤러코스터'];
const NA = -1, EMPTY = -2;
let ans = [[], []], sec = 0;
function decode(s) { const all = Array.from({ length: 18 }, (_, i) => { const c = s[i]; return c === 'n' ? NA : '0123'.includes(c) && c ? +c : EMPTY; }); return [all.slice(0, 9), all.slice(9)]; }
function encode() { return [...ans[0], ...ans[1]].map((v) => (v === NA ? 'n' : v === EMPTY ? '.' : String(v))).join(''); }
function section(a) { const na = a.filter((v) => v === NA).length; return na >= 9 ? 0 : (a.filter((v) => v >= 0).reduce((x, y) => x + y, 0) * 9) / (9 - na); }
const raw = () => Math.min(54, section(ans[0]) + section(ans[1]));
const label = (s) => (s > 1.5 ? '민감' : s > 1.15 ? '평균보다 조금 민감' : s >= 0.85 ? '보통' : '둔감한 편');
function resetSurvey() { ans = decode(S.profile.answers || ''); sec = 0; renderSurvey(); }
function renderSurvey() {
  [...$('svTab').children].forEach((b) => b.classList.toggle('on', +b.dataset.sec === sec));
  $('svRows').innerHTML = ITEMS.map((n, i) => `<div class="q"><span>${n}</span><div class="opts">${[NA, 0, 1, 2, 3].map((v) =>
    `<button class="${v === NA ? 'na ' : ''}${ans[sec][i] === v ? 'on' : ''}" data-i="${i}" data-v="${v}" aria-label="${n} ${v === NA ? '타 본 적 없음' : v}">${v === NA ? '—' : v}</button>`).join('')}</div></div>`).join('');
  $('svCount').textContent = `응답 ${[...ans[0], ...ans[1]].filter((v) => v !== EMPTY).length} / 18`;
  $('svScore').textContent = `지금 점수 ${raw().toFixed(1)}`;
  $('svNext').textContent = sec === 0 ? '다음' : '결과 보기';
  $('svSkip').hidden = S.onboarded; $('svBack').style.visibility = S.onboarded ? 'visible' : 'hidden';
}
$('svRows').onclick = (e) => { const b = e.target.closest('[data-v]'); if (b) { ans[sec][+b.dataset.i] = +b.dataset.v; renderSurvey(); } };
$('svTab').onclick = (e) => { const b = e.target.closest('[data-sec]'); if (b) { sec = +b.dataset.sec; renderSurvey(); } };
$('svNext').onclick = () => { if (sec === 0) { sec = 1; renderSurvey(); } else { renderResult(); go('result'); } };
$('svBack').onclick = () => go('home');
$('svSkip').onclick = () => { setProfile(MSSQ.def.mssq, MSSQ.def.sens, ''); finishOnboarding(); };
function renderResult() {
  const m = raw(), s = sensFromMssq(m);
  $('rsArc').setAttribute('stroke-dasharray', `${(m / 54) * 389.6} 390`);
  $('rsScore').textContent = m.toFixed(1); $('rsSens').textContent = `민감도 ${s.toFixed(2)}`; $('rsLabel').textContent = label(s);
  $('rsA').textContent = section(ans[0]).toFixed(1); $('rsB').textContent = section(ans[1]).toFixed(1);
  $('rsCtrl').textContent = L.ctrl ? `민감도 ${(+L.ctrl.sens).toFixed(2)}` : '—';
  $('rsSave').textContent = S.onboarded ? '저장' : '저장하고 시작하기';
}
function setProfile(m, s, a) {
  S.profile = { has: true, mssq: m, sens: s, answers: a }; save();
  if (link.state === 'connected') link.send(msg.prof(m, s));
}
$('rsSave').onclick = () => { const m = raw(); setProfile(m, sensFromMssq(m), encode()); if (S.onboarded) go('home'); else finishOnboarding(); };
$('rsRedo').onclick = () => { sec = 0; renderSurvey(); go('survey'); };
function finishOnboarding() { S.onboarded = true; save(); go('home'); }

// ───────── APP-05 홈
function chip(el) {
  const [cls, text] = !S.bridge ? ['none', '미등록'] : L.link === 'connected' ? ['', '연결됨'] : L.link === 'lost' ? ['bad', '신호 없음'] : L.link === 'connecting' ? ['wait', '연결 중'] : ['wait', '차 찾는 중'];
  el.className = 'chipm ' + cls; el.lastElementChild.textContent = text;
}
function renderHome() {
  chip($('homeChip'));
  const c = $('homeCard'), name = S.bridge?.name || '차량';
  if (!S.bridge) c.innerHTML = `<div class="card" style="display:grid; gap:10px"><div class="h2">차를 등록해 주세요</div><div class="mut">한 번만 등록하면 다음부터 차에 타면 자동으로 연결돼요.</div><button class="btnm" id="hcReg">차 등록하기</button></div>`;
  else if (L.link === 'connected') c.innerHTML = `<div class="card" style="background:#1e1b1d; color:#fff; border:0; display:grid; gap:8px"><span style="font-size:12.5px; color:#cbbfba">${name}</span><div class="h2" style="color:#fff">차와 연결됐어요.<br>이제 다른 앱을 쓰셔도 돼요</div><span style="font-size:13px; color:#cbbfba">필요할 때 화면 가장자리에 점이 나타나요. 앱을 닫아도 계속 동작해요.</span><button class="btnm sm" style="justify-self:start; background:#fff; color:#1e1b1d; margin-top:4px" data-go="app2">홈 화면으로</button></div>`;
  else if (L.link === 'lost') c.innerHTML = `<div class="card" style="background:#fdeeee; border-color:#f0b8b8; display:grid; gap:6px"><b style="color:#c62828; font-size:16px">차와 연결이 끊겼어요</b><span style="font-size:13px; color:#7a3b3b">안전을 위해 화면의 점을 껐어요. 신호가 돌아오면 바로 이어서 챙겨 드려요.</span></div>`;
  else c.innerHTML = `<div class="card" style="display:grid; gap:8px"><div class="h2">${L.link === 'connecting' ? '차와 연결하는 중' : '차를 찾는 중'}</div><div class="mut">차에 타면 자동으로 연결돼요. 앱을 닫아도 괜찮아요.</div><button class="btnm sm ghost" style="justify-self:start" id="hcConn">지금 연결</button></div>`;
  $('hcReg') && ($('hcReg').onclick = () => { regReturn = 'home'; go('register'); });
  $('hcConn') && ($('hcConn').onclick = connectCar);

  const live = L.link === 'connected';
  $('tiers').classList.toggle('off', !live);
  [[1, DEV.airflow, L.t1, '차 안 공용'], [2, DEV.display, L.t2, '차 안 공용'], [3, ME, L.t3, '이 폰에만']].forEach(([n, dev, actual, sub]) => {
    const v = ovr.view(dev, actual), sw = $('sw' + n);
    sw.classList.toggle('on', live && v.on); sw.classList.toggle('pend', v.pending); sw.disabled = !live;
    $('tv' + n).textContent = `${sub} · ${!live ? '알 수 없음' : v.pending ? '요청 중' : v.on ? '켜짐' : '꺼짐'}`;
  });
  $('homeProf').textContent = S.profile.has ? `MSSQ ${S.profile.mssq.toFixed(1)} · 민감도 ${S.profile.sens.toFixed(2)}` : '아직 설문 전 (기본값 사용)';
}
const toggle = (dev, actual) => ovr.request(dev, !ovr.view(dev, actual).on);
$('sw1').onclick = () => toggle(DEV.airflow, L.t1);
$('sw2').onclick = () => toggle(DEV.display, L.t2);
$('sw3').onclick = () => toggle(ME, L.t3);

// ───────── APP-06 설정
function renderSettings() {
  [...$('stGain').children].forEach((b) => b.classList.toggle('on', +b.dataset.v === S.cue.gain));
  $('stGainV').textContent = { 16: '약', 24: '중', 32: '강' }[S.cue.gain];
  $('stAlpha').value = S.cue.alpha; $('stAlpha').style.setProperty('--p', ((S.cue.alpha - 0.3) / 0.5) * 100 + '%');
  $('stAlphaV').textContent = S.cue.alpha.toFixed(2) + (S.cue.alpha >= 0.79 ? ' (최대)' : '');
  $('stWhite').classList.toggle('on', !S.cue.amber); $('stAmber').classList.toggle('on', S.cue.amber);
  $('stPill').classList.toggle('on', S.cue.pill); $('stAuto').classList.toggle('on', S.auto); $('regAuto').classList.toggle('on', S.auto);
  $('stCar').textContent = S.bridge?.name || '등록된 차량 없음';
  $('stUnreg').textContent = S.bridge ? '해제' : '등록';
  const miss = Object.values(S.perms).filter((v) => !v).length;
  $('stPerm').textContent = miss ? `${miss}개 필요` : '모두 허용'; $('stPerm').className = miss ? 'tag' : 'ok';
  $('stVer').textContent = `0.2.0 웹 · ${ME.slice(0, 4)}…`;
}
$('stGain').onclick = (e) => { const b = e.target.closest('[data-v]'); if (b) { S.cue.gain = +b.dataset.v; save(); render(); } };
$('stAlpha').oninput = (e) => { S.cue.alpha = +e.target.value; save(); render(); }; // 0.8 상한 = Android 터치 통과 조건
$('stWhite').onclick = () => { S.cue.amber = false; save(); render(); };
$('stAmber').onclick = () => { S.cue.amber = true; save(); render(); };
$('stPill').onclick = () => { S.cue.pill = !S.cue.pill; save(); render(); };
$('stAuto').onclick = () => { S.auto = !S.auto; save(); render(); };
$('stUnreg').onclick = () => {
  if (!S.bridge) { regReturn = 'home'; go('register'); return; }
  if (!confirm(`${S.bridge.name} 등록을 해제할까요?\n연결을 끊고 자동 연결을 멈춰요.`)) return;
  link.close(); S.bridge = null; save(); L.link = 'idle'; render();
};

// ───────── APP-07 RF 진단
function renderDiag() {
  const st = link.stats, live = L.link === 'connected' || L.link === 'lost';
  const age = live && isFinite(link.age) ? link.age / 1000 : null;
  const frac = Math.min(1, (age ?? 5) / 5);
  $('dgArc').setAttribute('stroke-dasharray', `${314 * (1 - frac)} 314`);
  $('dgArc').setAttribute('stroke', L.link !== 'connected' ? '#c62828' : frac > 0.5 ? '#c77700' : '#1f9d68');
  $('dgAge').textContent = age != null ? age.toFixed(1) + 's' : '—';
  const label = { connected: '연결됨', lost: '신호 없음', connecting: '연결 중', idle: '대기' }[L.link];
  $('dgState').textContent = label; $('dgState').style.color = L.link === 'connected' ? '#1f9d68' : L.link === 'lost' ? '#c62828' : '';
  $('dgName').textContent = S.bridge?.name || '—';
  $('dgKind').textContent = { ble: 'BLE', ws: '서버 (WebSocket)', demo: '데모' }[L.kind] || '—';
  $('dgMtu').textContent = L.kind === 'ble' ? '자동' : '—';
  $('dgHb').textContent = `${st.hbTx} / ${st.hbRx ?? '-'}`;
  $('dgScr').textContent = L.scr.on == null ? '—' : `on · ${L.scr.att ? 'att' : 'att 없음'}`;
}
$('dgRe').onclick = connectCar;
$('dgClear').onclick = () => { $('dgLog').innerHTML = ''; };
$('dgDemo').onclick = () => { S.bridge = { kind: 'demo', name: 'MSAgent-데모' }; save(); connectCar(); render(); };
$('dgDrop').onclick = () => sim?.drop(7);

// ───────── SYS-01 알림창 (상태별 문구: 화면정의서 9쪽)
function renderShade() {
  const live = L.link === 'connected', cueOn = ovr.view(ME, L.t3).on, name = S.bridge?.name || '차량';
  const tiers = `송풍 ${L.t1 ? '켜짐' : '꺼짐'} · 앰비언트 ${L.t2 ? '켜짐' : '꺼짐'}`;
  let t;
  if (!S.bridge || L.link === 'idle') t = null;
  else if (live && !S.perms.overlay) t = ['점을 표시할 수 없어요', '"다른 앱 위에 표시"를 허용해 주세요', '허용하기'];
  else if (live && L.t3) t = ['모션 큐가 켜져 있어요', `${tiers} · ${name}`, '모션 큐 끄기'];
  else if (live && (L.t1 || L.t2)) t = ['차량과 연결됨', `${tiers} · ${name}`, '모션 큐 켜기'];
  else if (live) t = ['차량과 연결됨', `편안한 주행이에요 · ${name}`, '모션 큐 켜기'];
  else if (L.link === 'lost') t = ['연결이 끊겼어요', '안전을 위해 점을 껐어요 · 다시 연결 중', null];
  else t = ['차와 연결하는 중', name, null];
  $('noti').hidden = !t;
  if (t) {
    $('notiApp').textContent = live ? 'MerMaid · 차량과 연결됨' : 'MerMaid';
    $('notiTitle').textContent = t[0]; $('notiTitle').style.color = L.link === 'lost' ? '#c62828' : '';
    $('notiText').textContent = t[1];
    $('notiCue').hidden = !t[2]; if (t[2]) $('notiCue').textContent = t[2];
  }
  const q = $('qsCue');
  q.className = 'qt' + (!live ? ' na' : cueOn ? ' mm' : '');
  $('qsCueSub').textContent = !live ? '연결 안 됨' : cueOn ? '켜짐' : '꺼짐';
  const d = new Date(); $('shDate').textContent = `${d.getMonth() + 1}월 ${d.getDate()}일 (${'일월화수목금토'[d.getDay()]})`;
}
function openShade() { renderShade(); $('shade').classList.add('open'); ph.classList.add('shading'); }
function closeShade() { $('shade').classList.remove('open'); ph.classList.remove('shading'); }
$('pull').onclick = openShade;
$('shadeClose').onclick = closeShade;
$('notiOpen').onclick = () => go('home');
$('notiCue').onclick = () => { if (!S.perms.overlay) { S.perms.overlay = true; save(); sendScr(); render(); return; } toggle(ME, L.t3); };
$('qsCue').onclick = () => { if (L.link === 'connected') toggle(ME, L.t3); else go('home'); };
$('toLand').onclick = () => go('land');
$('toPort').onclick = () => go('app2');
$('tApp2').onclick = () => go(cur === 'app2' || cur === 'land' ? 'home' : 'app2');
$('tShade').onclick = () => ($('shade').classList.contains('open') ? closeShade() : openShade());
$('tReset').onclick = () => { if (confirm('저장된 설정을 지우고 첫 실행부터 다시 볼까요?')) { try { localStorage.removeItem(KEY); } catch {} link.close(); location.reload(); } };

// ───────── 모션 큐 그리기 (Android MotionDotsView와 같은 배치·이동)
// 점 지름 10, 가장자리 18, 열 간격 16 dp. 세로 2열 × 8행 + 상하 5 / 가로 2열 × 5행 + 상하 7. ±3 m/s²에서 최대 gain dp
const cue = $('cue'), cg = cue.getContext('2d');
let cueAlpha = 0, cx = 0, cy = 0, last = performance.now(), pillTimer = 0;
function showPill() { const p = $('pill'); p.style.opacity = 1; clearTimeout(pillTimer); pillTimer = setTimeout(() => (p.style.opacity = 0), 1500); }

function drawDots(g, W, H, ins, dx, dy, a, r, edge, gap, rows, across, color) {
  const left = ins.l + edge, right = W - ins.r - edge, top = ins.t + edge, bottom = H - ins.b - edge;
  g.fillStyle = color.replace('A', a); g.strokeStyle = `rgba(0,0,0,${0.55 * a})`; g.lineWidth = 1;
  const dot = (x, y) => {
    const px = Math.max(r + 2, Math.min(W - r - 2, x + dx)), py = Math.max(r + 2, Math.min(H - r - 2, y + dy));
    g.beginPath(); g.arc(px, py, r, 0, Math.PI * 2); g.fill(); g.stroke();
  };
  const y0 = top + (rows > 4 ? 16 : 0), y1 = bottom - (rows > 4 ? 16 : 0);
  for (let col = 0; col < 2; col++) for (let i = 0; i < rows; i++) {
    const t = col === 0 ? i / (rows - 1) : (i + 0.5) / rows;
    const y = y0 + (y1 - y0) * t; if (y > y1 + 1) continue;
    dot(left + col * gap, y); dot(right - col * gap, y);
  }
  for (let i = 1; i <= across; i++) { const x = left + ((right - left) * i) / (across + 1); dot(x, top); dot(x, bottom); }
}

function frame(now) {
  const dt = Math.min(0.1, (now - last) / 1000); last = now;
  if (now - mot.at > MOT_STALE_MS) { mot.x = 0; mot.y = 0; }
  const k = 1 - Math.exp(-dt * 6); cx += (mot.x - cx) * k; cy += (mot.y - cy) * k;
  const want = L.t3 && L.link === 'connected' && S.perms.overlay;
  cueAlpha += ((want ? 1 : 0) - cueAlpha) * (1 - Math.exp(-dt * 10));
  const W = ph.offsetWidth, H = ph.offsetHeight, d = Math.min(devicePixelRatio || 1, 2) * scale;
  if (cue.width !== Math.round(W * d) || cue.height !== Math.round(H * d)) { cue.width = Math.round(W * d); cue.height = Math.round(H * d); }
  cg.setTransform(d, 0, 0, d, 0, 0); cg.clearRect(0, 0, W, H);
  if (cueAlpha > 0.01) {
    const land = W > H, bare = document.body.classList.contains('bare');
    const ins = land ? { l: bare ? 0 : 34, t: 0, r: 0, b: 0 } : { l: 0, t: bare ? 0 : 34, r: 0, b: bare ? 0 : 24 };
    const max = S.cue.gain, dx = (Math.max(-3, Math.min(3, cx)) / 3) * max, dy = (Math.max(-3, Math.min(3, cy)) / 3) * max;
    // 창 불투명도 ≤ 0.8 (Android 터치 통과 조건)과 같게 전체에 곱함
    drawDots(cg, W, H, ins, dx, dy, (cueAlpha * Math.min(0.8, S.cue.alpha)).toFixed(3), 5, 18, 16, land ? 5 : 8, land ? 7 : 5,
      S.cue.amber ? 'rgba(255,181,71,A)' : 'rgba(255,255,255,A)');
  }
  // 미리보기 (APP-02, APP-06): 사인파로 흔들리는 작은 점
  document.querySelectorAll('.preview canvas').forEach((c) => {
    if (!c.offsetParent) return;
    const w = c.clientWidth, h = c.clientHeight, dd = Math.min(devicePixelRatio || 1, 2) * scale;
    if (c.width !== Math.round(w * dd)) { c.width = Math.round(w * dd); c.height = Math.round(h * dd); }
    const g = c.getContext('2d'); g.setTransform(dd, 0, 0, dd, 0, 0); g.clearRect(0, 0, w, h);
    const off = Math.sin((now / 4000) * Math.PI * 2) * S.cue.gain * 0.45, rows = +c.dataset.mini;
    drawDots(g, w, h, { l: 0, t: 0, r: 0, b: 0 }, off, 0, Math.min(0.8, S.cue.alpha), rows > 4 ? 3.5 : 3, 10, 10, rows, 0,
      S.cue.amber ? 'rgba(255,181,71,A)' : 'rgba(255,255,255,A)');
  });
  requestAnimationFrame(frame);
}

// ───────── 렌더
function render() {
  if (cur === 'home') renderHome();
  if (cur === 'settings') renderSettings();
  if (cur === 'diag') renderDiag();
  if ($('shade').classList.contains('open')) renderShade();
  $('sbBt').textContent = L.link === 'connected' ? 'ᛒ' : '';
}
setInterval(() => { if (cur === 'diag') renderDiag(); }, 200);
function clock() { const d = new Date(); $('clock').textContent = `${d.getHours()}:${String(d.getMinutes()).padStart(2, '0')}`; }
setInterval(clock, 10000); clock();

// ───────── 시작
if (Q.has('reset')) { try { localStorage.removeItem(KEY); } catch {} S = structuredClone(DEF); }
// 시연·검수용 바로가기: ?screen=home|app2|land|settings|diag|survey|result|perms|register|registered|welcome (&demo로 데모 차량, &shade로 알림창)
const jump = Q.get('screen');
if (jump) {
  if (!['welcome', 'perms', 'register'].includes(jump)) S.onboarded = true;
  if (Q.has('demo') && !S.bridge) S.bridge = { kind: 'demo', name: 'MSAgent-데모' };
  if (Q.has('demo')) S.perms = { bt: true, notif: true, overlay: true, battery: true };
  if (jump === 'registered') { $('regName').textContent = S.bridge?.name || 'MSAgent-1A2B'; $('regDev').textContent = ME.slice(0, 8) + '…'; }
  if (jump === 'result') { ans = decode(S.profile.answers || '21003n100'); renderResult(); }
}
go(jump || (S.onboarded ? 'home' : 'welcome'));
if (Q.has('shade')) setTimeout(openShade, 1500);
if (S.onboarded && S.bridge && S.bridge.kind !== 'ble' && S.auto) connectCar(); // BLE는 사용자 탭이 필요 → 홈의 [지금 연결]
requestAnimationFrame(frame);
