/* R9 四类任务的线程:任务卡(做了哪几步)→ 智能体回答(要点带来源)→ 内嵌的图表卡 → 接着问 → 追加的问答。
   文字沿用 R6 的确定性生成,图形沿用 viz.js;这里只决定摆放。右侧证据栏的默认内容也在这里。 */
(function (R) {
  'use strict';
  const { D, S, esc, I, lk, lname, glyph, tag, dig, fmtInt, fmtMs, stamp, cn, colFmt } = R;
  const ws = R.ws, H = R.pages.health, T = R.pages.topsql, W = R.pages.wdr, K = R.pages.kb;
  const sortF = (fs) => [...(fs || [])].sort((a, b) => (b.severity | 0) - (a.severity | 0));
  const btn = (attr, icon, label, cls = '') => `<button class="btn sm ${cls}"${attr}>${icon || ''}${esc(label)}</button>`;

  /** 发现卡:级别、当前值对阈值、证据,以及「让智能体分析」等动作。 */
  function fcard(f, open, prompt) {
    const x = R.times(f), thr = R.parseCmp(f.threshold), v = R.parseVal(f.value), sc = x ? Math.max(v, thr.v) * 1.1 : 1;
    const m = (H.METRIC[f.metric] || [f.metric])[0], name = W.WT[f.dimension] || R.dt(f.dimension);
    return `<div class="fc" data-lv="${lk(f.severity)}"><div class="fc-h">${tag(f.severity)}<b>${esc(name)}</b><span class="t3">${esc(cn(m))}</span></div>
      <div class="fc-v"><span class="num">${esc(f.value)}</span><small>阈值 ${esc(f.threshold)}${x ? `，${x.toFixed(1)} 倍` : '，组合条件'}</small></div>
      ${x ? `<div class="over" data-lv="${lk(f.severity)}"><div class="bar"><i class="grow" style="width:${(v / sc) * 100}%"></i><span class="thr" style="left:${(thr.v / sc) * 100}%"></span></div></div>` : ''}
      <div class="fc-e" title="${esc(cn(f.evidence))}">${esc(cn(f.evidence))}</div>
      <div class="fc-a">${btn(R.ask(prompt), I.spark, '让智能体分析')}${btn(open, '', '看证据', 'ghost')}${f.sql_id ? btn(` data-sql="${esc(f.sql_id)}"`, I.code, f.sql_id, 'ghost') : ''}</div></div>`;
  }

  /* ------------------------------------------------------------ 健康巡检 */
  const health = {
    head() {
      const cur = R.current('health', S.key);
      if (!cur) return { title: `${S.key} · 健康巡检`, meta: '还没有报告', ctx: [`@${S.key}`] };
      const r = cur.data;
      return { title: `${r.conn} · 健康巡检`, tag: tag(R.repLevel(r)), meta: `${stamp(cur.entry.at)} · 第 ${cur.pos + 1} 次 · ${D.whoami.user_id} 发起`, ctx: [`@${r.conn}`, `健康巡检 ${stamp(cur.entry.at)}`],
        actions: `<button class="btn"${dig('请对当前实例重新做一次完整健康检查。')}>${I.redo}重新巡检</button><button class="btn"${dig('打开本人的 opencode 会话')}>${I.ext}在 opencode 中打开</button>` };
    },
    body() {
      const cur = R.current('health', S.key);
      if (!cur) return ws.empty('这个实例还没有健康检查报告', '要我现在做一次巡检吗？巡检完，报告会作为新任务出现在左侧。', [['现在做一次健康检查', '请对当前实例做一次健康检查。']]);
      const r = cur.data, lvl = R.repLevel(r), fs = sortF(r.findings), dims = r.dims || [], subs = r.sub_skills || [], kb = r.kb_refs && r.kb_refs.status;
      const okN = dims.filter((d) => d.available !== false).length, why = cn((dims.find((d) => d.available === false) || {}).note || '');
      const steps = [
        [okN ? 'ok' : 'fail', `连接实例 ${r.conn}`, okN ? '经中间件取数' : why],
        [okN === dims.length ? 'ok' : okN ? 'warn' : 'fail', `采集 ${dims.length} 个维度`, okN === dims.length ? '全部采到' : `${okN} 个采到，${dims.length - okN} 个失败`],
        ...subs.map((s) => [s.ok ? 'ok' : 'fail', `运行子技能 ${s.skill.replace('gaussdb-', '')}`, s.ok ? '已纳入汇总' : cn(s.error)]),
        [lvl === null ? 'skip' : 'ok', '按阈值判级', lvl === null ? '没有采到数据，无法判级' : `脚本判定为「${lname(lvl)}」，模型不改`],
        [kb && kb.attached ? 'ok' : 'skip', '检索知识库', kb && kb.attached ? `命中 ${(r.kb_refs.items || []).length} 条` : '未接入，跳过'],
      ];
      const ins = H.insightFor(cur, r, lvl, fs), segs = H.checks(r);
      const order = { 3: 0, 2: 1, 1: 2, na: 3, 0: 4 };
      const rows = [...segs].sort((a, b) => order[a.lv] - order[b.lv]).map((s) => `<button class="chk-row" data-lv="${s.lv}"${s.attr}>${glyph(s.lv === 'na' ? null : +s.lv)}<b>${esc(s.name)}</b><span class="s">${esc(lname(s.lv === 'na' ? null : +s.lv))}</span></button>`).join('');
      const dimOpen = (f) => { const i = dims.findIndex((d) => d.dimension === f.dimension); return i >= 0 ? ` data-dim="${i}"` : ` data-xdim="${esc(f.dimension)}"`; };
      return ws.task(I.pulse, '定时健康巡检', `${stamp(cur.entry.at)} · 实例 ${r.conn}`, steps)
        + ws.agent(ins.head, ws.lines(ins.lines))
        + ws.art({ ic: 'grid', title: '检查项', sub: `${segs.length} 项 · 点一项在右侧看明细`, body: `<div class="chk">${R.ring(segs, lvl, `${segs.length} 项检查`)}<div class="chk-list">${rows}</div></div>` })
        + (fs.length ? ws.art({ ic: 'pulse', title: `越线发现 ${fs.length} 条`, sub: '按级别从高到低', body: `<div class="fcs">${fs.map((f) => fcard(f, dimOpen(f), H.fPrompt(f))).join('')}</div>` }) : '')
        + ws.art({ ic: 'clock', title: '巡检节奏', sub: `最近 ${cur.index.length} 次，点格子切换到那次任务`, body: R.rhythm('health', S.key, cur.entry.file, 680) })
        + ws.follow(ins.chips) + ws.chat();
    },
    pane() {
      const cur = R.current('health', S.key); if (!cur) return '<p class="t3">还没有证据。</p>';
      const r = cur.data;
      const dimRows = (r.dims || []).map((d, i) => { const k = d.available === false ? null : (H.KEYFIG[d.dimension] || (() => null))(d);
        return `<button class="pv-row" data-dim="${i}">${glyph(H.dimLevel(d))}<b>${esc(R.dt(d.dimension))}</b><span class="v num">${k ? esc(`${k.v} ${k.u}`) : esc(d.available === false ? '未采集' : '')}</span></button>`; }).join('');
      return `<div class="pv-sec">报告 · ${esc(stamp(cur.entry.at))}</div>${dimRows}
        <div class="pv-sec">子技能</div>${(r.sub_skills || []).map((s) => `<div class="pv-row static">${glyph(s.ok ? 0 : null)}<b>${esc(s.skill.replace('gaussdb-', ''))}</b><span class="v">${s.ok ? '已纳入' : esc(cn(s.error))}</span></div>`).join('')}
        <div class="pv-sec">不在巡检里的能力</div><p class="pv-p">${esc((r.uncovered_capabilities || []).map((x) => x.replace('gaussdb-', '')).join('、'))}，需要时在会话里单独运行。</p>`;
    },
  };

  /* ------------------------------------------------------------ Top SQL */
  const topsql = {
    head() {
      const t = T.data();
      if (!t) return { title: `${S.key} · Top SQL`, meta: '还没有报告', ctx: [`@${S.key}`] };
      const label = T.BY.find((b) => b[0] === t.by)[1];
      return { title: `${t.rep.conn} · Top SQL`, meta: `${stamp(t.e.at)} · 按${label}排序 · 累计统计视图`, ctx: [`@${t.rep.conn}`, `Top SQL ${stamp(t.e.at)}`],
        actions: `<button class="btn"${dig('请重新取一次当前实例的 Top SQL。')}>${I.redo}重新取数</button>` };
    },
    body() {
      const t = T.data();
      if (!t) return ws.empty('这个实例还没有 Top SQL 报告', '要我现在取一次吗？默认按总耗时取前 10 条。', [['现在取一次 Top SQL', '请列出当前实例总耗时最高的 SQL。']]);
      const rows = t.rep.rows || [], sys = rows.filter((r) => T.isSys(t, r)).length, tuned = rows.filter((r) => t.tune[r.sql_id] && !t.tune[r.sql_id].skipped).length;
      const label = T.BY.find((b) => b[0] === t.by)[1], sum = rows.reduce((a, r) => a + (+r.total_sec || 0), 0) || 1;
      const steps = [['ok', '读取累计统计视图', '自统计重置以来，不是时间窗口'], ['ok', `按${label}取前 ${t.rep.limit} 条`, `上榜 ${rows.length} 条`],
        ['ok', '对照调优记录', `${tuned} 条已调优，${sys} 条系统 SQL，${rows.length - tuned - sys} 条未调优`]];
      const seg = `<div class="seg">${T.BY.map(([b, l]) => `<button data-by="${b}" class="${b === t.by ? 'on' : ''}" ${t.per[b] ? '' : 'disabled'}>${esc(l)}</button>`).join('')}</div>`;
      const tg = (r) => { const x = t.tune[r.sql_id]; return x && x.skipped ? '<span class="tag gray">系统</span>' : x ? '<span class="tag blue">已调优</span>' : '<span class="t3">未调优</span>'; };
      const table = `<div class="scroll"><table class="tbl"><thead><tr><th>#</th><th>sql_id</th><th>语句</th><th class="r">调用</th><th class="r">平均</th><th>占上榜耗时</th><th>调优</th></tr></thead><tbody>${rows.map((r, i) => { const p = (((+r.total_sec || 0) / sum) * 100).toFixed(1);
        return `<tr><td class="t3">${i + 1}</td><td class="id"><a href="#" data-sql="${esc(r.sql_id)}">${esc(r.sql_id)}</a></td><td class="q" title="${esc(r.query)}">${esc(r.query)}</td><td class="r">${fmtInt(r.calls)}</td><td class="r">${fmtMs(r.avg_ms)}</td><td><div class="share"><div class="b"><i style="width:${p}%"></i></div><b>${p}%</b></div></td><td>${tg(r)}</td></tr>`; }).join('')}</tbody></table></div>`;
      const ins = T.insightFor(t);
      return ws.task(I.list, '取 Top SQL', `${stamp(t.e.at)} · 实例 ${t.rep.conn}`, steps) + ws.agent(ins.head, ws.lines(ins.lines))
        + ws.art({ ic: 'grid', title: '上榜耗时构成', sub: '面积 = 总耗时占比 · 斜纹为系统 SQL', body: R.treemap(rows, t.tune) })
        + ws.art({ ic: 'list', title: '榜单', sub: `按${label}排序`, tools: seg, body: table, flush: true })
        + ws.art({ ic: 'code', title: '调用次数 × 平均耗时', sub: '右上角是又多又慢的', body: R.scatter(rows, t.tune, 680) })
        + ws.follow(ins.chips) + ws.chat();
    },
    pane() {
      const t = T.data(); if (!t) return '<p class="t3">还没有证据。</p>';
      return `<div class="pv-sec">上榜语句，点开看语句、执行统计与调优结论</div>${(t.rep.rows || []).map((r) => { const x = t.tune[r.sql_id];
        return `<button class="pv-row" data-sql="${esc(r.sql_id)}">${x && x.skipped ? glyph(null) : x ? glyph(0) : '<span class="glyph pending"></span>'}<b>${esc(r.sql_id)}</b><span class="v num">${esc(fmtMs(r.avg_ms))} · ${esc(fmtInt(r.calls))} 次</span></button>`; }).join('')}
        <div class="pv-sec">口径</div><p class="pv-p">累计统计视图（自统计重置以来），含系统与监控 SQL；要看某段时间的负载，用 WDR 分析。</p>`;
    },
  };

  /* ------------------------------------------------------------ WDR */
  const wdr = {
    head() {
      const cur = R.current('wdr', S.key);
      if (!cur) return { title: `${S.key} · WDR 分析`, meta: '还没有报告', ctx: [`@${S.key}`] };
      const d = cur.data, w = d.window || {};
      return { title: `${d.conn} · WDR 分析`, tag: tag(d.overall | 0), meta: `snap ${w.begin_id}–${w.end_id} · ${w.duration_min} 分钟 · ${w.scope || ''} ${w.node || ''}`, ctx: [`@${d.conn}`, `WDR snap ${w.begin_id}–${w.end_id}`],
        actions: (d.native || {}).generated ? `<button class="btn" data-toast="原型：真实环境会下载 openGauss 原生 WDR 报告（HTML）">${I.down}原生报告</button>` : '' };
    },
    body() {
      const cur = R.current('wdr', S.key);
      if (!cur) return ws.empty('这个实例还没有 WDR 报告', '要我列出最近的快照，并对最近一个窗口做分析吗？', [['分析最近一个窗口', '请列出最近的 WDR 快照，并对最近一个窗口做分析。']]);
      const d = cur.data, w = d.window || {}, p = cur.prev && cur.prev.data, native = d.native || {}, fs = sortF(d.findings);
      const steps = [['ok', `读取快照 ${w.begin_id} → ${w.end_id}`, `${w.duration_min} 分钟，${w.scope || ''} ${w.node || ''}`],
        [p ? 'ok' : 'skip', '与上一窗口对比', p ? `snap ${p.window.begin_id} → ${p.window.end_id}` : '这是第一份报告'],
        ['ok', '按阈值判级', `脚本判定为「${lname(d.overall | 0)}」`],
        [native.generated ? 'ok' : 'skip', '生成原生 WDR 报告', native.generated ? `${fmtInt(Math.round((native.bytes || 0) / 1024))} KB，可下载` : '未生成']];
      const dbt = W.kpiVal(W.KPIS[0], d), pdb = W.kpiVal(W.KPIS[0], p), cpu = W.wval(d, 'loadprofile', 'CPU占DBtime'), waits = W.wdim(d, 'waits'), ch = dbt !== null && pdb !== null ? R.change(W.KPIS[0], dbt, pdb) : null;
      const dbBox = `<div class="dbt2"><div><div class="big num">${dbt === null ? '—' : R.cu(fmtInt(Math.round(dbt)))}<small>秒 DB Time</small></div>
          <div class="sub">平均 <b>${w.duration_min && dbt !== null ? (dbt / (w.duration_min * 60)).toFixed(1) : '—'}</b> 个活跃会话${ch ? `<span class="chg ${ch.cls}">${ch.dir > 0 ? I.up : ch.dir < 0 ? I.dn : ''}${esc(ch.text)}</span><span class="t3">上一窗口 ${esc(fmtInt(Math.round(pdb)))} 秒</span>` : ''}</div>
          ${cpu === null ? '' : `<div class="stackbar"><i style="flex:${cpu};background:var(--sb-cpu)"></i><i style="flex:${100 - cpu};background:var(--sb-wait)"></i></div><div class="legend"><span><i class="sw" style="--c:var(--sb-cpu)"></i>CPU ${cpu.toFixed(1)}%</span><span><i class="sw" style="--c:var(--sb-wait)"></i>等待 ${(100 - cpu).toFixed(1)}%</span></div>`}</div>
        <div><div class="subt">等待按等待类拆开</div>${W.hb(waits, waits ? (waits.headers || []).length - 1 : 0, '%', true)}</div></div>`;
      const ts = W.wdim(d, 'topsql'), tsh = (ts && ts.headers) || [], TF = tsh.map((x, i) => colFmt((ts && ts.rows) || [], i));
      const tsTable = ts && (ts.rows || []).length ? `<div class="scroll"><table class="tbl"><thead><tr>${tsh.map((h, i) => `<th class="${i && i < tsh.length - 1 ? 'r' : ''}">${esc(h)}</th>`).join('')}</tr></thead><tbody>${ts.rows.map((r) => `<tr>${r.map((c, i) => (i === 0 ? `<td class="id">${esc(c)}</td>` : i === r.length - 1 ? `<td class="q" title="${esc(c)}">${esc(c)}</td>` : `<td class="r">${esc(TF[i](c))}</td>`)).join('')}</tr>`).join('')}</tbody></table></div>` : '<p class="t3" style="padding:16px">未采集</p>';
      const ins = W.insightFor(cur, d, p, fs);
      const prompt = (f) => `请针对 WDR 发现 ${f.code}（${f.metric}=${f.value}）做深入分析：${f.evidence}`;
      return ws.task(I.clock, 'WDR 窗口分析', `${stamp(cur.entry.at)} · 实例 ${d.conn}`, steps) + ws.agent(ins.head, ws.lines(ins.lines))
        + ws.art({ ic: 'clock', title: 'DB Time 与构成', sub: `${w.duration_min} 分钟窗口`, body: dbBox })
        + ws.art({ ic: 'pulse', title: '负载对比', sub: '空心点是上一窗口，实心点是本窗口；比例尺从 0 开始', body: `<div class="dumbbells">${W.KPIS.map((k) => R.dumbbell(k, W.kpiVal(k, d), W.kpiVal(k, p), W.showK)).join('')}</div>` })
        + (fs.length ? ws.art({ ic: 'pulse', title: `越线发现 ${fs.length} 条`, sub: '脚本按阈值判定', body: `<div class="fcs">${fs.map((f) => fcard(f, ` data-wdim="${esc(f.dimension)}"`, prompt(f))).join('')}</div>` }) : '')
        + ws.art({ ic: 'list', title: '窗口内 Top SQL', sub: esc(cn((ts && ts.headline) || '')), body: tsTable, flush: true })
        + ws.follow(ins.chips) + ws.chat();
    },
    pane() {
      const cur = R.current('wdr', S.key); if (!cur) return '<p class="t3">还没有证据。</p>';
      const d = cur.data, w = d.window || {}, p = cur.prev && cur.prev.data;
      return `<div class="pv-sec">窗口</div><div class="pv-kv"><span>快照</span><b>snap ${esc(w.begin_id)} → ${esc(w.end_id)}</b><span>时间</span><b>${esc(String(w.begin_ts || '').slice(5, 16))} 至 ${esc(String(w.end_ts || '').slice(11, 16))}</b><span>时长</span><b>${esc(w.duration_min)} 分钟</b><span>对比</span><b>${p ? `snap ${esc(p.window.begin_id)} → ${esc(p.window.end_id)}` : '没有'}</b></div>
        <div class="pv-sec">报告里的 ${(d.dims || []).length} 个部分</div>${(d.dims || []).map((x) => { const l = (d.findings || []).filter((f) => f.dimension === x.dimension).reduce((a, f) => Math.max(a, f.severity | 0), 0);
          return `<button class="pv-row" data-wdim="${esc(x.dimension)}">${glyph(l)}<b>${esc(W.WT[x.dimension] || x.dimension)}</b><span class="v">${esc(cn(x.headline || ''))}</span></button>`; }).join('')}`;
    },
  };

  /* ------------------------------------------------------------ 知识库 */
  const kb = {
    head() {
      const m = K.model();
      return { title: '知识库', tag: m.issues ? tag(1, `${m.issues} 项待处理`) : tag(0, '健康'), meta: `版本 ${m.st.version} · ${m.st.mode} · 本环境只读`, ctx: ['@知识库', `版本 ${m.st.version}`],
        actions: `<button class="btn"${R.ask('请在知识库里检索：')}>${I.search}检索知识库</button>` };
    },
    body() {
      const m = K.model(), c = m.c, total = (+c['docs.case'] || 0) + (+c['docs.rule'] || 0), warns = (m.h.file_warnings || []).length;
      const steps = [['ok', '读取知识库状态', `版本 ${m.st.version}，${m.st.mode}`], ['ok', '统计条目', `案例 ${fmtInt(c['docs.case'])}、条款 ${fmtInt(c['docs.rule'])}，整理自 ${fmtInt(c['docs.raw'])} 份工单`],
        [warns ? 'warn' : 'ok', '校验文件', warns ? `${warns} 个文件格式有问题` : '全部通过'], [m.cov !== null && m.cov < 95 ? 'warn' : 'ok', '检查向量覆盖', `覆盖 ${m.cov ?? '—'}%`],
        ['ok', '统计缺口', `${m.misses.length} 类诊断发现查不到案例`]];
      const ins = K.insightFor(m), mx = Math.max(1, ...m.misses.map((x) => x.n));
      const comp = `<div class="kb2"><div><div class="big num">${R.cu(fmtInt(total))}<small>条知识</small></div><div class="kbsplit"><span>案例 <b>${fmtInt(c['docs.case'])}</b></span><span>条款 <b>${fmtInt(c['docs.rule'])}</b></span><span class="t3">整理自 ${fmtInt(c['docs.raw'])} 份工单</span></div></div><div class="kbcov">${R.covring(m.cov, '向量覆盖')}</div></div>
        <div class="mg-h"><span>关系图 · ${m.cat.edges.length} 条已确认的边</span></div>${R.minigraph(m.cat, m.rules)}`;
      const gaps = m.misses.length ? `<div class="hb">${m.misses.map((x) => `<div class="r"><div class="l">${esc(x.code)}</div><div class="b"><i class="grow" style="width:${(x.n / mx) * 100}%"></i></div><div class="v">${x.n}<small>次</small></div></div>`).join('')}</div>` : '<p class="t3">没有缺口</p>';
      const cases = `<div class="list">${m.cases.slice(0, 5).map((x) => `<div class="it"><span class="tag line">${esc(x.severity)}</span><div class="m"><a href="#" data-case="${esc(x.id)}"><b>${esc(x.title)}</b></a><small>${esc(x.system)} · ${esc(x.occurred_at)} · ${esc(cn(x.primary_factor))}</small></div></div>`).join('')}</div>`;
      return ws.task(I.book, '知识库自检', `版本 ${m.st.version} · 全体共享`, steps) + ws.agent(ins.head, ws.lines(ins.lines), '依据知识库自检生成')
        + ws.art({ ic: 'book', title: '知识构成', body: comp })
        + ws.art({ ic: 'pulse', title: '缺口', sub: '诊断发现在库里查不到案例的次数', body: gaps })
        + ws.art({ ic: 'list', title: '最近的案例', sub: '点开在右侧看现场、判断、处置与复发标志', body: cases })
        + ws.follow(ins.chips) + ws.chat();
    },
    pane() {
      const m = K.model();
      return `<div class="pv-sec">全部案例</div>${m.cases.map((x) => `<button class="pv-row" data-case="${esc(x.id)}"><span class="tag line">${esc(x.severity)}</span><b>${esc(x.title)}</b></button>`).join('')}
        <div class="pv-sec">条款</div>${m.rules.map((r) => `<div class="pv-row static">${r.severity === 'error' ? '<span class="tag" data-lv="2">必须</span>' : '<span class="tag gray">建议</span>'}<b>${esc(r.id)}</b><span class="v">${esc(cn(r.rule))}</span></div>`).join('')}
        <div class="pv-sec">最近检索</div>${D.kb.queries.map((q) => `<div class="pv-row static"><span class="tag ${q.how === 'semantic' ? 'blue' : 'gray'}">${q.how === 'semantic' ? '语义' : '关键词'}</span><b>${esc(q.q)}</b><span class="v num">${q.hits_cases} / ${q.hits_rules}</span></div>`).join('')}`;
    },
  };

  Object.assign(ws.threads, { health, topsql, wdr, kb });
  Object.keys(ws.threads).forEach((k) => { R.pages[k].render = ws.threads[k].body; });
})(window.R6);
