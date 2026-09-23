// 数字 / 时间 / 级别的格式化。纯函数,无 DOM。

export const LV = ['ok', 'notice', 'warn', 'crit'];
export const LVN = ['正常', '关注', '告警', '严重'];

/** severity(0–3)→ css 级别名;非法值当 ok。 */
export const lv = (s) => LV[Number(s)] || 'ok';
export const lvn = (s) => LVN[Number(s)] || '正常';

export function fmtInt(n) {
  n = Number(n);
  if (!Number.isFinite(n)) return '—';
  if (n >= 1e8) return (n / 1e8).toFixed(2) + ' 亿';
  if (n >= 1e4) return (n / 1e4).toFixed(1) + ' 万';
  return n.toLocaleString('en-US');
}

export function fmtSec(s) {
  s = Number(s);
  if (!Number.isFinite(s)) return '—';
  if (s >= 3600) return (s / 3600).toFixed(1) + ' h';
  if (s >= 60) return (s / 60).toFixed(1) + ' min';
  return s.toFixed(s < 10 ? 1 : 0) + ' s';
}

export function fmtMs(ms) {
  ms = Number(ms);
  if (!Number.isFinite(ms)) return '—';
  return ms >= 1000 ? (ms / 1000).toFixed(1) + ' s' : ms.toFixed(1) + ' ms';
}

/** 存档时间戳 20260922T060312Z 或 ISO 串 → "09-22 14:03"(本地时区)。 */
export function stamp(s) {
  if (!s) return '—';
  const m = /^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z$/.exec(s);
  const d = m ? new Date(Date.UTC(+m[1], m[2] - 1, +m[3], +m[4], +m[5], +m[6])) : new Date(s);
  if (Number.isNaN(d.getTime())) return String(s);
  const p = (x) => String(x).padStart(2, '0');
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

export const mmdd = (s) => stamp(s).slice(0, 5);

/** 模板里插值用:把用户数据里的 < > & 转掉,报告里的 SQL 原文经常带 < 。 */
export function esc(v) {
  return String(v ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

/** SQL 摘要:压掉空白,截到 n 个字符。 */
export function brief(sql, n = 80) {
  const s = String(sql ?? '').replace(/\s+/g, ' ').trim();
  return s.length > n ? s.slice(0, n) + '…' : s;
}
