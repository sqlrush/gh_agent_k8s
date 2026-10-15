/* R10 内页:A2 要闻·健康检查、B1 慢语句·Top SQL、C1 负载·WDR、D1 知识·知识库。
   每版一条版头,正文分栏;表格用报纸排法(粗线起止、细线分行)。 */
(function (R) {
  'use strict';
  const { D, S, esc, lk, status, fmtInt, fmtMs, fmtSec, stamp, cn, colFmt } = R;
  const H = R.pages.health, T = R.pages.topsql, W = R.pages.wdr, K = R.pages.kb, P = R.press, pp = R.paper;
  const sheet = (id, pg, title, sub, body, tools = '') => `<section class="sheet" id="${id}"><div class="sheet-h"><span class="pg">${pg}</span><b>${esc(title)}</b><span class="sub">${sub}</span><span class="tools">${tools}</span></div>${body}</section>`;
  const none = (text, prompt, label) => `<div class="sheet-empty"><p>${esc(text)}</p>${R.primary(prompt, label)}</div>`;
  const sum = (c) => Object.values(c || {}).reduce((a, b) => a + (+b || 0), 0);

  /* ------------------------------------------------------------ A2 要闻 · 健康检查 */
  function secHealth() {
    const cur = R.current('health', S.key);
    if (!cur) return sheet('sec-health', 'A2', '要闻 · 健康检查', '', none('本期没有巡检报告。', '请对当前实例做一次健康检查。', '做一次健康检查'));
    const r = cur.data, lvl = R.repLevel(r), fs = pp.sortF(r.findings), dims = r.dims || [];
    const open = (f) => { const i = dims.findIndex((d) => d.dimension === f.dimension); return i >= 0 ? ` data-dim="${i}"` : ` data-xdim="${esc(f.dimension)}"`; };
    const table = fs.length ? `<table class="ptable"><thead><tr><th>级别</th><th>维度</th><th>指标</th><th class="r">读数</th><th class="r">阈值</th><th class="r">倍数</th><th>证据</th></tr></thead><tbody>${fs.map((f) => { const x = R.times(f);
      return `<tr${open(f)}><td>${status(f.severity)}</td><td><b>${esc(R.dt(f.dimension))}</b></td><td class="mt" title="${esc(cn(f.metric))}">${esc(cn((H.METRIC[f.metric] || [f.metric])[0]))}</td><td class="r num"><b>${esc(f.value)}</b></td><td class="r num t2">${esc(f.threshold)}</td><td class="r num">${x ? `${x.toFixed(1)}×` : '—'}</td><td class="ev2" title="${esc(cn(f.evidence))}">${esc(cn(f.evidence))}</td></tr>`; }).join('')}</tbody></table>`
      : `<p class="t2">${lvl === null ? '这次什么都没采到，所以也没有发现。' : '本期没有越线发现。'}</p>`;
    const smalls = `<div class="smalls">${dims.map((d, i) => { const l = H.dimLevel(d), k = d.available === false ? null : (H.KEYFIG[d.dimension] || (() => null))(d);
      return `<button class="sm" data-lv="${lk(l)}" data-dim="${i}"><span class="n">${esc(R.dt(d.dimension))}</span><span class="v num">${k ? esc(k.v) : '—'}<small>${k ? esc(k.u) : ''}</small></span><span class="c">${esc(k ? k.cap : cn(d.headline || '未采集'))}</span>${status(l)}</button>`; }).join('')}</div>`;
    const subs = r.sub_skills || [], okS = subs.filter((s) => s.ok).map((s) => s.skill.replace('gaussdb-', '')), bad = subs.filter((s) => !s.ok);
    const note = `<p>本期巡检采集 ${dims.length} 个维度，采到 ${dims.filter((d) => d.available !== false).length} 个。${okS.length ? `子技能 ${esc(okS.join('、'))} 执行成功，已纳入汇总` : '子技能都没有执行成功'}${bad.length ? `；${esc(bad.map((s) => `${s.skill.replace('gaussdb-', '')}（${cn(s.error)}）`).join('、'))}未纳入` : ''}。级别由巡检脚本按阈值判定，智能体只负责整理与解释。</p>
      <p>不在巡检里的能力：${esc((r.uncovered_capabilities || []).map((x) => x.replace('gaussdb-', '')).join('、'))}，需要时在会话里单独运行。</p>`;
    const past = `<ol class="past">${[...cur.index].reverse().map((e) => { const l = R.entryLevel(e), on = e.file === cur.entry.file;
      return `<li class="${e.demo ? 'demo' : ''}${on ? ' on' : ''}"${e.demo ? '' : ` data-run="health|${esc(e.file)}"`}><span class="d num">${esc(stamp(e.at))}</span>${status(l)}<span class="n num">${sum(e.counts)} 条${e.demo ? '<em>示意</em>' : ''}</span></li>`; }).join('')}</ol>`;
    return sheet('sec-health', 'A2', '要闻 · 健康检查', `${esc(r.conn)} · ${esc(stamp(cur.entry.at))} 巡检全文`,
      `<div class="sheet-b"><div class="main"><h3 class="sh3">全部越线发现　<span>${fs.length} 条，按级别从高到低；点一行看明细</span></h3>${table}
        <h3 class="sh3">八个维度　<span>每格是该维度的关键读数</span></h3>${smalls}</div>
        <aside class="rcol"><div class="box"><h4>编者按：本期过程</h4>${note}</div><div class="box"><h4>往期巡检</h4>${past}</div></aside></div>`);
  }

  /* ------------------------------------------------------------ B1 慢语句 · Top SQL */
  function secTop() {
    const t = T.data();
    if (!t) return sheet('sec-topsql', 'B1', '慢语句 · Top SQL', '', none('本期没有 Top SQL 榜单。', '请列出当前实例总耗时最高的 SQL。', '取一次 Top SQL'));
    const rows = t.rep.rows || [], ins = T.insightFor(t), b = pp.bizTop(t), label = T.BY.find((x) => x[0] === t.by)[1];
    const by = T.BY.filter(([k]) => t.per[k]).map(([k, l]) => `<a href="#" data-by="${k}" class="${k === t.by ? 'on' : ''}">按${esc(l)}</a>`).join('');
    const tg = (r) => { const x = t.tune[r.sql_id]; return x && x.skipped ? '系统 SQL' : x ? '已调优' : '未调优'; };
    const table = `<table class="ptable"><thead><tr><th>#</th><th>sql_id</th><th>语句</th><th class="r">调用</th><th class="r">总耗时</th><th class="r">平均</th><th class="r">返回行</th><th>调优</th></tr></thead><tbody>${rows.map((r, i) => `<tr data-sql="${esc(r.sql_id)}"><td class="t3 num">${i + 1}</td><td><b>${esc(r.sql_id)}</b></td><td class="q mono" title="${esc(r.query)}">${esc(r.query)}</td><td class="r num">${fmtInt(r.calls)}</td><td class="r num">${fmtSec(r.total_sec)}</td><td class="r num">${fmtMs(r.avg_ms)}</td><td class="r num">${fmtInt(r.rows)}</td><td>${tg(r)}</td></tr>`).join('')}</tbody></table>`;
    const notes = `<ol class="notes">${rows.map((r) => { const x = t.tune[r.sql_id], f = x && x.evidence && (x.evidence.findings || [])[0];
      return `<li data-sql="${esc(r.sql_id)}"><b>${esc(r.sql_id)}</b>　${x && x.skipped ? `系统 SQL，只引用 ${esc((x.system_objects || []).join('、'))}，按策略不调优。` : f ? `${esc(cn(f.detail))}。建议：${esc(cn(f.advice))}。` : '还没有调优过。'}</li>`; }).join('')}</ol>`;
    return sheet('sec-topsql', 'B1', '慢语句 · Top SQL', `${esc(t.rep.conn)} · ${esc(stamp(t.e.at))} 取数 · 累计统计视图，按${esc(label)}排序`,
      `<div class="sheet-b"><div class="main"><h3 class="hl3">${ins.head}</h3><div class="body cols2">${pp.paras(ins.lines)}</div>
        <figure class="fig">${P.shares(rows, t.tune, b && b.sql_id)}<figcaption>图　上榜语句占上榜总耗时的比例。斜纹为系统 SQL；红色为业务语句里耗时最高的一条。点一行看语句与调优结论。</figcaption></figure></div>
        <aside class="rcol"><div class="box"><h4>调优札记</h4>${notes}</div></aside></div>
        <h3 class="sh3">完整榜单　<span>按${esc(label)}排序</span></h3>${table}`, `<span class="byx">${by}</span>`);
  }

  /* ------------------------------------------------------------ C1 负载 · WDR */
  function secWdr() {
    const cur = R.current('wdr', S.key);
    if (!cur) return sheet('sec-wdr', 'C1', '负载 · WDR 分析', '', none('本期没有 WDR 窗口分析。', '请列出最近的 WDR 快照，并对最近一个窗口做分析。', '做一次 WDR 分析'));
    const d = cur.data, w = d.window || {}, p = cur.prev && cur.prev.data, fs = pp.sortF(d.findings), ins = W.insightFor(cur, d, p, fs);
    const dbt = W.kpiVal(W.KPIS[0], d), waits = W.wdim(d, 'waits'), cpu = W.wval(d, 'loadprofile', 'CPU占DBtime');
    const wItems = waits ? (waits.rows || []).map((r) => [r[0], num2(r[r.length - 1]), '%', `${fmtInt(+r[1])} 次等待`]) : [];
    const ts = W.wdim(d, 'topsql'), tsh = (ts && ts.headers) || [], TF = tsh.map((x, i) => colFmt((ts && ts.rows) || [], i));
    const tsTable = ts && (ts.rows || []).length ? `<table class="ptable"><thead><tr>${tsh.map((h, i) => `<th class="${i && i < tsh.length - 1 ? 'r' : ''}">${esc(h)}</th>`).join('')}</tr></thead><tbody>${ts.rows.map((r) => `<tr>${r.map((c, i) => (i === 0 ? `<td><b>${esc(c)}</b></td>` : i === r.length - 1 ? `<td class="q mono" title="${esc(c)}">${esc(c)}</td>` : `<td class="r num">${esc(TF[i](c))}</td>`)).join('')}</tr>`).join('')}</tbody></table>` : '<p class="t3">未采集</p>';
    const fl = fs.map((f) => `<li data-wdim="${esc(f.dimension)}">${status(f.severity)}<b>${esc(cn(f.metric))}：${esc(f.value)}</b>　阈值 ${esc(f.threshold)}。${esc(cn(f.evidence))}</li>`).join('');
    return sheet('sec-wdr', 'C1', '负载 · WDR 分析', `${esc(d.conn)} · snap ${esc(w.begin_id)}–${esc(w.end_id)}，${esc(w.duration_min)} 分钟，${esc(w.scope || '')} ${esc(w.node || '')}${p ? ` · 对比 snap ${esc(p.window.begin_id)}–${esc(p.window.end_id)}` : ''}`,
      `<div class="sheet-b"><div class="main"><h3 class="hl3">${ins.head}</h3><div class="body cols2">${pp.paras(ins.lines)}</div>
        <h3 class="sh3">八项负载指标前后对照　<span>墨线从上一窗口画到本窗口；比例尺从 0 开始</span></h3>${P.pairs(W.KPIS, d, p, W.kpiVal, W.showK)}</div>
        <aside class="rcol"><div class="box"><h4>DB Time</h4><div class="numb big"><span class="v">${dbt === null ? '—' : esc(fmtInt(Math.round(dbt)))}</span><span class="l">秒，平均 ${w.duration_min && dbt !== null ? (dbt / (w.duration_min * 60)).toFixed(1) : '—'} 个活跃会话</span><span class="s">${cpu === null ? '' : `CPU ${cpu.toFixed(0)}%，等待 ${(100 - cpu).toFixed(0)}%`}</span></div></div>
          <div class="box"><h4>等待构成</h4>${P.bars(wItems, true)}</div>${fs.length ? `<div class="box"><h4>越线发现</h4><ul class="flist">${fl}</ul></div>` : ''}</aside></div>
        <h3 class="sh3">窗口内 Top SQL　<span>${esc(cn((ts && ts.headline) || ''))}</span></h3>${tsTable}`);
  }
  const num2 = (v) => { const n = R.num(v); return n === null ? 0 : +n.toFixed(1); };

  /* ------------------------------------------------------------ D1 知识 · 知识库 */
  function secKb() {
    const m = K.model(), ins = K.insightFor(m);
    const cases = `<div class="cases">${m.cases.map((x) => `<article class="case"><h4><a href="#" data-case="${esc(x.id)}">${esc(x.title)}</a></h4><div class="meta">${esc(x.severity)} · ${esc(x.system)} · ${esc(x.occurred_at)} · ${esc(x.conclusion)}</div>
      <p><b>主因</b>　${esc(cn(x.primary_factor))}</p>${Object.entries(x.sections || {}).map(([k, v]) => `<p><b>${esc(k)}</b>　${esc(cn(v))}</p>`).join('')}<div class="refs">依据 ${(x.rules || []).map((r) => `<span>${esc(r)}</span>`).join('')}</div></article>`).join('')}</div>`;
    const q = `<table class="ptable mini"><thead><tr><th>时间</th><th>检索内容</th><th class="r">案例</th><th class="r">条款</th><th>方式</th></tr></thead><tbody>${D.kb.queries.map((x) => `<tr><td class="num t2">${esc(stamp(x.at))}</td><td class="wrap">${esc(x.q)}</td><td class="r num">${x.hits_cases}</td><td class="r num">${x.hits_rules}</td><td>${x.how === 'semantic' ? '语义' : '关键词'}</td></tr>`).join('')}</tbody></table>`;
    const rules = `<ol class="notes">${m.rules.map((r) => `<li><b>${esc(r.id)}</b>　${esc(cn(r.rule))}<span class="t3">（${r.severity === 'error' ? '必须' : '建议'}，${esc(r.group)}）</span></li>`).join('')}</ol>`;
    return sheet('sec-kb', 'D1', '知识 · 知识库', `版本 ${esc(m.st.version)} · ${esc(m.st.mode)} · 全体共享，本环境只读`,
      `<div class="sheet-b"><div class="main"><h3 class="hl3">${ins.head}</h3><div class="body cols2">${pp.paras(ins.lines)}</div><h3 class="sh3">案例摘编　<span>按发生时间；点标题看全文</span></h3>${cases}</div>
        <aside class="rcol"><div class="box"><h4>缺口</h4>${P.bars(m.misses.map((x) => [x.code, x.n, '次', '查不到案例']), true)}</div><div class="box"><h4>最近检索</h4>${q}</div><div class="box"><h4>条款</h4>${rules}</div></aside></div>`);
  }

  R.sections = () => secHealth() + secTop() + secWdr() + secKb();
})(window.R6);
