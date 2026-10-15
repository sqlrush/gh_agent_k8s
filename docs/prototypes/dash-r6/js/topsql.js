/* R6 Top SQL:主视觉 = 耗时树图 + 智能体解读;其下 = 榜单、调用×耗时散点、调优进度。
   口径:累计统计视图,一份报告一个排序维度,没取过的维度不可选。 */
(function (R) {
  'use strict';
  const { D, S, esc, I, glyph, tipAttr, dig, fmtInt, fmtSec, fmtMs, split, stamp, cn } = R;

  const BY = [['time', '总耗时', 'total_sec'], ['avg', '平均耗时', 'avg_ms'], ['calls', '调用次数', 'calls'], ['reads', '逻辑读', null], ['rows', '返回行', 'rows']];
  const KW = /\b(SELECT|FROM|WHERE|GROUP BY|ORDER BY|LEFT JOIN|JOIN|ON|AND|OR|UPDATE|SET|INSERT INTO|VALUES|DELETE|AS|IN|LIMIT|HAVING|sum|count)\b/gi;
  const hl = (sql) => esc(sql).replace(KW, (m) => `<span class="kw">${m}</span>`).replace(/(\$\d+)/g, '<span class="ph">$1</span>');
  const tunePrompt = (r) => `请调优 sql_id 为 ${r.sql_id} 的语句（调用 ${r.calls} 次，平均 ${fmtMs(r.avg_ms)}）：${r.query}`;

  function data() {
    const x = D.topsql[S.key], ents = R.real('topsql', S.key);
    if (!x || !ents.length) return null;
    const per = {}; ents.forEach((e) => { per[x.runs[e.file].by] = e; });
    const by = S.by && per[S.by] ? S.by : x.runs[ents[ents.length - 1].file].by;
    return { per, by, e: per[by], rep: x.runs[per[by].file], tune: D.sqltune[S.key] || {} };
  }
  const isSys = (t, r) => !!(t.tune[r.sql_id] && t.tune[r.sql_id].skipped);
  const sqlChip = (id) => `<span class="ev" data-sql="${esc(id)}">${I.code}${esc(id)}</span>`;

  function insightFor(t) {
    const rows = t.rep.rows || [], sum = rows.reduce((a, r) => a + (+r.total_sec || 0), 0) || 1, pct = (r) => (((+r.total_sec || 0) / sum) * 100).toFixed(1);
    const byT = [...rows].sort((a, b) => b.total_sec - a.total_sec), top = byT[0], biz = byT.filter((r) => !isSys(t, r));
    const slow = [...rows].sort((a, b) => b.avg_ms - a.avg_ms)[0], tuned = (r) => t.tune[r.sql_id] && t.tune[r.sql_id].evidence;
    const lines = [];
    if (isSys(t, top)) lines.push(`${sqlChip(top.sql_id)} 占上榜耗时 <b>${pct(top)}%</b>，只引用系统对象 ${esc((t.tune[top.sql_id].system_objects || []).join('、'))}，按策略不调优。`);
    else lines.push(`${sqlChip(top.sql_id)} 占上榜耗时 <b>${pct(top)}%</b>，调用 ${esc(fmtInt(top.calls))} 次，平均 ${esc(fmtMs(top.avg_ms))}。`);
    const b0 = biz[0];
    if (b0 && b0 !== top) lines.push(`业务语句里总耗时最高的是 ${sqlChip(b0.sql_id)}：占 <b>${pct(b0)}%</b>，平均 ${esc(fmtMs(b0.avg_ms))}，调用 ${esc(fmtInt(b0.calls))} 次，${tuned(b0) ? '已有调优结论' : '还没有调优过'}。`);
    if (slow && slow !== b0 && slow !== top) {
      const ev = tuned(slow), f = ev && (ev.findings || [])[0];
      lines.push(`单次最慢的是 ${sqlChip(slow.sql_id)}：平均 <b>${esc(fmtMs(slow.avg_ms))}</b>${f ? `，已调优：${esc(cn(f.detail))}` : `，调用 ${esc(fmtInt(slow.calls))} 次`}。`);
    }
    lines.push('口径是累计统计视图，不是时间窗口；看某段时间的负载用 <a href="#/wdr">WDR 分析</a>。');
    const head = isSys(t, top) && b0 ? `耗时第一的是系统 SQL，业务语句先看 ${esc(b0.sql_id)}` : `${esc(top.sql_id)} 占上榜耗时 ${pct(top)}%，最值得先看`;
    const chips = [];
    if (b0) chips.push([`调优 ${b0.sql_id}`, tunePrompt(b0)]);
    if (isSys(t, top)) chips.push(['系统 SQL 为什么占这么多', `Top SQL 里系统语句 ${top.sql_id} 占上榜耗时 ${pct(top)}%，请分析原因，是否需要调整监控采集频率。`]);
    if (slow && slow !== b0) chips.push([`${slow.sql_id} 的索引建议能落地吗`, `sql_id ${slow.sql_id} 的调优建议能否直接落地？请评估索引的写入代价。`]);
    chips.push(['对照 WDR 窗口', '请把累计 Top SQL 与最近一个 WDR 窗口里的 Top SQL 对照，说明差异。']);
    return { src: `依据 ${stamp(t.e.at)} 取数 · 按${BY.find((b) => b[0] === t.by)[1]}排序`, ask: '问问这份榜单，比如：先调哪一条？', head, lines, chips };
  }

  function render() {
    const t = data();
    if (!t) return R.pageHead('Top SQL', '', '', []) + R.emptyHero('还没有 Top SQL 报告', '在会话里取一次，结果会自动出现在这里。', '请列出当前实例总耗时最高的 SQL。', '取一次 Top SQL');
    const { per, by, e, rep, tune } = t, rows = rep.rows || [], [, label, field] = BY.find((b) => b[0] === by);
    const sumT = rows.reduce((a, r) => a + (+r.total_sec || 0), 0) || 1, sumC = rows.reduce((a, r) => a + (+r.calls || 0), 0);
    const top = [...rows].sort((a, b) => b.total_sec - a.total_sec)[0], top1 = top ? ((+top.total_sec || 0) / sumT) * 100 : 0;
    const [tv, tu] = split(fmtSec(sumT)), [cv, cu] = split(fmtInt(sumC));
    const stats = [{ l: '上榜语句', v: R.cu(String(rows.length)), u: '条', s: `按${esc(label)}取前 ${esc(rep.limit)} 条` },
      { l: '上榜总调用', v: R.cu(cv), u: `${cu ? cu + ' ' : ''}次`, s: '自统计视图重置以来' },
      { l: '上榜总耗时', v: R.cu(tv), u: tu, s: `合计 ${fmtInt(Math.round(sumT))} 秒` },
      { l: '耗时第一占比', v: R.cu(top1.toFixed(1)), u: '%', s: `${esc(top ? top.sql_id : '')}${top && isSys(t, top) ? '，系统 SQL' : ''}` }];
    const tm = `<div class="glass tmbox rise"${R.rise()}><div class="title">${I.grid}上榜耗时构成<span class="t3">面积 = 总耗时占比 · 斜纹为系统 SQL · 点块看详情</span></div>${R.treemap(rows, tune)}</div>`;
    const seg = `<div class="seg">${BY.map(([b, l]) => `<button data-by="${b}" class="${b === by ? 'on' : ''}" ${per[b] ? '' : 'disabled'}${per[b] ? '' : tipAttr(`还没按「${esc(l)}」取过<div class="d">在会话里说「按${esc(l)}看 Top SQL」</div>`)}>${esc(l)}</button>`).join('')}</div>`;
    const th = (name, f) => `<th class="r">${name}${f === field ? I.sortd : ''}</th>`;
    const tagOf = (r) => { const x = tune[r.sql_id]; return x && x.skipped ? '<span class="tag gray">系统 SQL</span>' : x ? '<span class="tag blue">已调优</span>' : '<span class="t3">未调优</span>'; };
    const table = `<div class="scroll"><table class="tbl"><thead><tr><th>排名</th><th>sql_id</th><th>语句</th>${th('调用次数', 'calls')}${th('总耗时', 'total_sec')}${th('平均耗时', 'avg_ms')}${th('返回行', 'rows')}<th>占上榜耗时</th><th>调优</th><th class="r">操作</th></tr></thead><tbody>${rows.map((r, i) => { const p = (((+r.total_sec || 0) / sumT) * 100).toFixed(1);
      return `<tr><td class="t3">${i + 1}</td><td class="id"><a href="#" data-sql="${esc(r.sql_id)}">${esc(r.sql_id)}</a></td><td class="q" title="${esc(r.query)}">${esc(r.query)}</td><td class="r">${fmtInt(r.calls)}</td><td class="r">${fmtSec(r.total_sec)}</td><td class="r">${fmtMs(r.avg_ms)}</td><td class="r">${fmtInt(r.rows)}</td><td><div class="share"><div class="b"><i class="grow" style="width:${p}%"></i></div><b>${p}%</b></div></td><td>${tagOf(r)}</td><td class="ops"><a href="#" data-sql="${esc(r.sql_id)}">详情</a><a href="#"${R.ask(tunePrompt(r))}>问智能体</a></td></tr>`; }).join('')}</tbody></table></div>`;
    const progress = `<div class="list">${rows.map((r) => { const x = tune[r.sql_id], f = x && x.evidence && (x.evidence.findings || [])[0];
      return `<div class="it">${x && x.skipped ? glyph(null) : x ? glyph(0) : '<span class="glyph pending"></span>'}<div class="m"><b>${esc(r.sql_id)}</b><small>${x && x.skipped ? `系统 SQL，引用 ${esc((x.system_objects || []).join('、'))}，不调优` : f ? `${esc(cn(f.detail))}；建议${esc(cn(f.advice))}` : '还没有调优过'}</small></div><a href="#" class="r"${x && x.skipped ? ` data-sql="${esc(r.sql_id)}">查看` : `${R.ask(tunePrompt(r))}>${x ? '继续调优' : '调优'}`}</a></div>`; }).join('')}</div>`;
    return R.pageHead('Top SQL', '', `<button class="btn"${dig('请重新取一次当前实例的 Top SQL。')}>${I.redo}重新取数</button>` + R.primary('请分析当前实例 Top SQL 榜单，指出最值得先调的语句。', '在会话里分析'),
      [['实例', esc(rep.conn)], ['取数时间', esc(stamp(e.at))], ['口径', '累计统计视图，不是时间窗口；含系统与监控 SQL']])
      + `<section class="hero"><div class="hero-grid tm">${tm}${R.insight(insightFor(t))}</div>${R.heroStats(stats)}</section>`
      + `<div class="bento">${R.tile({ title: 'SQL 榜单', desc: `按${esc(label)}排序，点 sql_id 查看详情与调优结论`, body: table, tools: seg, flush: true })}
        ${R.tile({ span: 'b7', title: '调用次数 × 平均耗时', desc: '找又多又慢的语句', body: R.scatter(rows, tune, 700) })}
        ${R.tile({ span: 'b5', title: '调优进度', desc: `${rows.filter((r) => tune[r.sql_id] && !tune[r.sql_id].skipped).length} 条已调优，${rows.filter((r) => !tune[r.sql_id]).length} 条未调优`, body: progress })}</div>`;
  }

  function openSql(id) {
    const t = data(); if (!t) return;
    const s = (t.rep.rows || []).find((r) => r.sql_id === id); if (!s) return;
    const x = t.tune[s.sql_id], sv = { info: 1, warn: 2, crit: 3 };
    const tuneHtml = x && x.skipped ? `<div class="alert" data-lv="na">${glyph(null)}<div><b>系统 SQL，按策略不调优</b><p>只引用了系统对象 ${esc((x.system_objects || []).join('、'))}。这类慢多半来自采集频率或系统压力，不是语句本身的问题。</p></div></div>`
      : x && x.evidence ? (x.evidence.findings || []).map((f) => `<div class="alert" data-lv="${R.lk(sv[f.severity] ?? 1)}">${glyph(sv[f.severity] ?? 1)}<div><b>${esc(cn(f.detail))}</b><p>建议：${esc(cn(f.advice))}</p></div></div>`).join('')
        + `<div class="sec">执行计划<span class="t3" style="font-weight:400;font-size:13px;margin-left:8px">${x.evidence.analyzed ? '实际执行' : '估算'}，${esc(x.evidence.version)}</span></div><div class="plan">${esc(x.evidence.plan)}</div>`
      : `<div class="alert" data-lv="na">${glyph(null)}<div><b>还没有调优过</b><p>在会话里调优后，建议与执行计划会出现在这里。</p></div></div>`;
    const kv = (k, v) => `<div><div class="kv"><div class="k">${k}</div><div class="v num">${v}</div></div></div>`;
    R.drawer(`SQL 详情 ${esc(s.sql_id)}`, x && x.skipped ? '<span class="tag gray">系统 SQL</span>' : x ? '<span class="tag blue">已调优</span>' : '',
      `<div class="sec">语句</div><div class="sql">${hl(s.query)}</div>
       <div class="sec">执行统计</div><div class="kvs" style="--cols:4">${kv('调用次数', fmtInt(s.calls))}${kv('总耗时', fmtSec(s.total_sec))}${kv('平均耗时', fmtMs(s.avg_ms))}${kv('返回行', fmtInt(s.rows))}</div>
       <div class="sec">调优结论</div>${tuneHtml}`,
      `<button class="btn primary"${R.ask(tunePrompt(s))}>${I.spark}${x && !x.skipped ? '继续调优' : '问智能体'}</button><button class="btn" data-copy="${esc(s.sql_id)}">复制 sql_id</button>`);
  }

  function assist() {
    const t = data();
    if (!t) return { ctx: ['Top SQL', `实例 ${S.key}`, '还没有报告'], sugg: [['取一次 Top SQL', '按总耗时取前 10 条', '请列出当前实例总耗时最高的 SQL。']] };
    const rows = t.rep.rows || [], biz = [...rows].filter((r) => !isSys(t, r)).sort((a, b) => b.total_sec - a.total_sec);
    return { ctx: ['Top SQL', `实例 ${t.rep.conn}`, `取数 ${stamp(t.e.at)}`, `按${BY.find((b) => b[0] === t.by)[1]}`],
      sugg: [['先调哪一条？', '综合总耗时、单次耗时和调用次数', '请分析当前实例 Top SQL 榜单，指出最值得先调的语句。'],
        ...biz.slice(0, 2).map((r) => [`调优 ${r.sql_id}`, `${fmtInt(r.calls)} 次，平均 ${fmtMs(r.avg_ms)}`, tunePrompt(r)]),
        ['系统 SQL 能不能少跑点？', '监控采集频率与系统视图开销', '请分析 Top SQL 里系统语句的来源，是否需要调整监控采集频率。']] };
  }
  function commands() {
    const t = data(); if (!t) return [];
    return (t.rep.rows || []).map((r) => ({ grp: 'SQL', label: r.sql_id, sub: r.query, ic: 'code', go: () => { location.hash = '#/topsql'; setTimeout(() => openSql(r.sql_id), 0); } }));
  }
  R.pages.topsql = { render, assist, commands, openSql };
})(window.R6);
