// 健康检查:取数与建模。数据:/reports/health/<key>/latest.json(或 index 里指定的那一份)+ index.json。
// 级别全部来自技能脚本的确定性判定,这里只整理,不改级别。两种风格的版式在 views/ 下,共用这里的模型。

import { fetchLatest, fetchIndex, fetchReport } from '../lib/data.js';
import { esc, num, split, fmtMs, fmtSec, fmtInt, cn, stamp } from '../lib/fmt.js';
import { lk, instName } from '../lib/ui.js';
import { loadInRange, windowLabel } from '../lib/range.js';

// 维度名 = gaussdb-health/scripts/model.py 的 DIM_* 常量(契约守卫逐个核对,技能改名这里会先红)。
// 子技能的发现带它们自己的维度名(如 DB Time),不在这张表里就原样显示。
export const TITLES = {
  'Overview': '总览', 'Slow SQL': '慢 SQL', 'Long & Idle Transactions': '长事务与空闲事务',
  'Connections': '连接', 'Checkpoint / WAL / Archiving': '检查点 / WAL / 归档', 'Replication / Standby': '主备复制',
  'Schema / Objects': '对象与索引', 'Storage Usage': '占用量', 'Transactions / Concurrency': '事务并发', 'Wait Events': '等待事件',
  'Dead Tuples & Bloat': '死元组与膨胀', 'Lightweight Locks (LWLock)': '轻量锁', 'Transaction Locks & Blocking Chains': '锁与阻塞链',
  // 时段体检(window.py,DIM_WIN_*):窗口两端相减的增量
  'Evaluation Window': '评估窗口', 'Window Load': '时段负载', 'Window Transactions': '时段事务',
  'Window IO / Temp / Checkpoint': '时段 IO / 溢出 / 检查点', 'Window Waits': '时段等待', 'Window Top SQL': '时段 Top SQL',
};
export const title = (dim) => TITLES[dim] || dim;
export const METRIC = { avg_ms: ['平均耗时', ' ms'], idle_in_xact_min: ['事务空闲', ' 分钟'], idx_scan: ['索引扫描', ' 次'] };
export const metricName = (f) => (METRIC[f.metric] || [f.metric])[0];

/** 一份报告(index 条目)的级别;所有维度都没采到时是 null —— 那不是「正常」,是「不知道」。 */
export function entryLevel(e) {
  const dm = e && e.dims;
  if (dm && dm.total && dm.na === dm.total) return null;
  return e ? e.overall | 0 : null;
}
export const countsOf = (e) => Object.values((e && e.counts) || {}).reduce((a, b) => a + (+b || 0), 0);

// 各维度的关键读数:按 gaussdb-health/scripts/collectors.py 的表头(前缀)取值;取不到就不显示,不编。
const ci = (d, n) => (d.headers || []).findIndex((h) => String(h).startsWith(n));
const cell = (d, r, n) => { const i = ci(d, n), row = (d.rows || [])[r]; return i < 0 || !row ? null : row[i]; };
// 两列「项 / 值」表(时段体检的几个维度):按第一列的项名取第二列。项名在 window.py 里,契约守卫逐个核对
const item = (d, name) => { const row = (d.rows || []).find((x) => x[0] === name); return row ? row[1] : null; };
const KEYFIG = {
  'Overview': (d) => { const h = num(cell(d, 0, 'cache_hit')); return { v: h === null ? '—' : h.toFixed(1), u: '% 缓存命中', cap: `连接 ${cell(d, 0, 'connections') ?? '—'} / ${cell(d, 0, 'max_conn') ?? '—'}` }; },
  'Slow SQL': (d) => { if (!(d.rows || []).length) return { v: '0', u: '条', cap: '没有慢 SQL' }; const [v, u] = split(fmtMs(num(cell(d, 0, 'avg_ms')))); return { v, u: u + ' 平均耗时', cap: `最慢的是 ${cell(d, 0, 'sql_id')}，调用 ${fmtInt(num(cell(d, 0, 'calls')))} 次` }; },
  'Long & Idle Transactions': (d) => { const r = d.rows || []; if (!r.length) return { v: '0', u: '个', cap: '没有长事务' }; const mx = Math.max(...r.map((_, i) => num(cell(d, i, '时长')) || 0)); const [v, u] = split(fmtSec(mx)); return { v, u: u + ' 最长', cap: `${r.length} 个会话，pid ${cell(d, 0, 'pid')}` }; },
  'Connections': (d) => { const r = d.rows || [], t = r.reduce((a, x) => a + (num(x[1]) || 0), 0); return { v: String(t), u: '个会话', cap: r.map((x) => `${x[0] === 'active' ? '活跃' : x[0] === 'idle' ? '空闲' : x[0]} ${x[1]}`).join('，') }; },
  'Checkpoint / WAL / Archiving': (d) => { const row = (d.rows || []).find((x) => /timed\/req/.test(x[0])), arc = (d.rows || []).find((x) => /archive/.test(x[0])); if (!row) return null; const [t, q] = String(row[1]).split('/').map(num); return { v: (t + q ? (q / (t + q)) * 100 : 0).toFixed(1), u: '% 请求型', cap: `checkpoint ${fmtInt(t + q)} 次，归档 ${arc ? arc[1] : '—'}` }; },
  'Replication / Standby': (d) => { const r = d.rows || []; if (!r.length) return { v: '0', u: '个备机', cap: '没有备机' }; const [v, u] = split(String(cell(d, 0, 'replay_lag') || '—')); return { v, u: u + ' 回放延迟', cap: `${r.length} 个备机，${cell(d, 0, 'state') || ''} ${cell(d, 0, 'sync') || ''}` }; },
  'Schema / Objects': (d) => { const it = (d.rows || []).filter((x) => x[0] !== '观测窗口'); return { v: String(it.length), u: '项问题对象', cap: [...new Set(it.map((x) => x[0]))].join('、') || '没有问题对象' }; },
  // 占用量:取最大的那一行。**不显示「剩余」** —— 文件系统剩余空间在这个内核上
  // 取不到(pg_stat_file 要 initial account),卡片上写「剩余 x%」等于编一个没有的数。
  'Storage Usage': (d) => { const r = d.rows || []; if (!r.length) return null; const [v, u] = split(String(r[0][2] || '—')); return { v, u: u + ' 最大', cap: `${r[0][0]} ${r[0][1]}（共 ${r.length} 项，剩余空间不可知）` }; },
  'Evaluation Window': (d) => { const [v, u] = split(String(item(d, '窗口长度') || '—')); return { v, u, cap: item(d, '是否完整覆盖') === '是' ? '完整覆盖你要的时间段' : '没有完整覆盖，见明细' }; },
  'Window Load': (d) => { const a = num(item(d, '平均活跃会话')); return { v: a === null ? '—' : a.toFixed(2), u: '平均活跃会话', cap: `DB time ${item(d, 'DB time') || '—'}，CPU 占 ${item(d, 'CPU 占 DB time') || '—'}` }; },
  'Window Transactions': (d) => ({ v: String(item(d, '死锁') ?? '—'), u: '次死锁', cap: `提交 ${item(d, '提交') ?? '—'}，回滚率 ${item(d, '回滚率') ?? '—'}` }),
  'Window IO / Temp / Checkpoint': (d) => { const [v, u] = split(String(item(d, '临时文件溢出') || '—')); return { v, u: u + ' 临时溢出', cap: `命中率 ${item(d, '缓存命中率') || '—'}，checkpoint 定时 / 请求 ${item(d, 'checkpoint 定时 / 请求') || '—'}` }; },
  'Window Waits': (d) => { if (!(d.rows || []).length) return { v: '0', u: '秒', cap: '窗口内没有等待' }; const p = num(cell(d, 0, '占总等待')); return { v: p === null ? '—' : p.toFixed(1), u: `% ${cell(d, 0, '等待类')}`, cap: `占总等待（含后台线程），${cell(d, 0, '等待时长')} 秒` }; },
  'Window Top SQL': (d) => { if (!(d.rows || []).length) return { v: '0', u: '条', cap: '窗口内没有语句产生新的耗时' }; return { v: String(cell(d, 0, '窗口内耗时')), u: 's 第一名', cap: `${cell(d, 0, 'sql_id')}，窗口内调用 ${cell(d, 0, '窗口内调用')} 次` }; },
  'Transactions / Concurrency': (d) => { const dl = (d.rows || []).find((x) => /deadlock/.test(x[0])), cr = (d.rows || []).find((x) => /commit/.test(x[0])); const rb = cr ? (/([\d.]+)%/.exec(cr[1]) || [])[1] : null; return { v: dl ? String(dl[1]) : '—', u: '次死锁', cap: `回滚率 ${rb ?? '—'}%` }; },
};
/** 维度的关键读数 {v, u, cap};没采到或没有读数规则时 null。 */
export const keyFig = (d) => (d.available === false ? null : (KEYFIG[d.dimension] || (() => null))(d));

/** 深挖首句:只放这条发现的数据。 */
export const fPrompt = (f) => (f.sql_id
  ? `请解释并优化 sql_id 为 ${f.sql_id} 的语句:健康检查发现 ${f.code},${f.metric}=${f.value}(阈值 ${f.threshold})。`
  : `请针对健康检查发现 ${f.code}(${f.metric}=${f.value},阈值 ${f.threshold})做深入分析:${f.evidence}`);

export const LOOKBACK = '回看不了';
/** 没采到的维度在图例里怎么称呼:全是回看不了 / 全是采集失败 / 两种都有。 */
export const naLegend = (dims) => {
  const na = dims.filter((x) => x.level === null), look = na.filter((x) => x.na).length;
  return !look ? '采集失败' : look === na.length ? LOOKBACK : `${LOOKBACK} / 采集失败`;
};

/** 索引条目的称呼:时段体检写评估窗口(「时段 10-17 10:46 – 11:06」),此刻巡检写存档时间。 */
export const entryLabel = (e) => (e && e.mode === 'span' && e.window ? `时段 ${windowLabel(e.window)}` : stamp(e && e.at));

/** 取一份报告并建模。file 为空取最新;key 为空或 404 返回 {ok:false, reason:'none'}。
 *  range(时间段)给了:只在时间段内的报告里取,走势与上期对比也只算时间段内的;一份都没有 → reason 'range'。 */
export async function load(key, file, range) {
  if (!key) return { ok: false, reason: 'none' };
  if (range) {
    const got = await loadInRange('health', key, file, range);
    return got.ok ? model(key, got.rep.data, got.idx, got.file) : got;
  }
  const [got, index] = await Promise.all([file ? fetchReport('health', key, file) : fetchLatest('health', key), fetchIndex('health', key)]);
  const all = index.ok && Array.isArray(index.data) ? index.data : [];
  let rep = got, f = file;
  // 时段体检不写 latest.json(不顶掉此刻巡检):只跑过时段体检时 latest 是 404,退回索引里最新的一份
  if (!rep.ok && rep.status === 404 && !file && all.length) {
    f = all[all.length - 1].file;
    rep = await fetchReport('health', key, f);
  }
  if (!rep.ok) return { ok: false, reason: rep.status === 404 ? 'none' : 'error', error: rep.error };
  return model(key, rep.data, all, f);
}

export function model(key, d, all, file) {
  // 走势、上期对比、巡检节奏只在**同一种口径**里算:时段体检(mode=span)与此刻巡检的维度不同,
  // 混在一起比「发现比上次多 3 条」没有意义。选报告的菜单(all)两种都列。
  const mode = d.mode || '';
  const idx = all.filter((e) => (e.mode || '') === mode);
  const findings = [...(d.findings || [])].sort((a, b) => (b.severity | 0) - (a.severity | 0));
  const worst = {};
  for (const f of findings) worst[f.dimension] = Math.max(worst[f.dimension] || 0, f.severity | 0);
  // na:时段体检里「回看不了」的瞬时维度(脚本的 headline 就是这四个字)。它们不是采集失败,页面上要说对 ——
  // 真失败的(如时段 Top SQL 没取到)headline 是「不可用」,仍按采集失败画。
  const dims = (d.dims || []).map((x, i) => ({ ...x, i, level: x.available === false ? null : (worst[x.dimension] || 0),
    na: x.available === false && x.headline === LOOKBACK ? LOOKBACK : '' }));
  // 全部维度都没采到:overall 是 0 只因为没有发现,不能画成「正常」
  const allNa = dims.length > 0 && dims.every((x) => x.level === null);
  const pos = file ? idx.findIndex((e) => e.file === file) : idx.length - 1;
  const names = new Set(dims.map((x) => x.dimension));
  const extra = [...new Set(findings.filter((f) => !names.has(f.dimension)).map((f) => f.dimension))];
  const checks = [
    ...dims.map((x) => ({ name: title(x.dimension), lv: lk(x.level), naWord: x.na, attr: ` data-dim="${x.i}"`, tip: `<b>${esc(title(x.dimension))}</b><div class="d">${esc(cn(x.headline || ''))}</div>` })),
    ...extra.map((x) => ({ name: title(x), lv: lk(worst[x]), attr: ` data-xdim="${esc(x)}"`, tip: `<b>${esc(title(x))}</b><div class="d">子技能报出的发现,不属于巡检维度</div>` })),
  ];
  return { ok: true, key, d, idx, all, pos, entry: idx[pos] || null, prev: pos > 0 ? idx[pos - 1] : null,
    findings, dims, extra, checks, allNa, level: allNa ? null : d.overall | 0, subs: d.sub_skills || [],
    span: mode === 'span', win: mode === 'span' ? windowLabel(d.window) : '' };
}

/** 普通风格的健康检查页。 */
export async function render(root, ctx) {
  const view = await import('../views/normal/health.js');
  const m = await load(ctx.key, ctx.run && ctx.run.health, ctx.range);
  return view.render(root, m.ok ? { ...m, name: instName(ctx, m.d.conn) } : m, ctx);
}
