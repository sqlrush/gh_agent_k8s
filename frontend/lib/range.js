// 时间段筛选:只看某段时间内跑过的报告(大盘方案 B1)。
//
// 记在本标签页(sessionStorage),切页面、切风格都保留;地址里的 ?from=&to= 首次进入时带进来
// (智能体可以直接给一个带时间段的链接)。时间一律按浏览器本地时间理解。
//
// 口径:健康检查 / Top SQL 按存档时间;**WDR 按快照窗口**(与所选时间段有重叠就算)——WDR 的存档时间
// 是「什么时候分析的」,不是「分析的哪一段」。窗口来自索引里的 window(common/reports.py 抄进去的),
// 老存档没有它时退回存档时间。快照时间是数据库所在时区,浏览器与数据库不在同一时区时会有偏差。

import { fetchIndex, fetchReport } from './data.js';

const STORE = 'dash.range';
const LOCAL = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?$/;
const STAMP = /^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z$/;

/** 'YYYY-MM-DDTHH:MM[:SS]'(或中间是空格)→ 本地时间的 Date;不认识 → null。 */
export function parseLocal(s) {
  const m = LOCAL.exec(String(s || '').trim());
  return m ? new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +(m[6] || 0)) : null;
}

/** 存档时间戳 YYYYMMDDTHHMMSSZ(UTC)→ Date。 */
function parseStamp(s) {
  const m = STAMP.exec(String(s || ''));
  return m ? new Date(Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6])) : null;
}

function bounds(r) {
  const a = r.from ? parseLocal(r.from) : null;
  const b = r.to ? parseLocal(r.to) : null;
  // 只到分钟的结束时间算到这一分钟的最后一刻:「到 15:30」包含 15:30:40 存的那份
  const end = b && !/:\d{2}:\d{2}$/.test(r.to) ? new Date(b.getTime() + 59999) : b;
  return [a, end];
}

/** 至少有一头、两头都认得、起不晚于止。 */
export function valid(r) {
  if (!r || (!r.from && !r.to)) return false;
  const [a, b] = bounds(r);
  if ((r.from && !a) || (r.to && !b)) return false;
  return !(a && b && a > b);
}

export function readRange() {
  try {
    const r = JSON.parse(sessionStorage.getItem(STORE) || 'null');
    return valid(r) ? r : null;
  } catch (_) { return null; }
}

export function saveRange(r) {
  try {
    if (valid(r)) sessionStorage.setItem(STORE, JSON.stringify({ from: r.from || '', to: r.to || '' }));
    else sessionStorage.removeItem(STORE);
  } catch (_) { /* 隐私模式等,忽略 */ }
}

function span(skill, e) {
  if (skill === 'wdr' && e.window) {
    const a = parseLocal(e.window.begin_ts), b = parseLocal(e.window.end_ts);
    if (a && b) return [a, b];
  }
  const t = parseStamp(e.at);
  return t ? [t, t] : null;
}

export function inRange(skill, e, r) {
  if (!r) return true;
  const s = span(skill, e);
  if (!s) return false;
  const [a, b] = bounds(r);
  return (!a || s[1] >= a) && (!b || s[0] <= b);
}

/** 没选时间段时原样返回。 */
export const filterIndex = (skill, idx, r) => (r ? idx.filter((e) => inRange(skill, e, r)) : idx);

const short = (s) => String(s || '').replace('T', ' ').slice(5, 16);   // MM-DD HH:MM

export function label(r) {
  if (!r) return '全部';
  if (r.from && r.to) return `${short(r.from)} – ${short(r.to)}`;
  return r.from ? `${short(r.from)} 起` : `至 ${short(r.to)}`;
}

/** 首次进入时地址里的 ?from=&to=。不合法就不用(也不清掉已选的)。 */
export function fromQuery(q) {
  const r = { from: q.get('from') || '', to: q.get('to') || '' };
  return valid(r) ? r : null;
}

/** 时间段内的那一份:file 在时间段内就用它,否则取时间段内最新的一份。时间段内一份都没有 → reason 'range'。 */
export async function loadInRange(skill, key, file, r) {
  const index = await fetchIndex(skill, key);
  if (!index.ok) return { ok: false, reason: index.status === 404 ? 'none' : 'error', error: index.error };
  const idx = filterIndex(skill, Array.isArray(index.data) ? index.data : [], r);
  if (!idx.length) return { ok: false, reason: 'range' };
  const f = file && idx.some((e) => e.file === file) ? file : idx[idx.length - 1].file;
  const rep = await fetchReport(skill, key, f);
  if (!rep.ok) return { ok: false, reason: 'error', error: rep.error };
  return { ok: true, rep, idx, file: f };
}
