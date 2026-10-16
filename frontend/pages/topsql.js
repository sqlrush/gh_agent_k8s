// Top SQL:取数与建模。数据:/reports/topsql/<key>/index.json 里按文件名后缀 .<by>.json 找每个维度最近一份,
// 再取那份;逐条按 sql_id 取 /reports/sqltune/<key>/<sql_id>.json(有则带调优结论)。
// 报告只有 Top N,没有全量合计 —— 页面上写「上榜合计」,不写「占总耗时」。

import { fetchIndex, fetchReport, fetchSqltune } from '../lib/data.js';
import { fmtMs, brief } from '../lib/fmt.js';
import { instName } from '../lib/ui.js';
import { filterIndex } from '../lib/range.js';

export const BY = [['time', '总耗时', 'total_sec'], ['avg', '平均耗时', 'avg_ms'], ['calls', '调用次数', 'calls'], ['reads', '逻辑读', 'total_sec'], ['rows', '返回行', 'rows']];
export const byLabel = (by) => (BY.find(([b]) => b === by) || [, by])[1];
const BY_STORE = 'dash.topsql.by';
export function readBy() { try { return localStorage.getItem(BY_STORE) || ''; } catch (_) { return ''; } }
export function writeBy(b) { try { localStorage.setItem(BY_STORE, b); } catch (_) { /* 忽略 */ } }

/** index 条目 → 每个 by 最近一份的文件名。文件名形如 20260922T140300Z.avg.json。 */
export function latestPerBy(index) {
  const out = {};
  for (const e of index) {
    const m = /^\d{8}T\d{6}Z\.([a-z]+)\.json$/.exec(e.file || '');
    if (!m) continue;
    if (!out[m[1]] || (e.at || '') > (out[m[1]].at || '')) out[m[1]] = e;
  }
  return out;
}

// 调优入口全页只有一种写法(user 2026-09-23:两种说法并存让人以为是两件事)
export const tunePrompt = (r) => `请调优 sql_id 为 ${r.sql_id} 的语句(调用 ${r.calls} 次,平均 ${fmtMs(r.avg_ms)}):${brief(r.query, 200)}`;
// 取数脚本在库端把 SQL 截到 80 字(客户中间件白名单里注册的脚本,改它要客户重新注册):满 80 字就标出来
export const QUERY_CAP = 80;
export const fullQuery = (q) => (String(q ?? '').length >= QUERY_CAP ? `${q} …(取数时截到 ${QUERY_CAP} 字)` : String(q ?? ''));
// 调优技能按策略跳过的系统 SQL:存档里 skipped = "system"
export const isSkipped = (t) => !!(t && t.skipped);
export const READS_NOTE = '取数脚本(客户中间件白名单里注册的那条)不返回逻辑读数值,占比仍按耗时;要看数值需客户重新注册脚本';

/** 取某个排序维度最近一份榜单并建模。没取过的维度返回 {ok:true, missing:true},页面给「去会话里取」。 */
export async function load(key, byPref, range) {
  if (!key) return { ok: false, reason: 'none' };
  const index = await fetchIndex('topsql', key);
  if (!index.ok) return { ok: false, reason: index.status === 404 ? 'none' : 'error', error: index.error };
  const all = Array.isArray(index.data) ? index.data : [];
  const per = latestPerBy(filterIndex('topsql', all, range));      // 时间段内各维度最近一份
  const avail = BY.filter(([b]) => per[b]);
  if (!avail.length) return { ok: false, reason: range && all.length ? 'range' : 'none' };
  const by = BY.some(([b]) => b === byPref) ? byPref : avail[0][0];
  if (!per[by]) return { ok: true, key, per, avail, by, missing: true };
  const rep = await fetchReport('topsql', key, per[by].file);
  if (!rep.ok) return { ok: false, reason: 'error', error: rep.error };
  const rows = rep.data.rows || [];
  const tunes = await Promise.all(rows.map((r) => fetchSqltune(key, r.sql_id).then((t) => (t.ok ? t.data : null))));
  const tune = Object.fromEntries(rows.map((r, i) => [r.sql_id, tunes[i]]));
  const sum = rows.reduce((a, r) => a + (+r.total_sec || 0), 0) || 1;
  return { ok: true, key, per, avail, by, entry: per[by], rep: rep.data, rows, tune, sum, share: (r) => ((+r.total_sec || 0) / sum) * 100 };
}

/** 普通风格的 Top SQL 页。 */
export async function render(root, ctx) {
  const view = await import('../views/normal/topsql.js');
  const m = await load(ctx.key, readBy(), ctx.range);
  return view.render(root, m.ok && !m.missing ? { ...m, name: instName(ctx, m.rep.conn) } : m, ctx);
}
