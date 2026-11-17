// MerMaid 프로토콜 v1 — GitHub sunheeday/MerMaid `protocol/` (feat/rf-p5-p6, 2026-11-16) 값을 그대로 옮김
//   uuids.json · constants.json · messages.schema.json · PROTOCOL.md · docs/RF_MESSAGES.md
// 바뀌면 protocol/ 폴더를 먼저 고치고 이 파일을 맞춘다.

export const BLE = {
  namePrefix: 'MSAgent-',
  service: 'cd820001-2f55-4bae-8e0f-8474006c441e',
  rx: 'cd820002-2f55-4bae-8e0f-8474006c441e', // 기기 → 컨트롤러, Write Without Response, 암호화
  tx: 'cd820003-2f55-4bae-8e0f-8474006c441e', // 컨트롤러 → 기기, Notify, 암호화
};

export const VERSION = 1;
export const MAX_MSG = 512;          // 한 줄 최대 바이트
export const HB_PERIOD_MS = 1000;    // M07 양방향 1초
export const LINK_TIMEOUT_MS = 5000; // 5초 무수신 → 끊김
export const MOT_STALE_MS = 500;     // MOT(10 Hz)가 0.5초 안 오면 0으로

// 고정 기기 ID (PROTOCOL.md §4)
export const DEV = { airflow: 'airflow-01', display: 'display-01', hud: 'hud-01' };
export const TIER = { airflow: 1, display: 2, phone: 3 };
export const TIER_LABEL = { 1: '송풍', 2: '앰비언트', 3: '모션 큐' };

// MSSQ → 민감도 (constants.json mssq)
export const MSSQ = { mean: 12.9, sd: 9.9, coef: 0.3, min: 0.5, max: 2.0, def: { mssq: 12.9, sens: 1.0 } };
export const sensFromMssq = (m) =>
  Math.min(MSSQ.max, Math.max(MSSQ.min, Math.exp(MSSQ.coef * (m - MSSQ.mean) / MSSQ.sd)));

// 메시지 생성 (M00~M07)
export const msg = {
  hello: (role, dev) => ({ t: 'HELLO', role, dev, v: VERSION }),
  scr: (on, att) => ({ t: 'SCR', on: !!on, att: !!(on && att) }), // 화면 OFF면 att = false
  ovr: (dev, on) => ({ t: 'OVR', dev, on: !!on }),
  prof: (mssq, sens) => ({ t: 'PROF', mssq: +(+mssq).toFixed(2), sens: +(+sens).toFixed(3) }),
  hb: (seq) => ({ t: 'HB', seq }),
};

// 받은 메시지 검사 (messages.schema.json 중 컨트롤러 → 기기 방향만). 모르는 t는 null (명세: 무시)
export function parseIncoming(m) {
  if (!m || typeof m !== 'object') return null;
  switch (m.t) {
    case 'INT':
      return [1, 2, 3].includes(m.tier) && typeof m.on === 'boolean' ? m : null;
    case 'MOT': {
      if (!Number.isFinite(m.ax) || !Number.isFinite(m.ay)) return null;
      const hasP = Number.isFinite(m.pax) && Number.isFinite(m.pay);
      return { t: 'MOT', ax: m.ax, ay: m.ay, pax: hasP ? m.pax : null, pay: hasP ? m.pay : null };
    }
    case 'PROF':
      return Number.isFinite(m.mssq) && Number.isFinite(m.sens) ? m : null;
    case 'HB':
      return Number.isInteger(m.seq) ? m : null;
    case 'DBG':
      return m; // 로컬 백엔드 전용 확장 (실제 Pi는 보내지 않음)
    default:
      return null;
  }
}

// 영구 기기 ID. 폰은 앱이 처음 만든 UUID (63자 이하, 고정 ID와 겹치면 안 됨)
export function persistentId(key) {
  const make = () => (crypto.randomUUID ? crypto.randomUUID() : 'p' + Math.random().toString(16).slice(2) + Date.now().toString(16));
  try {
    let id = localStorage.getItem(key);
    if (!id || Object.values(DEV).includes(id) || id.length > 63) { id = make(); localStorage.setItem(key, id); }
    return id;
  } catch { return make(); }
}
