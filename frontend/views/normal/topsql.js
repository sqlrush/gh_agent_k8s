// 普通风格 · Top SQL:主视觉 = 耗时树图 + 智能体解读 + 四个统计;其下 = 榜单(五个排序页签)、调用×耗时散点、调优进度。
// 口径:累计统计视图,不是时间窗口;一份报告一个排序维度,没取过的维度一键去会话里取。
// 直达链接打开的时段榜 / 增量榜(m.span):数是窗口内增量,页头写评估时段与口径,不画页签(页签是累计榜)。

import { esc, stamp, cn, fmtInt, fmtSec, fmtMs, split } from '../../lib/fmt.js';
import { I, glyph, tipAttr, dig, cu, primary } from '../../lib/ui.js';
import { rise } from '../../lib/motion.js';
import { setModel } from '../../lib/store.js';
import { treemap, scatter } from '../../lib/viz.js';
import * as insight from '../../lib/insight.js';
import { BY, byLabel, tunePrompt, isSkipped, READS_NOTE } from '../../pages/topsql.js';
import { pageHead, tile, insightCard, heroStats, emptyHero, errorHero, rangeHero } from './common.js';

/** 五个排序页签都能点:取过的切过去(data-by),没取过的写「未取」,点了也切过去并给「去会话里取」。 */
const byTabs = (m) => (m.span ? `<button class="btn" data-by="${esc(m.by)}">看累计榜</button>` : `<div class="seg">${BY.map(([b, l]) => `<button data-by="${b}" class="${b === m.by ? 'on' : ''}"${m.per[b] ? '' : tipAttr(`还没按「${esc(l)}」取过<div class="d">点一下看怎么取</div>`)}>${esc(l)}${m.per[b] ? '' : '<small class="t3"> 未取</small>'}</button>`).join('')}</div>`);

export function render(root, m, ctx) {
  const target = m.name || (ctx.target && ctx.target.label) || '';
  const head = (act, desc) => pageHead('Top SQL', '', act, desc);
  if (!m.ok) {
    root.innerHTML = head('', []) + (m.reason === 'error' ? errorHero(m.error) : m.reason === 'range' ? rangeHero(ctx.range) : emptyHero(ctx.key ? '这个实例还没有 Top SQL 报告' : '还没有 Top SQL 报告', '在会话里取一次，结果会自动出现在这里。', '请列出当前库的 Top SQL(按总耗时)。', '在会话里取一次 Top SQL'));
    return;
  }
  if (m.missing) {
    const label = byLabel(m.by);
    root.innerHTML = head('', [['实例', esc(target)], ['已取过', esc(m.avail.map(([, l]) => l).join('、'))]])
      + `<div class="bento">${tile({ title: 'SQL 榜单', tools: byTabs(m), body: `<div class="empty-state" style="padding:40px">${glyph(null)}<h4>「${esc(label)}」这个维度还没取过</h4><p>大盘只展示在会话里真正取过的维度。</p>${primary(`请按${label}取一次 Top SQL。`, `在会话里按${label}取一次`, 'Top SQL')}</div>` })}</div>`;
    return;
  }
  setModel('topsql', m);
  const rows = m.rows, label = byLabel(m.by), field = (BY.find(([b]) => b === m.by) || [])[2];
  const sumC = rows.reduce((a, r) => a + (+r.calls || 0), 0), top = [...rows].sort((a, b) => b.total_sec - a.total_sec)[0], top1 = top ? m.share(top) : 0;
  const [tv, tu] = split(fmtSec(m.sum)), [cv, cu2] = split(fmtInt(sumC));
  const stats = [{ l: '上榜语句', v: cu(String(rows.length)), u: '条', s: `按${esc(label)}取前 ${esc(m.rep.limit)} 条` },
    { l: '上榜总调用', v: cu(cv), u: `${cu2 ? cu2 + ' ' : ''}次`, s: m.span ? '窗口内' : '自统计视图重置以来' },
    { l: '上榜总耗时', v: cu(tv), u: tu, s: m.sum >= 60 ? `合计 ${fmtInt(Math.round(m.sum))} 秒` : '上榜语句合计' },
    { l: '耗时第一占上榜', v: cu(top1.toFixed(1)), u: '%', s: `${esc(top ? top.sql_id : '')}${top && isSkipped(m.tune[top.sql_id]) ? '，系统 SQL' : ''}` }];
  const tm = `<div class="glass tmbox rise"${rise()}><div class="title">${I.grid}上榜耗时构成<span class="t3">面积 = 总耗时占上榜合计 · 斜纹为系统 SQL · 点块看详情</span></div>${treemap(rows, m.tune, isSkipped)}</div>`;
  const th = (name, f) => `<th class="r">${name}${f === field ? I.sortd : ''}</th>`;
  const tagOf = (r) => { const x = m.tune[r.sql_id]; return isSkipped(x) ? '<span class="tag gray">系统 SQL</span>' : x ? '<span class="tag blue">已调优</span>' : '<span class="t3">未调优</span>'; };
  const table = `<div class="scroll"><table class="tbl"><thead><tr><th>排名</th><th>sql_id</th><th>语句</th>${th('调用次数', 'calls')}${th('总耗时', 'total_sec')}${th('平均耗时', 'avg_ms')}${th('返回行', 'rows')}<th>占上榜耗时</th><th>调优</th><th class="r">操作</th></tr></thead><tbody>${rows.map((r, i) => { const p = m.share(r).toFixed(1);
    return `<tr><td class="t3">${i + 1}</td><td class="id"><a href="#" data-sql="${esc(r.sql_id)}">${esc(r.sql_id)}</a>${r.is_new ? ' <span class="tag gray"' + tipAttr('窗口起点时还没有这条语句的统计，整条计入') + '>新</span>' : ''}</td><td class="q" title="${esc(r.query)}">${esc(r.query)}</td><td class="r">${fmtInt(r.calls)}</td><td class="r">${fmtSec(r.total_sec)}</td><td class="r">${fmtMs(r.avg_ms)}</td><td class="r">${fmtInt(r.rows)}</td>
      <td><div class="share"><div class="b"><i class="grow" style="width:${p}%"></i></div><b>${p}%</b></div></td><td>${tagOf(r)}</td><td class="ops"><a href="#" data-sql="${esc(r.sql_id)}">详情</a>${isSkipped(m.tune[r.sql_id]) ? '' : `<a href="#"${dig(tunePrompt(r), 'SQL 调优')}>调优</a>`}</td></tr>`; }).join('')}</tbody></table></div>`;
  const progress = `<div class="list">${rows.slice(0, 12).map((r) => { const x = m.tune[r.sql_id], f = x && x.evidence && (x.evidence.findings || [])[0];
    return `<div class="it">${isSkipped(x) ? glyph(null) : x ? glyph(0) : '<span class="glyph pending"></span>'}<div class="m"><b>${esc(r.sql_id)}</b><small>${isSkipped(x) ? `系统 SQL，引用 ${esc((x.system_objects || []).join('、'))}，按策略不调优` : f ? `${esc(cn(f.detail || ''))}${f.advice ? `；建议${esc(cn(f.advice))}` : ''}` : x ? '已调优' : '还没有调优过'}</small></div>${isSkipped(x) ? `<a href="#" class="r" data-sql="${esc(r.sql_id)}">查看</a>` : `<a href="#" class="r"${dig(tunePrompt(r), 'SQL 调优')}>${x ? '继续调优' : '调优'}</a>`}</div>`; }).join('')}</div>`;
  const nTuned = rows.filter((r) => m.tune[r.sql_id] && !isSkipped(m.tune[r.sql_id])).length, nNone = rows.filter((r) => !m.tune[r.sql_id]).length;
  const redo = m.span ? `请重新取一次 ${m.win || '这段时间'} 的 Top SQL。` : '请重新取一次当前库的 Top SQL。';
  const desc = m.span
    ? [['实例', esc(target)], ['评估时段', esc(m.win || '—')], ['口径', m.span === 'span' ? '窗口两端相减的增量，不是累计值' : '两次采样相减的增量，不是累计值'], ['存档时间', esc(stamp(m.entry.at))]]
    : [['实例', esc(target)], ['取数时间', esc(stamp(m.entry.at))], ['口径', '累计统计视图，不是时间窗口；含系统与监控 SQL（调优按策略跳过这类）']];
  const about = m.span ? `关于实例 ${m.name} 在 ${m.win || '这段时间'} 的 Top SQL(窗口内增量):` : `关于实例 ${m.name} 在 ${stamp(m.entry.at)} 取的 Top SQL 榜单:`;
  // 实际评估的是哪一段、为什么(技能写的 span_note):用户问的时间段与实际窗口可能不一样,页面上也要看得到
  const note = m.span && m.rep.span_note ? tile({ title: '评估窗口', desc: '你要看的时间段与实际评估的窗口', body: `<p>${esc(cn(m.rep.span_note))}</p>` }) : '';
  root.innerHTML = pageHead('Top SQL', m.span ? '<span class="tag blue">时段榜</span>' : '', `<button class="btn"${dig(redo, 'Top SQL')}>${I.redo}重新取数</button>` + primary('请基于最近一次 Top SQL 结果分析哪些语句最值得优化,给出优先级。', '在会话里深挖', 'Top SQL'), desc)
    + `<section class="hero"><div class="hero-grid tm">${tm}${insightCard({ ...insight.topsql(m), ctx: about })}</div>${heroStats(stats)}</section>`
    + `<div class="bento">${note}${tile({ title: 'SQL 榜单', desc: `按${esc(label)}排序，点 sql_id 查看语句与调优结论${m.by === 'reads' ? `；${esc(READS_NOTE)}` : ''}`, body: table, tools: byTabs(m), flush: true })}
      ${tile({ span: 'b7', title: '调用次数 × 平均耗时', desc: '找又多又慢的语句', body: scatter(rows, m.tune, isSkipped, 700) })}
      ${tile({ span: 'b5', title: '调优进度', desc: `${nTuned} 条已调优，${nNone} 条未调优`, body: progress })}</div>`;
}
