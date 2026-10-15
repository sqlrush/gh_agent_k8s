/* R6 知识库:主视觉 = 条目总数、覆盖环、关系缩略图 + 智能体解读(缺口);其下 = 最近案例、健康自检、最近检索、缺口清单。
   案例 / 条款 / 关系图三个页签沿用 R5。 */
(function (R) {
  'use strict';
  const { D, S, esc, I, glyph, num, fmtInt, stamp, cn, tipAttr } = R;

  function model() {
    const h = D.kb.health, st = h.status || {}, cat = D.kb.catalog, c = st.counts || {};
    const cov = num((/覆盖\s*([\d.]+)%/.exec(st.vector || '') || [])[1]);
    const issues = (h.file_warnings || []).length + (cov !== null && cov < 95 ? 1 : 0) + (h.pending || []).length;
    const rules = cat.groups.flatMap((g) => g.rules.map((r) => ({ ...r, group: g.title, file: g.file })));
    const cases = [...cat.cases].sort((a, b) => String(b.occurred_at).localeCompare(String(a.occurred_at)));
    return { h, st, cat, c, cov, issues, rules, cases, misses: h.misses || [] };
  }

  function insightFor(m) {
    const q = D.kb.queries, sem = q.filter((x) => x.how === 'semantic').length, none = q.filter((x) => !x.hits_cases && !x.hits_rules).length;
    const head = m.misses.length ? `知识库可用，有 ${m.misses.length} 类诊断发现在库里查不到案例` : '知识库可用，诊断发现都查得到知识';
    return { src: `依据知识库自检 · 版本 ${m.st.version}`, ask: '问问知识库，比如：无主键大表怎么处理？', head,
      lines: [m.misses.length ? `${m.misses.map((x) => `<b>${esc(x.code)}</b> ${x.n} 次`).join('、')}没有命中案例，可以请管理员补充。` : '',
        m.cov !== null ? `向量覆盖 <b>${m.cov}%</b>：没覆盖的条目只能靠关键词命中，管理员在导入环境重建索引即可。` : '',
        ...(m.h.file_warnings || []).map((x) => `文件格式有问题：${esc(cn(x))}。`),
        `你近 7 天检索 ${q.length} 次：语义 ${sem} 次、关键词 ${q.length - sem} 次${none ? `，其中 <b>${none}</b> 次什么都没查到` : ''}。`],
      chips: [...m.misses.slice(0, 1).map((x) => [`${x.code} 怎么处理`, `知识库里没有 ${x.code} 的案例，请根据经验说明怎么处理，并给出可以沉淀成案例的要点。`]),
        ['帮我起草一个案例', '请按案例文件的格式（现场、判断、处置、复发标志）帮我起草一个案例，内容来自最近一次健康检查的发现。'],
        ['向量覆盖怎么算', '知识库的向量覆盖率是怎么计算的？没覆盖的条目会怎样？']] };
  }

  function hero(m) {
    const total = (+m.c['docs.case'] || 0) + (+m.c['docs.rule'] || 0);
    const left = `<div class="glass kbbox rise"${R.rise()}><div class="title">${I.book}知识条目<span class="t3">全体共享，只读</span></div>
      <div class="kbtop"><div><div class="big num">${R.cu(fmtInt(total))}<small>条</small></div>
        <div class="kbsplit"><span>案例 <b>${fmtInt(m.c['docs.case'])}</b></span><span>条款 <b>${fmtInt(m.c['docs.rule'])}</b></span><span class="t3">整理自 ${fmtInt(m.c['docs.raw'])} 份原始工单</span></div></div>
        <div class="kbcov">${R.covring(m.cov, '向量覆盖')}</div></div>
      <div class="mg-h"><span>关系图 · ${m.cat.edges.length} 条已确认的边</span><a href="#" data-kbtab="graph">展开${I.arrow}</a></div>${R.minigraph(m.cat, m.rules)}</div>`;
    return `<section class="hero"><div class="hero-grid">${left}${R.insight(insightFor(m))}</div></section>`;
  }

  const caseTable = (list, full = true) => `<div class="scroll"><table class="tbl"><thead><tr><th>级别</th><th>案例</th><th>系统</th><th>发生时间</th>${full ? '<th>主因</th>' : ''}<th>结论</th><th class="r">操作</th></tr></thead><tbody>${list.map((x) => `<tr><td><span class="tag line">${esc(x.severity)}</span></td><td class="title" title="${esc(x.title)}"><a href="#" data-case="${esc(x.id)}">${esc(x.title)}</a></td><td>${esc(x.system)}</td><td class="num">${esc(x.occurred_at)}</td>${full ? `<td class="q" title="${esc(x.primary_factor)}">${esc(cn(x.primary_factor))}</td>` : ''}<td>${esc(x.conclusion)}</td><td class="ops"><a href="#" data-case="${esc(x.id)}">查看</a></td></tr>`).join('')}</tbody></table></div>`;

  function render() {
    const m = model();
    const tabs = `<div class="tabs">${[['overview', '总览', ''], ['cases', '案例', m.cat.cases.length], ['rules', '条款', m.rules.length], ['graph', '关系图', m.cat.edges.length]].map(([k, l, n]) => `<button data-kbtab="${k}" class="${S.kbTab === k ? 'on' : ''}">${l}${n !== '' ? `<span class="n">${n}</span>` : ''}</button>`).join('')}</div>`;
    const top = R.pageHead('知识库', m.issues ? R.tag(1, `${m.issues} 项待处理`) : R.tag(0, '健康'), `<button class="btn"${R.ask('请在知识库里检索：')}>${I.search}检索知识库</button>`,
      [['模式', esc(m.st.mode)], ['版本', esc(m.st.version)], ['本环境', m.h.readonly ? '只读' : '可写'], ['维护', '全体共享，由知识库管理员在导入环境维护']]) + tabs;
    if (S.kbTab === 'cases') return top + `<div class="bento">${R.tile({ title: '全部案例', desc: '按发生时间，点击查看现场、判断、处置与复发标志', body: caseTable(m.cases), flush: true })}</div>`;
    if (S.kbTab === 'rules') return top + `<div class="bento">${R.tile({ title: '全部条款', desc: '规范、标准与制度中可引用的条文', flush: true, body: `<div class="scroll"><table class="tbl"><thead><tr><th>编号</th><th>条文</th><th>理由</th><th>级别</th><th>出处</th></tr></thead><tbody>${m.rules.map((r) => `<tr><td class="id">${esc(r.id)}</td><td class="wrap">${esc(cn(r.rule))}</td><td class="t2 wrap">${esc(cn(r.rationale))}</td><td>${r.severity === 'error' ? '<span class="tag" data-lv="2">必须</span>' : '<span class="tag gray">建议</span>'}</td><td class="t2 wrap">${esc(r.group)}<br><span class="t3">${esc(r.source)}</span></td></tr>`).join('')}</tbody></table></div>` })}</div>`;
    if (S.kbTab === 'graph') return top + `<div class="bento">${R.tile({ title: '关系图', desc: '案例、现象、根因、处置与引用的条款；鼠标停在节点上，高亮它所在的整条链', body: R.graphSvg(m.cat, m.rules) })}</div>`;
    const qTable = `<div class="scroll"><table class="tbl"><thead><tr><th>时间</th><th>检索内容</th><th class="r">命中案例</th><th class="r">命中条款</th><th>方式</th></tr></thead><tbody>${D.kb.queries.map((q) => `<tr><td class="num t2">${esc(stamp(q.at))}</td><td>${esc(q.q)}</td><td class="r">${q.hits_cases}</td><td class="r">${q.hits_rules}</td><td>${q.how === 'semantic' ? '<span class="tag blue">语义</span>' : '<span class="tag gray">关键词</span>'}</td></tr>`).join('')}</tbody></table></div>`;
    const mx = Math.max(1, ...m.misses.map((x) => x.n));
    const gaps = m.misses.length ? `<div class="hb">${m.misses.map((x) => `<div class="r"${tipAttr(`<b>${esc(x.code)}</b><div class="d">诊断发现在库里查不到案例 ${x.n} 次</div>`)}><div class="l">${esc(x.code)}</div><div class="b"><i class="grow" style="width:${(x.n / mx) * 100}%"></i></div><div class="v">${x.n}<small>次</small></div></div>`).join('')}</div><div class="note">缺口会随诊断自动累计；补上案例后，下次同样的发现就能引用到知识。</div>` : '<p class="t3">没有缺口</p>';
    const health = `<div class="list">${(m.h.file_warnings || []).map((x) => `<div class="it">${glyph(1)}<div class="m"><b>文件格式有问题</b><small>${esc(cn(x))}</small></div></div>`).join('')}${m.cov !== null && m.cov < 95 ? `<div class="it">${glyph(1)}<div class="m"><b>向量覆盖 ${m.cov}%</b><small>没覆盖的条目只能靠关键词命中，管理员在导入环境重建索引即可</small></div></div>` : ''}${!m.issues ? `<div class="it">${glyph(0)}<div class="m"><b>没有待处理项</b></div></div>` : ''}</div>`;
    return top + hero(m) + `<div class="bento">${R.tile({ span: 'b8', title: '最近的案例', desc: '按发生时间', body: caseTable(m.cases.slice(0, 5), false), tools: '<a href="#" data-kbtab="cases">查看全部</a>', flush: true })}
      ${R.tile({ span: 'b4', title: '健康自检', desc: '降级即发现', body: health })}
      ${R.tile({ span: 'b8', title: '最近检索', desc: '本人，近 7 天', body: qTable, flush: true })}
      ${R.tile({ span: 'b4', title: '缺口清单', desc: '诊断发现在库里查不到案例的次数', body: gaps })}</div>`;
  }

  function openCase(id) {
    const x = D.kb.catalog.cases.find((c) => c.id === id); if (!x) return;
    const kv = (k, v) => `<div><div class="kv"><div class="k">${k}</div><div class="v">${esc(v)}</div></div></div>`;
    const sec = Object.entries(x.sections || {}).map(([k, v]) => `<div class="sec">${esc(k)}</div><p class="t2" style="margin:0">${esc(cn(v))}</p>`).join('');
    R.drawer(esc(x.title), `<span class="tag line">${esc(x.severity)}</span>`, `<div class="kvs" style="--cols:3">${kv('系统', x.system)}${kv('发生时间', x.occurred_at)}${kv('结论', x.conclusion)}</div>
      <div class="sec">主因</div><p style="margin:0">${esc(cn(x.primary_factor))}</p>${sec}
      <div class="sec">依据条款</div><p style="margin:0">${(x.rules || []).map((r) => `<span class="tag gray" style="margin-right:6px">${esc(r)}</span>`).join('')}</p>
      <div class="sec">出处</div><p class="t3" style="margin:0">${esc(x.source)}</p>`,
      `<button class="btn primary"${R.ask(`请参考案例「${x.title}」，判断当前实例有没有同样的复发标志：${(x.sections || {})['复发标志'] || ''}`)}>${I.spark}对照当前实例</button><button class="btn" data-close>关闭</button>`);
  }

  function assist() {
    const m = model();
    return { ctx: ['知识库', `版本 ${m.st.version}`, `${fmtInt((+m.c['docs.case'] || 0) + (+m.c['docs.rule'] || 0))} 条知识`],
      sugg: [...m.misses.slice(0, 2).map((x) => [`${x.code} 怎么处理？`, `库里查不到，${x.n} 次`, `知识库里没有 ${x.code} 的案例，请根据经验说明怎么处理。`]),
        ['有没有和现在相似的案例？', '用最近一次健康检查的发现去检索', '请用最近一次健康检查的发现检索知识库，列出相似案例与复发标志。'],
        ['帮我起草一个案例', '现场、判断、处置、复发标志', '请按案例文件的格式帮我起草一个案例。']] };
  }
  function commands() {
    return D.kb.catalog.cases.map((x) => ({ grp: '案例', label: x.title, sub: `${x.severity} · ${x.system} · ${x.occurred_at}`, ic: 'book', go: () => { location.hash = '#/kb'; setTimeout(() => openCase(x.id), 0); } }));
  }
  R.pages.kb = { render, assist, commands, openCase };
})(window.R6);
