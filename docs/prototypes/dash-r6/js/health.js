/* R6 健康检查:主视觉 = 维度圆环 + 智能体解读 + 四个统计;其下便当盒 = 发现表、巡检节奏、维度格、子技能。 */
(function (R) {
  'use strict';
  const { D, S, esc, I, lk, lname, glyph, status, tag, tipAttr, dig, num, fmtInt, fmtSec, fmtMs, split, stamp, cn, joinCn, colFmt } = R;

  const TITLES = {
    'Overview': '总览', 'Slow SQL': '慢 SQL', 'Long & Idle Transactions': '长事务与空闲事务', 'Connections': '连接',
    'Checkpoint / WAL / Archiving': '检查点与归档', 'Replication / Standby': '主备复制', 'Schema / Objects': '对象与索引',
    'Transactions / Concurrency': '事务并发', 'Wait Events': '等待事件', 'Dead Tuples & Bloat': '死元组与膨胀',
    'Lightweight Locks (LWLock)': '轻量锁', 'Transaction Locks & Blocking Chains': '锁与阻塞链',
  };
  const dt = (R.dt = (d) => TITLES[d] || d);
  const METRIC = { avg_ms: ['平均耗时', ' ms'], idle_in_xact_min: ['事务空闲', ' 分钟'], idx_scan: ['索引扫描', ' 次'] };
  const dimLevel = (d) => (d.available === false ? null : (d.findings || []).reduce((a, f) => Math.max(a, f.severity | 0), 0));
  const ci = (d, n) => (d.headers || []).findIndex((h) => String(h).startsWith(n));
  const cell = (d, r, n) => { const i = ci(d, n), row = (d.rows || [])[r]; return i < 0 || !row ? null : row[i]; };
  const KEYFIG = {
    'Overview': (d) => { const h = num(cell(d, 0, 'cache_hit')); return { v: h === null ? '—' : h.toFixed(1), u: '% 缓存命中', cap: `连接 ${cell(d, 0, 'connections') ?? '—'} / ${cell(d, 0, 'max_conn') ?? '—'}` }; },
    'Slow SQL': (d) => { if (!(d.rows || []).length) return { v: '0', u: '条', cap: '没有慢 SQL' }; const [v, u] = split(fmtMs(num(cell(d, 0, 'avg_ms')))); return { v, u: u + ' 平均耗时', cap: `最慢的是 ${cell(d, 0, 'sql_id')}，调用 ${fmtInt(num(cell(d, 0, 'calls')))} 次` }; },
    'Long & Idle Transactions': (d) => { const r = d.rows || []; if (!r.length) return { v: '0', u: '个', cap: '没有长事务' }; const mx = Math.max(...r.map((_, i) => num(cell(d, i, '时长')) || 0)); const [v, u] = split(fmtSec(mx)); return { v, u: u + ' 最长', cap: `${r.length} 个会话，pid ${cell(d, 0, 'pid')}` }; },
    'Connections': (d) => { const r = d.rows || [], t = r.reduce((a, x) => a + (num(x[1]) || 0), 0); return { v: String(t), u: '个会话', cap: r.map((x) => `${x[0] === 'active' ? '活跃' : x[0] === 'idle' ? '空闲' : x[0]} ${x[1]}`).join('，') }; },
    'Checkpoint / WAL / Archiving': (d) => { const row = (d.rows || []).find((x) => /timed\/req/.test(x[0])), arc = (d.rows || []).find((x) => /archive/.test(x[0])); if (!row) return null; const [t, q] = String(row[1]).split('/').map(num); return { v: (t + q ? (q / (t + q)) * 100 : 0).toFixed(1), u: '% 请求型', cap: `checkpoint ${fmtInt(t + q)} 次，归档 ${arc ? arc[1] : '—'}` }; },
    'Replication / Standby': (d) => { const r = d.rows || []; if (!r.length) return { v: '0', u: '个备机', cap: '没有备机' }; const [v, u] = split(String(cell(d, 0, 'replay_lag') || '—')); return { v, u: u + ' 回放延迟', cap: `${r.length} 个备机，${cell(d, 0, 'state') || ''} ${cell(d, 0, 'sync') || ''}` }; },
    'Schema / Objects': (d) => { const it = (d.rows || []).filter((x) => x[0] !== '观测窗口'); return { v: String(it.length), u: '项问题对象', cap: [...new Set(it.map((x) => x[0]))].join('、') || '没有问题对象' }; },
    'Transactions / Concurrency': (d) => { const dl = (d.rows || []).find((x) => /deadlock/.test(x[0])), cr = (d.rows || []).find((x) => /commit/.test(x[0])); const rb = cr ? (/([\d.]+)%/.exec(cr[1]) || [])[1] : null; return { v: dl ? String(dl[1]) : '—', u: '次死锁', cap: `回滚率 ${rb ?? '—'}%` }; },
  };
  const fPrompt = (f) => (f.sql_id ? `请解释并优化 sql_id 为 ${f.sql_id} 的语句：健康检查发现 ${f.code}，${f.metric}=${f.value}（阈值 ${f.threshold}）。` : `请针对健康检查发现 ${f.code}（${f.metric}=${f.value}，阈值 ${f.threshold}）做深入分析：${f.evidence}`);
  const sevWord = (s) => (s >= 3 ? '达到严重级别' : s === 2 ? '越过告警阈值' : '需要关注');

  /** 报告里的检查项:8 个维度,加上子技能报出、不属于任何维度的发现(如 DB Time)。 */
  function checks(r) {
    const dims = r.dims || [], names = new Set(dims.map((d) => d.dimension)), fs = r.findings || [];
    const extra = [...new Set(fs.filter((f) => !names.has(f.dimension)).map((f) => f.dimension))];
    return [
      ...dims.map((d, i) => ({ name: dt(d.dimension), lv: lk(dimLevel(d)), attr: ` data-dim="${i}"`, tip: `<b>${esc(dt(d.dimension))}</b><div class="d">${esc(cn(d.headline || ''))}</div>` })),
      ...extra.map((x) => { const l = fs.filter((f) => f.dimension === x).reduce((a, f) => Math.max(a, f.severity | 0), 0); return { name: dt(x), lv: lk(l), attr: ` data-xdim="${esc(x)}"`, tip: `<b>${esc(dt(x))}</b><div class="d">子技能报出的发现，不属于 8 个维度</div>` }; }),
    ];
  }
  const evChip = (f, r) => { const i = (r.dims || []).findIndex((d) => d.dimension === f.dimension); return `<span class="ev"${i >= 0 ? ` data-dim="${i}"` : ` data-xdim="${esc(f.dimension)}"`}>${glyph(f.severity)}${esc(dt(f.dimension))}</span>`; };
  function fLine(f, r) {
    const [m, u] = METRIC[f.metric] || [f.metric, ''], x = R.times(f);
    const ev = !f.sql_id && String(f.evidence || '').length <= 32 ? `（${esc(cn(f.evidence))}）` : '';
    return `${evChip(f, r)} ${esc(cn(m))} <b>${esc(f.value)}${esc(u)}</b>，阈值 ${esc(f.threshold)}${x && x >= 1.05 ? `，是阈值的 <b>${x.toFixed(1)} 倍</b>` : ''}${ev}${f.sql_id ? `，涉及 <span class="ev" data-sql="${esc(f.sql_id)}">${I.code}${esc(f.sql_id)}</span>` : ''}。`;
  }
  function since(cur) {
    if (!cur.prev) return '这是该实例的第一次巡检。';
    const pc = cur.prev.entry.counts || {}, cc = cur.entry.counts || {};
    const bits = [[3, '严重'], [2, '告警'], [1, '关注']].map(([s, n]) => { const d = (+cc[s] || 0) - (+pc[s] || 0); return d > 0 ? `多 <b>${d}</b> 条${n}` : d < 0 ? `少 <b>${-d}</b> 条${n}` : ''; }).filter(Boolean);
    return `与上次巡检（${esc(stamp(cur.prev.entry.at))}，${esc(lname(R.entryLevel(cur.prev.entry)))}）相比，${bits.length ? bits.join('，') : '发现没有变化'}。`;
  }
  function headline(lvl, fs, dims) {
    if (lvl === null) return '一个维度都没采到——没有发现，不等于没有问题';
    if (!fs.length) return `${dims.length} 个维度都在阈值以内`;
    const top = fs[0].severity | 0, names = [...new Set(fs.filter((f) => (f.severity | 0) === top).map((f) => dt(f.dimension)))];
    const rest = fs.filter((f) => (f.severity | 0) < top), restLv = [...new Set(rest.map((f) => f.severity | 0))];
    return R.pangu(`${esc(joinCn(names))}${sevWord(top)}`) + `${rest.length ? `，另有 ${rest.length} 项${restLv.length === 1 ? sevWord(restLv[0]) : '级别较低的发现'}` : ''}`;
  }
  function insightFor(cur, r, lvl, fs) {
    const failed = (r.sub_skills || []).filter((s) => !s.ok), fname = (s) => s.skill.replace('gaussdb-', '');
    const src = `依据 ${stamp(cur.entry.at)} 巡检报告 · 级别由脚本判定`;
    const ask = '问问这次巡检，比如：先处理哪一条？';
    if (lvl === null) {
      const why = [...new Set((r.dims || []).map((d) => cn(d.note || d.headline)))].join('；');
      return { src, ask, head: headline(lvl, fs, r.dims || []),
        lines: [`${(r.dims || []).length} 个维度全部采集失败，原因：<b>${esc(why)}</b>。`, '常见是中间件不通、执行账号没有权限，或实例地址填错。',
          failed.length ? `子技能 ${esc(failed.map(fname).join('、'))} 也都没有执行成功。` : ''],
        chips: [['排查采集为什么失败', `请排查这次健康检查为什么一个维度都没有采到：${why}`], ['检查中间件连通性', '请检查当前实例到中间件的连通性，并说明怎么修复。'], ['重新巡检', '请对当前实例重新做一次完整健康检查。']] };
    }
    const chips = [];
    if (fs[0] && fs[0].sql_id) chips.push([`解释 ${fs[0].sql_id} 为什么慢`, fPrompt(fs[0])]);
    chips.push(['给出处置顺序', '请逐条解释本次健康检查的发现，并给出处置顺序。']);
    if (cur.prev) chips.push(['和上次比变了什么', `请对比本次与上次（${stamp(cur.prev.entry.at)}）的健康检查，说明新增和消失的发现。`]);
    if (failed[0]) chips.push([`${fname(failed[0])} 为什么失败`, `健康检查的子技能 ${failed[0].skill} 执行失败（${failed[0].error}），请分析原因。`]);
    return { src, ask, head: headline(lvl, fs, r.dims || []), chips,
      lines: [fs[0] && fLine(fs[0], r), fs[1] && fLine(fs[1], r), since(cur) + (fs.length > 2 ? ` 其余 ${fs.length - 2} 条见下方发现表。` : ''),
        failed.length ? `子技能 <b>${esc(failed.map(fname).join('、'))}</b> 执行失败（${esc(cn(failed[0].error))}），它的检查项这次没有纳入汇总。` : ''] };
  }

  function hero(cur, r, lvl, fs) {
    const segs = checks(r), cnt = {};
    segs.forEach((s) => { cnt[s.lv] = (cnt[s.lv] || 0) + 1; });
    const legend = [['3', '严重'], ['2', '告警'], ['1', '关注'], ['0', '正常'], ['na', '采集失败']].filter(([k]) => cnt[k]).map(([k, n]) => `<span data-lv="${k}"><i class="sw${k === '0' ? ' calm' : ''}"></i>${n}<b>${cnt[k]}</b></span>`).join('');
    const pc = (cur.prev && cur.prev.entry.counts) || {}, sum = (c) => Object.values(c).reduce((a, b) => a + (+b || 0), 0), dTot = fs.length - sum(pc);
    const subs = r.sub_skills || [], okN = subs.filter((s) => s.ok).length, hi = fs.filter((f) => (f.severity | 0) >= 2), lo = fs.filter((f) => (f.severity | 0) === 1);
    const stats = lvl === null
      ? [{ l: `${glyph(null)}采集失败的维度`, v: R.cu(String(cnt.na || 0)), u: `/ ${(r.dims || []).length}`, s: '一个维度都没有采到' },
         { l: '子技能', v: R.cu(String(okN)), u: `/ ${subs.length} 成功`, s: '全部失败' },
         { l: '越过阈值的发现', v: '—', s: '没采到数据，无法判定' },
         { l: '巡检次数', v: R.cu(String(cur.pos + 1)), u: '次', s: `本次 ${esc(stamp(cur.entry.at))}` }]
      : [{ l: '越过阈值的发现', v: R.cu(String(fs.length)), u: '条', s: cur.prev ? (dTot > 0 ? `<span class="d-up">${I.up} ${dTot}</span> 比上次多` : dTot < 0 ? `<span class="d-dn">${I.dn} ${-dTot}</span> 比上次少` : '与上次持平') : '第一次巡检' },
         { l: `${glyph(2)}告警及以上`, v: R.cu(String(hi.length)), u: '条', s: hi.some((f) => (f.severity | 0) === 3) ? '含严重' : '没有严重' },
         { l: `${glyph(1)}需要关注`, v: R.cu(String(lo.length)), u: '条', s: '需要跟进' },
         { l: '子技能', v: R.cu(String(okN)), u: `/ ${subs.length} 成功`, s: okN < subs.length ? `${esc(subs.filter((s) => !s.ok).map((s) => s.skill.replace('gaussdb-', '')).join('、'))} 失败` : '全部成功' }];
    return `<section class="hero"><div class="hero-grid">
        <div class="glass ringbox rise"${R.rise()}><div class="title">${I.grid}检查项<span class="t3">点圆环看明细</span></div>${R.ring(segs, lvl, `${segs.length} 项检查`)}<div class="ring-legend">${legend}</div></div>
        ${R.insight(insightFor(cur, r, lvl, fs))}
      </div>${R.heroStats(stats)}</section>`;
  }

  function findingsTable(r, fs, lvl) {
    if (!fs.length) return `<div class="empty-state" style="padding:36px">${glyph(lvl === null ? null : 0)}<p style="margin-top:10px">${lvl === null ? '这次什么都没采到，所以也没有发现。' : '没有越过阈值的发现。'}</p></div>`;
    const over = (f) => { const x = R.times(f), thr = R.parseCmp(f.threshold), v = R.parseVal(f.value); if (!x) return `<div class="over"><div class="x">阈值 ${esc(f.threshold)}</div><div class="x t3">组合条件，见证据</div></div>`; const sc = Math.max(v, thr.v) * 1.1; return `<div class="over" data-lv="${lk(f.severity)}"><div class="bar"><i class="grow" style="width:${(v / sc) * 100}%"></i><span class="thr" style="left:${(thr.v / sc) * 100}%"></span></div><div class="x">阈值 ${esc(f.threshold)}，<b>${x.toFixed(1)} 倍</b></div></div>`; };
    return `<div class="scroll"><table class="tbl"><thead><tr><th>级别</th><th>维度</th><th>指标</th><th class="r">当前值</th><th>对比阈值</th><th class="r">操作</th></tr></thead><tbody>${fs.map((f) => {
      const i = (r.dims || []).findIndex((d) => d.dimension === f.dimension);
      return `<tr><td>${status(f.severity)}</td><td><a href="#"${i >= 0 ? ` data-dim="${i}"` : ` data-xdim="${esc(f.dimension)}"`}>${esc(dt(f.dimension))}</a></td><td class="title metric"${tipAttr(`<b>证据</b><div class="d">${esc(cn(f.evidence))}</div>`)}>${esc(cn((METRIC[f.metric] || [f.metric])[0]))}</td><td class="r"><b style="font-weight:500">${esc(f.value)}</b></td><td>${over(f)}</td><td class="ops"><a href="#"${R.ask(fPrompt(f))}>问智能体</a>${f.sql_id ? `<a href="#" data-sql="${esc(f.sql_id)}">SQL</a>` : ''}</td></tr>`;
    }).join('')}</tbody></table></div>`;
  }

  function render() {
    const cur = R.current('health', S.key);
    if (!cur) return R.pageHead('健康检查', '', '', []) + R.emptyHero('还没有健康检查报告', '在会话里做一次巡检，报告会自动出现在这里。', '请对当前实例做一次健康检查。', '做一次健康检查');
    const r = cur.data, lvl = R.repLevel(r), dims = r.dims || [], fs = [...(r.findings || [])].sort((a, b) => (b.severity | 0) - (a.severity | 0));
    const tiles = `<div class="dims">${dims.map((d, i) => { const l = dimLevel(d), k = d.available === false ? null : (KEYFIG[d.dimension] || (() => null))(d), s = k || { v: '—', u: '', cap: d.available === false ? '未采集' : cn(d.headline || '') };
      return `<button class="dim" data-lv="${lk(l)}" data-dim="${i}"${tipAttr(`<b>${esc(dt(d.dimension))}</b><div class="d">${esc(cn(d.headline || ''))}</div>`)}><div class="h"><b>${esc(dt(d.dimension))}</b>${status(l)}</div><div class="v num">${esc(s.v)}<small>${esc(s.u)}</small></div><div class="c">${esc(s.cap)}</div></button>`; }).join('')}</div>`;
    const kb = r.kb_refs && r.kb_refs.status;
    const subs = `<div class="list">${(r.sub_skills || []).map((s) => `<div class="it">${glyph(s.ok ? 0 : null)}<div class="m"><b>${esc(s.skill.replace('gaussdb-', ''))}</b>${s.ok ? '' : `<small>${esc(cn(s.error))}</small>`}</div><span class="r">${s.ok ? '已纳入' : '失败'}</span></div>`).join('')}</div>
      <div class="note">不在巡检里的能力：${esc((r.uncovered_capabilities || []).map((x) => x.replace('gaussdb-', '')).join('、'))}，需要时在会话里单独运行。<br>知识库参照：${kb && kb.attached ? `已接入，命中 ${(r.kb_refs.items || []).length} 条` : '未接入'}。</div>`;
    return R.pageHead('健康检查', R.tag(lvl), R.runPicker('health', '巡检') + `<button class="btn"${dig('请对当前实例重新做一次完整健康检查。')}>${I.redo}重新巡检</button>` + R.primary('请逐条解释本次健康检查的发现，并给出处置顺序。', '在会话里分析'),
      [['实例', esc(r.conn)], ['执行人', esc(D.whoami.user_id)], ['巡检时间', esc(stamp(cur.entry.at))], ['巡检次数', `第 ${cur.pos + 1} 次`]])
      + hero(cur, r, lvl, fs)
      + `<div class="bento">${R.tile({ span: 'b8', title: `需要处理的发现<span class="cnt">${fs.length}</span>`, desc: '按级别从高到低；指标上停留可看证据', body: findingsTable(r, fs, lvl), flush: !!fs.length })}
        ${R.tile({ span: 'b4', title: '巡检节奏', desc: `最近 ${cur.index.length} 次，点格子切换报告`, body: R.rhythm('health', S.key, cur.entry.file) })}
        ${R.tile({ span: 'b9', title: '维度状态', desc: `共 ${dims.length} 个维度，点击查看明细`, body: tiles })}
        ${R.tile({ span: 'b3', title: '子技能执行', desc: '任一失败也照常出报告', body: subs })}</div>`;
  }

  function openDim(i) {
    const r = R.current('health', S.key).data, d = r.dims[i], h = d.headers || [], F = h.map((x, j) => colFmt(d.rows || [], j));
    const table = (d.rows || []).length ? `<div class="scroll boxed"><table class="tbl"><thead><tr>${h.map((x) => `<th>${esc(x)}</th>`).join('')}</tr></thead><tbody>${d.rows.map((row) => `<tr>${row.map((c, j) => `<td class="${/^-?[\d.,]+$/.test(String(c)) ? 'r' : String(c).length > 40 ? 'q' : ''}" title="${esc(c)}">${esc(F[j](c))}</td>`).join('')}</tr>`).join('')}</tbody></table></div>` : `<p class="t3">${esc(cn(d.note || '没有数据行'))}</p>`;
    const fl = (d.findings || []).map((f) => `<div class="alert" data-lv="${lk(f.severity)}">${glyph(f.severity)}<div><b>${esc(cn((METRIC[f.metric] || [f.metric])[0]))}：${esc(f.value)}，阈值 ${esc(f.threshold)}</b><p>${esc(cn(f.evidence))}</p></div></div>`).join('');
    R.drawer(esc(dt(d.dimension)), tag(dimLevel(d)), `<div class="sec">概况</div><p class="t2" style="margin:0">${esc(cn(d.headline || ''))}</p>${fl ? `<div class="sec">发现</div>${fl}` : ''}<div class="sec">明细</div>${table}`,
      `<button class="btn primary"${R.ask(`请深入分析健康检查的「${dt(d.dimension)}」维度：${d.headline}`)}>${I.spark}问智能体</button><button class="btn" data-close>关闭</button>`);
  }
  function openXdim(name) {
    const r = R.current('health', S.key).data, fs = (r.findings || []).filter((f) => f.dimension === name), l = fs.reduce((a, f) => Math.max(a, f.severity | 0), 0);
    R.drawer(esc(dt(name)), tag(l), `<div class="sec">来源</div><p class="t2" style="margin:0">由子技能报出，不属于 8 个维度。</p><div class="sec">发现</div>${fs.map((f) => `<div class="alert" data-lv="${lk(f.severity)}">${glyph(f.severity)}<div><b>${esc(cn(f.metric))}：${esc(f.value)}，阈值 ${esc(f.threshold)}</b><p>${esc(cn(f.evidence))}</p></div></div>`).join('')}`,
      `<button class="btn primary"${R.ask(fs[0] ? fPrompt(fs[0]) : '')}>${I.spark}问智能体</button><button class="btn" data-close>关闭</button>`);
  }

  function assist() {
    const cur = R.current('health', S.key);
    if (!cur) return { ctx: ['健康检查', `实例 ${S.key}`, '还没有报告'], sugg: [['做一次健康检查', '巡检完报告会自动出现在大盘', '请对当前实例做一次健康检查。']] };
    const r = cur.data, fs = [...(r.findings || [])].sort((a, b) => (b.severity | 0) - (a.severity | 0));
    return { ctx: ['健康检查', `实例 ${r.conn}`, `巡检 ${stamp(cur.entry.at)}`, `${fs.length} 条发现`],
      sugg: [['先处理哪一条？', '按级别和影响面给出处置顺序', '请逐条解释本次健康检查的发现，并给出处置顺序。'],
        ...fs.slice(0, 2).map((f) => [`${dt(f.dimension)}：${(METRIC[f.metric] || [f.metric])[0]} ${f.value}`, `阈值 ${f.threshold}，问问原因和怎么改`, fPrompt(f)]),
        ['和上次巡检比变了什么？', '新增、消失和恶化的发现', '请对比本次与上次健康检查，说明新增和消失的发现。']] };
  }
  function commands() {
    const cur = R.current('health', S.key); if (!cur) return [];
    const r = cur.data;
    return [...(r.dims || []).map((d, i) => ({ grp: '维度', label: dt(d.dimension), sub: cn(d.headline || ''), ic: 'grid', go: () => { location.hash = '#/health'; setTimeout(() => openDim(i), 0); } })),
      ...(r.findings || []).map((f) => ({ grp: '发现', label: `${dt(f.dimension)} · ${cn((METRIC[f.metric] || [f.metric])[0])} ${f.value}`, sub: `${lname(f.severity)}，阈值 ${f.threshold}`, ic: 'pulse', lv: f.severity, go: () => { location.hash = '#/health'; const i = (r.dims || []).findIndex((d) => d.dimension === f.dimension); setTimeout(() => (i >= 0 ? openDim(i) : openXdim(f.dimension)), 0); } }))];
  }
  R.pages.health = { render, assist, commands, openDim, openXdim };
})(window.R6);
