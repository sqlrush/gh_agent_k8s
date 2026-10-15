/* R9「智能体工作台」的框架:左侧任务栏、中间对话线程、右侧证据栏、底部输入框。
   每份报告是一个「任务」;页面模块的抽屉改为在右侧证据栏打开;提问直接追加在线程末尾。
   必须在 shell.js 之前加载(shell 加载完就路由)。 */
(function (R) {
  'use strict';
  const { D, S, $, esc, I, glyph, stamp, day } = R;
  const ws = (R.ws = { threads: {} });

  /* ------------------------------------------------------------ 左侧任务:每份真实报告一个;示意条目只计数 */
  function tasks() {
    const out = [];
    R.real('health', S.key).forEach((e, i) => out.push({ page: 'health', key: e.file, at: e.at, ic: glyph(R.entryLevel(e)), title: '健康巡检', sub: `第 ${i + 1} 次` }));
    R.real('wdr', S.key).forEach((e) => { const w = D.wdr[S.key].runs[e.file].window || {}; out.push({ page: 'wdr', key: e.file, at: e.at, ic: glyph(R.entryLevel(e)), title: 'WDR 分析', sub: `snap ${w.begin_id}–${w.end_id}` }); });
    R.real('topsql', S.key).forEach((e) => { const by = D.topsql[S.key].runs[e.file].by, b = R.pages.topsql.BY.find((x) => x[0] === by); out.push({ page: 'topsql', key: by, at: e.at, ic: `<span class="glyph tk">${I.list}</span>`, title: 'Top SQL', sub: `按${b ? b[1] : by}` }); });
    return out.sort((a, b) => String(b.at).localeCompare(String(a.at)));
  }
  const demoCount = () => ['health', 'wdr'].reduce((a, k) => a + ((D[k][S.key] && D[k][S.key].index.filter((e) => e.demo).length) || 0), 0);
  function activeKey(page) {
    if (page === 'topsql') { const t = R.pages.topsql.data(); return t && t.by; }
    if (page === 'kb') return 'kb';
    const c = R.current(page, S.key); return c && c.entry.file;
  }
  function renderTasks() {
    const list = tasks(), act = activeKey(S.page);
    const item = (t) => `<a href="#" class="task-i${t.page === S.page && t.key === act ? ' on' : ''}" data-task="${esc(t.page)}|${esc(t.key)}">${t.ic}<span class="m"><b>${esc(t.title)}</b><small>${esc(t.sub)}</small></span><span class="tm num">${esc(stamp(t.at).slice(6))}</span></a>`;
    const days = [...new Set(list.map((t) => day(t.at)))];
    const n = demoCount();
    $('#tasks').innerHTML = `<div class="grp">常驻</div><a href="#" class="task-i${S.page === 'kb' ? ' on' : ''}" data-task="kb|kb"><span class="glyph tk">${I.book}</span><span class="m"><b>知识库</b><small>全体共享 · 只读</small></span></a>
      ${days.map((d) => `<div class="grp">${esc(d)}</div>${list.filter((t) => day(t.at) === d).map(item).join('')}`).join('')}
      ${list.length ? '' : '<div class="rail-note">这个实例还没有任务。按 ⌘K 发起一次巡检。</div>'}
      ${n ? `<div class="rail-note">更早还有 ${n} 次巡检与窗口，为原型补的示意数据，不在此列出。</div>` : ''}`;
  }

  /* ------------------------------------------------------------ 线程部件 */
  const STEP = {
    ok: '<svg viewBox="0 0 16 16"><circle cx="8" cy="8" r="7" fill="currentColor"/><path d="m5.1 8.1 2 2 3.9-4" fill="none" stroke="#fff" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    fail: '<svg viewBox="0 0 16 16"><circle cx="8" cy="8" r="7" fill="currentColor"/><path d="m5.8 5.8 4.4 4.4m0-4.4-4.4 4.4" stroke="#fff" stroke-width="1.6" stroke-linecap="round"/></svg>',
    warn: '<svg viewBox="0 0 16 16"><circle cx="8" cy="8" r="7" fill="currentColor"/><path d="M8 4.6v4.1" stroke="#fff" stroke-width="1.6" stroke-linecap="round"/><circle cx="8" cy="11.2" r=".95" fill="#fff"/></svg>',
    skip: '<svg viewBox="0 0 16 16" fill="none"><circle cx="8" cy="8" r="6.4" stroke="currentColor" stroke-width="1.5" stroke-dasharray="2.4 2"/><path d="M5.4 8h5.2" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>',
  };
  /** 任务卡:智能体做了哪几步。每一步都来自报告字段(维度是否采到、子技能成败、判级、知识库是否接入)。 */
  ws.task = (icon, title, meta, steps) => {
    const bad = steps.filter(([s]) => s === 'fail').length;
    return `<section class="task rise"${R.rise()}><div class="task-h"><span class="ti">${icon}</span><div><b>${esc(title)}</b><small>${esc(meta)}</small></div>
      <span class="done">${steps.length} 步${bad ? `，<em>${bad} 步失败</em>` : '，全部完成'}</span></div>
      <ol class="steps">${steps.map(([s, t, d]) => `<li class="st-${s} rise"${R.rise()}><span class="si">${STEP[s]}</span><span class="sl">${esc(t)}</span><span class="sd" title="${esc(d)}">${esc(d)}</span></li>`).join('')}</ol></section>`;
  };
  ws.agent = (head, body, note) => `<section class="msg-agent rise"${R.rise()}><div class="who"><span class="orb">${I.spark}</span><b>智能体</b><span>${esc(note || '依据本次报告生成 · 级别由脚本判定')}</span></div><h2>${head}</h2>${body}</section>`;
  ws.lines = (lines) => `<ul class="ans">${lines.filter(Boolean).map((l) => `<li>${l}</li>`).join('')}</ul>`;
  ws.art = (o) => `<section class="art rise"${R.rise()}><div class="art-h"><span class="ai">${I[o.ic] || I.grid}</span><b>${o.title}</b>${o.sub ? `<span class="t3">${o.sub}</span>` : ''}<div class="tools">${o.tools || ''}</div></div><div class="art-b${o.flush ? ' flush' : ''}">${o.body}</div></section>`;
  ws.follow = (chips) => `<section class="follow rise"${R.rise()}><div class="fh">${I.spark}接着问</div><div class="fl">${chips.map(([l, p]) => `<button${R.ask(p)}><span>${esc(l)}</span>${I.arrow}</button>`).join('')}</div></section>`;
  /** 当前任务里追加的问答。原型不连 opencode,回答处说明真实环境会怎样。 */
  ws.chat = () => S.chat.filter((c) => c.page === S.page).map((c) => `<div class="u-msg">${esc(c.q)}</div>${c.pending
    ? `<div class="a-msg"><div class="who"><span class="orb">${I.spark}</span><b>智能体</b><span>正在读取报告…</span></div><div class="typing"><i></i><i></i><i></i></div></div>`
    : `<div class="a-msg"><div class="who"><span class="orb">${I.spark}</span><b>智能体</b></div><p>原型里不连 opencode。真实环境会在这里接着回答：引用这份报告里的字段，必要时调用子技能（如 explain、sqltune）再取数，每一步都会像上面一样列出来。</p><button class="btn sm"${R.dig(c.q)}>${I.ext}在 opencode 中继续</button></div>`}`).join('');
  ws.empty = (title, text, chips) => ws.agent(esc(title), `<p class="lead">${esc(text)}</p>`, '这个实例上还没有这类任务') + ws.follow(chips);

  /* ------------------------------------------------------------ 顶部、证据栏、输入框上下文 */
  function renderHead() {
    const h = ws.threads[S.page].head();
    $('#thead').innerHTML = `<div class="th-t"><b>${esc(h.title)}</b>${h.tag || ''}<span class="meta">${esc(h.meta || '')}</span></div><div class="th-a">${h.actions || ''}</div>`;
    $('#ctxpill').innerHTML = (h.ctx || []).map((c) => `<span>${esc(c)}</span>`).join('');
  }
  function renderPane() {
    const d = S.paneDetail, t = ws.threads[S.page];
    $('#pane').innerHTML = d
      ? `<div class="ph"><button class="icon-btn" data-pane-back aria-label="返回">${I.arrow}</button><h3>${d.title}</h3>${d.mark || ''}</div><div class="pb">${d.body}</div>${d.foot ? `<div class="pf">${d.foot}</div>` : ''}`
      : `<div class="ph"><h3>证据</h3><span class="t3">点线程里的来源或条目，在这里展开</span></div><div class="pb">${t.pane()}</div>`;
  }
  R.drawer = (title, mark, body, foot) => {
    S.paneDetail = { title, mark, body, foot };
    renderPane();
    const p = $('#pane'); p.classList.remove('flash'); void p.offsetWidth; p.classList.add('flash');
  };
  R.afterRender = (anim) => {
    if (anim) S.paneDetail = null;
    renderTasks(); renderHead(); renderPane();
  };
  R.afterRoute = () => { $('#center').scrollTop = 0; };
  const toBottom = () => { const c = $('#center'); c.scrollTop = c.scrollHeight; };
  R.askHook = (q) => {
    S.chat = [...S.chat, { page: S.page, q, pending: true }];
    R.render(false); toBottom();
    setTimeout(() => { S.chat = S.chat.map((c) => ({ ...c, pending: false })); R.render(false); toBottom(); }, 900);
  };
  R.openAssistHook = () => { const i = $('.composer input'); if (i) i.focus(); };

  document.addEventListener('click', (ev) => {
    const el = ev.target.closest('[data-task],[data-pane-back],#pane [data-close]');
    if (!el) return;
    ev.preventDefault();
    if (el.dataset.task) {
      const [page, key] = el.dataset.task.split('|');
      if (page === 'topsql') S.by = key; else if (page !== 'kb') S.run[page + ':' + S.key] = key;
      if (S.page === page) { R.render(true); R.afterRoute(); } else location.hash = '#/' + page;
      return;
    }
    S.paneDetail = null; renderPane();
  });
})(window.R6);
