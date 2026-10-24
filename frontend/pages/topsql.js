// Top SQL:取数与建模。数据:/reports/topsql/<key>/index.json 里按文件名后缀 .<by>.json 找每个维度最近一份,
// 再取那份;逐条按 sql_id 取 /reports/sqltune/<key>/<sql_id>.json(有则带调优结论)。
// 时段榜 / 增量榜(.<by>.span.json / .<by>.delta.json)不进页签 —— 页签是累计榜;它们只经直达链接
// 的 ?run=<文件> 打开(技能存档后打印的那一行),切页签即回到累计榜。
// 报告只有 Top N,没有全量合计 —— 页面上写「上榜合计」,不写「占总耗时」。

import { fetchIndex, fetchReport, fetchSqltune } from '../lib/data.js';
import { fmtMs, brief, stamp } from '../lib/fmt.js';
import { instName } from '../lib/ui.js';
import { filterIndex, windowLabel, windowPromptLabel } from '../lib/range.js';
import { atText, prevNote } from '../lib/prompts.js';

export const BY = [['time', '总耗时', 'total_sec'], ['avg', '平均耗时', 'avg_ms'], ['calls', '调用次数', 'calls'], ['reads', '逻辑读', 'total_sec'], ['rows', '返回行', 'rows']];
export const byLabel = (by) => (BY.find(([b]) => b === by) || [, by])[1];
const BY_STORE = 'dash.topsql.by';
export function readBy() { try { return localStorage.getItem(BY_STORE) || ''; } catch (_) { return ''; } }
export function writeBy(b) { try { localStorage.setItem(BY_STORE, b); } catch (_) { /* 忽略 */ } }

// 20260922T140300Z.avg.json(累计榜)/ 20261018T124105Z.time.span.json(时段榜)/ ….time.delta.json(增量榜)
const FILE = /^\d{8}T\d{6}Z\.([a-z]+)(?:\.(span|delta))?\.json$/;

/** index 条目 → 每个 by 最近一份**累计榜**的文件名。 */
export function latestPerBy(index) {
  const out = {};
  for (const e of index) {
    const m = FILE.exec(e.file || '');
    if (!m || m[2]) continue;
    if (!out[m[1]] || (e.at || '') > (out[m[1]].at || '')) out[m[1]] = e;
  }
  return out;
}

// 深挖首句里这份榜单的时间与称呼(口径见 lib/prompts.js):时段 / 增量榜 = 评估窗口(带秒),累计榜 = 取数时间。
export const tWhen = (m) => (!m ? '' : m.span ? m.winq || m.win || '' : atText(m.entry && m.entry.at));
/** 「10-17 10:46:29 – 11:06:30 的 Top SQL 时段榜」/「10-22 14:03 取数的 Top SQL 累计榜」;取不到时间只写「Top SQL」。 */
export const tSrc = (m) => (!tWhen(m) ? 'Top SQL' : m.span ? `${tWhen(m)} 的 Top SQL 时段榜` : `${tWhen(m)} 取数的 Top SQL 累计榜`);

// 调优入口全页只有一种写法(user 2026-09-23:两种说法并存让人以为是两件事)。
// m(榜单模型)给了就写明调用次数、平均耗时是哪一份榜上的:累计榜是取数那一刻的累计,时段榜是窗口内的增量。
export const tunePrompt = (r, m) => `请调优 sql_id 为 ${r.sql_id} 的语句(${tWhen(m) ? `${tSrc(m)}上` : ''}调用 ${r.calls} 次,平均 ${fmtMs(r.avg_ms)}):${brief(r.query, 200)}`;
/** 页头「在会话里深挖」与解读卡「先调哪一条」:同一句;带排序维度(按平均耗时排的榜和按总耗时排的不是一回事)。 */
export const tDigPrompt = (m) => `请基于${tSrc(m)}(按${byLabel(m.by)})分析哪些语句最值得优化,给出优先级。`;
/** 「重新取数」:时段榜重取同一个窗口;累计榜重新取一次,交代上一份的时间供对比。都按当前页签的维度取。 */
export const tRedoPrompt = (m) => (m.span ? `请重新取一次 ${tWhen(m) || '这段时间'} 的 Top SQL(按${byLabel(m.by)})。`
  : `请重新取一次当前库的 Top SQL(按${byLabel(m.by)})。${prevNote(m.entry && m.entry.at)}`);
/** 解读卡提问框与 ⌘J / ⌘K 自由提问的前缀。 */
export const tAbout = (m) => (m.span ? `关于实例 ${m.name} 在 ${tWhen(m) || '这段时间'} 的 Top SQL(窗口内增量):`
  : `关于实例 ${m.name} 在 ${tWhen(m)} 取的 Top SQL 榜单:`);
// 取数脚本在库端把 SQL 截到 80 字(客户中间件白名单里注册的脚本,改它要客户重新注册):满 80 字就标出来
export const QUERY_CAP = 80;
export const fullQuery = (q) => (String(q ?? '').length >= QUERY_CAP ? `${q} …(取数时截到 ${QUERY_CAP} 字)` : String(q ?? ''));
// 调优技能按策略跳过的系统 SQL:存档里 skipped = "system"
export const isSkipped = (t) => !!(t && t.skipped);
export const READS_NOTE = '取数脚本(客户中间件白名单里注册的那条)不返回逻辑读数值,占比仍按耗时;要看数值需客户重新注册脚本';

/** 时段 / 增量榜的评估窗口文字;累计榜返回 ''。 */
export function windowText(rep) {
  if (!rep) return '';
  if (rep.window) return windowLabel(rep.window);
  return rep.baseline_at && rep.sampled_at ? `${stamp(rep.baseline_at)} – ${stamp(rep.sampled_at)}` : '';
}

/** 取某个排序维度最近一份榜单并建模。没取过的维度返回 {ok:true, missing:true},页面给「去会话里取」。
 *  file(直达链接里的那一份)在时间段内就打开它 —— 时段榜 / 增量榜只能这样打开。 */
export async function load(key, byPref, range, file) {
  if (!key) return { ok: false, reason: 'none' };
  const index = await fetchIndex('topsql', key);
  if (!index.ok) return { ok: false, reason: index.status === 404 ? 'none' : 'error', error: index.error };
  const all = Array.isArray(index.data) ? index.data : [];
  const inRange = filterIndex('topsql', all, range);
  const per = latestPerBy(inRange);                                  // 时间段内各维度最近一份
  const avail = BY.filter(([b]) => per[b]);
  const pin = file ? inRange.find((e) => e.file === file && FILE.test(e.file)) : null;
  if (!avail.length && !pin) return { ok: false, reason: range && all.length ? 'range' : 'none' };
  const by = pin ? FILE.exec(pin.file)[1] : BY.some(([b]) => b === byPref) ? byPref : avail[0][0];
  if (!pin && !per[by]) return { ok: true, key, per, avail, by, missing: true };
  const entry = pin || per[by];
  const rep = await fetchReport('topsql', key, entry.file);
  if (!rep.ok) return { ok: false, reason: 'error', error: rep.error };
  const rows = rep.data.rows || [];
  const tunes = await Promise.all(rows.map((r) => fetchSqltune(key, r.sql_id).then((t) => (t.ok ? t.data : null))));
  const tune = Object.fromEntries(rows.map((r, i) => [r.sql_id, tunes[i]]));
  const sum = rows.reduce((a, r) => a + (+r.total_sec || 0), 0) || 1;
  // span:'span'(时段榜)/ 'delta'(增量榜)/ ''(累计榜)。前两种的数是窗口内增量,页面上不能写「自统计重置以来」
  const span = ['span', 'delta'].includes(rep.data.mode) ? rep.data.mode : '';
  return { ok: true, key, per, avail, by, entry, rep: rep.data, rows, tune, sum, span, win: span ? windowText(rep.data) : '',
    winq: span ? (rep.data.window ? windowPromptLabel(rep.data.window) : windowText(rep.data)) : '',   // 深挖首句用,带秒
    share: (r) => ((+r.total_sec || 0) / sum) * 100 };
}

/** 普通风格的 Top SQL 页。 */
export async function render(root, ctx) {
  const view = await import('../views/normal/topsql.js');
  const m = await load(ctx.key, readBy(), ctx.range, ctx.run && ctx.run.topsql);
  return view.render(root, m.ok && !m.missing ? { ...m, name: instName(ctx, m.rep.conn) } : m, ctx);
}
