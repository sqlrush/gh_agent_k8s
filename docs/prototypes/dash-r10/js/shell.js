/* R6 外壳:路由、抽屉、提示、⌘K 命令面板、⌘J 智能体侧栏、入场编排(滚动数字)、全局事件。最后加载。
   原型不连 opencode:提问只在侧栏里记下问题,并说明真实环境会怎样回答。 */
(function (R) {
  'use strict';
  const { D, S, $, $$, esc, I, glyph, lname, stamp } = R;
  // ?still 只为截图验收:跳过动效,直接画终态(无头浏览器里 requestAnimationFrame 不一定走完)
  const still = new URLSearchParams(location.search).has('still');
  const reduced = () => still || window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* ------------------------------------------------------------ 渲染与路由 */
  const instLevel = (k) => { const c = D.health[k]; if (!c) return null; const e = c.index.filter((x) => !x.demo); return e.length ? R.entryLevel(e[e.length - 1]) : null; };
  function chrome() {
    const t = D.targets.find((x) => x.key === S.key) || {};
    $('#inst').innerHTML = `${glyph(instLevel(S.key))}<span class="t3">实例</span>${esc(t.label || S.key)}<span class="chev">${I.chev}</span>`;
    $$('#nav a[data-p]').forEach((a) => a.classList.toggle('on', a.dataset.p === S.page));
  }
  function countUp(root) {
    if (reduced()) return;
    $$('[data-count]', root).forEach((el, i) => {
      const txt = el.textContent.trim(), m = /^(-?)([\d,]*\.?\d+)$/.exec(txt);
      if (!m) return;
      const target = parseFloat(m[2].replace(/,/g, '')), dec = (m[2].split('.')[1] || '').length, comma = m[2].includes(',');
      const fmt = (v) => { const s = v.toFixed(dec); if (!comma) return m[1] + s; const [a, b] = s.split('.'); return m[1] + Number(a).toLocaleString('en-US') + (b ? '.' + b : ''); };
      const t0 = performance.now(), dur = 900 + i * 35;
      const step = (t) => { const p = Math.min(1, (t - t0) / dur); el.textContent = p < 1 ? fmt(target * (1 - Math.pow(1 - p, 3))) : txt; if (p < 1) requestAnimationFrame(step); };
      el.textContent = fmt(0);
      requestAnimationFrame(step);
    });
  }
  /** anim=true 只在进入页面、切实例时:一次编排好的入场;页内切换(换报告、换排序)不重播。 */
  function render(anim) {
    chrome();
    R.resetSeq();
    const page = $('#page');
    page.classList.toggle('anim', !!anim && !reduced());
    page.innerHTML = R.pages[S.page].render();
    if (anim) { countUp(page); clearTimeout(render.t); render.t = setTimeout(() => page.classList.remove('anim'), 2000); }
    if (R.afterRender) R.afterRender(anim);
    if ($('#assist').classList.contains('on')) renderAssist();
  }
  R.render = render;
  function route() { const p = (location.hash.match(/^#\/(\w+)/) || [])[1]; S.page = R.pages[p] ? p : 'health'; render(true); if (R.afterRoute) R.afterRoute(); else window.scrollTo(0, 0); }

  /* ------------------------------------------------------------ 抽屉 / 提示 / 菜单 */
  // 布局可以先定义自己的 R.drawer(如把详情放进右侧栏),这里只在没有时给默认的抽屉
  R.drawer = R.drawer || ((title, mark, body, foot) => {
    $('#drawer').innerHTML = `<div class="dh"><h2>${title}</h2>${mark || ''}<button class="icon-btn" data-close aria-label="关闭">${I.close}</button></div><div class="db">${body}</div>${foot ? `<div class="df">${foot}</div>` : ''}`;
    $('#drawer').classList.add('on'); $('#scrim').classList.add('on');
  });
  const closeDrawer = () => { $('#drawer').classList.remove('on'); $('#scrim').classList.remove('on'); $('#menu').classList.remove('on'); };
  function toast(msg) { const t = $('#toast'); t.innerHTML = `${I.info}<span>${esc(msg)}</span>`; t.classList.add('on'); clearTimeout(toast.h); toast.h = setTimeout(() => t.classList.remove('on'), 3600); }
  function menu(anchor, html, right) {
    const m = $('#menu'), r = anchor.getBoundingClientRect();
    m.innerHTML = html; m.classList.add('on');
    m.style.top = r.bottom + 6 + window.scrollY + 'px';
    m.style.left = (right ? r.right - m.offsetWidth : r.left) + window.scrollX + 'px';
  }

  /* ------------------------------------------------------------ ⌘J 智能体侧栏 */
  const ctxNow = () => R.pages[S.page].assist();
  function renderAssist() {
    const a = ctxNow();
    const chat = S.chat.map((c) => `<div class="msg me">${esc(c.q)}</div>${c.pending ? '<div class="msg ai typing"><i></i><i></i><i></i></div>'
      : `<div class="msg ai"><p>原型里不连 opencode。真实环境会把这个问题连同上下文（${esc(c.ctx.join(' · '))}）交给你本人的 opencode 会话，回答逐字出现在这里，并标出引用了报告里的哪些字段。</p><button class="btn sm"${R.dig(c.q)}>${I.ext}在会话里打开</button></div>`}`).join('');
    const hello = `<div class="hello">你好，${esc(D.whoami.user_id)}<br><span>想先弄清哪件事？</span></div><p class="lead">我能看到你当前打开的报告。下面是根据它建议的问题，也可以直接输入。</p>`;
    $('#assist').innerHTML = `<div class="ah"><span class="orb">${I.spark}</span><div><b>智能体</b><small>基于当前页面的上下文</small></div><button class="icon-btn" data-assist-close aria-label="关闭" style="margin-left:auto">${I.close}</button></div>
      <div class="ab"><div class="ctx">${a.ctx.map((c) => `<span>${esc(c)}</span>`).join('')}</div>${S.chat.length ? chat : hello}
        <div class="sugg">${S.chat.length ? '<div class="sugg-h">还可以问</div>' : ''}${a.sugg.filter(([t, , p]) => !S.chat.some((c) => c.q === p || c.q === t)).slice(0, S.chat.length ? 2 : 4).map(([t, s, p]) => `<button${R.ask(p)}>${I.spark}<span>${esc(t)}<small>${esc(s)}</small></span></button>`).join('')}</div></div>
      <div class="af"><form class="askbox" data-askform>${I.spark}<input name="q" autocomplete="off" placeholder="输入问题，回车发送"><button class="send" aria-label="发送">${I.send}</button></form><div class="hint">回答由你本人的 opencode 会话生成 · 级别以脚本判定为准</div></div>`;
    const ab = $('#assist .ab'); ab.scrollTop = ab.scrollHeight;
  }
  function openAssist() { if (R.openAssistHook) return R.openAssistHook(); closeDrawer(); closeCmdk(); renderAssist(); $('#assist').classList.add('on'); setTimeout(() => { const i = $('#assist input'); if (i) i.focus({ preventScroll: true }); }, 320); }
  const closeAssist = () => $('#assist').classList.remove('on');
  function ask(q) {
    q = String(q || '').trim();
    if (!q) return;
    if (R.askHook) return R.askHook(q);
    S.chat = [...S.chat, { q, ctx: ctxNow().ctx, pending: true }];
    openAssist();
    setTimeout(() => { S.chat = S.chat.map((c) => ({ ...c, pending: false })); renderAssist(); }, reduced() ? 0 : 900);
  }

  /* ------------------------------------------------------------ ⌘K 命令面板 */
  const PAGE_IC = { health: 'pulse', topsql: 'list', wdr: 'clock', kb: 'book' };
  function cmdItems(q) {
    const base = [
      ...[['health', '健康检查'], ['topsql', 'Top SQL'], ['wdr', 'WDR 分析'], ['kb', '知识库']].map(([p, l]) => ({ grp: '页面', label: l, sub: S.page === p ? '当前页面' : '', ic: PAGE_IC[p], go: () => { location.hash = '#/' + p; } })),
      ...D.targets.map((t) => { const l = instLevel(t.key); return { grp: '实例', label: t.label, sub: `${lname(l)}，最近巡检 ${stamp(t.last_at)}`, lv: l, go: () => { S.key = t.key; render(true); } }; }),
      { grp: '操作', label: '问智能体', sub: '⌘J', ic: 'spark', go: openAssist },
      { grp: '操作', label: '重新巡检', sub: '在会话里做一次完整健康检查', ic: 'redo', go: () => digToast('请对当前实例重新做一次完整健康检查。') },
      { grp: '操作', label: '新对话', sub: '打开本人的 opencode 会话', ic: 'chat', go: () => digToast('新对话') },
    ];
    const all = [...base, ...['health', 'topsql', 'wdr', 'kb'].flatMap((p) => R.pages[p].commands())];
    if (!q) return base.concat(R.pages.health.commands().filter((c) => c.grp === '发现'));
    const k = q.toLowerCase(), hit = all.filter((c) => (c.label + ' ' + (c.sub || '')).toLowerCase().includes(k));
    const per = {}, capped = hit.filter((c) => (per[c.grp] = (per[c.grp] || 0) + 1) <= 5);
    return [{ grp: '智能体', label: `问智能体：“${q}”`, sub: '带上当前页面的上下文', ic: 'spark', ai: true, go: () => ask(q) }, ...capped];
  }
  function renderCmdRes() {
    const q = $('#cmdk input').value.trim(), items = cmdItems(q);
    S.cmd = { items, i: Math.min(S.cmd ? S.cmd.i : 0, Math.max(0, items.length - 1)) };
    let g = '';
    $('#cmdk .res').innerHTML = items.length ? items.map((c, i) => {
      const h = c.grp !== g ? `<div class="grp">${esc(c.grp)}</div>` : ''; g = c.grp;
      const ic = c.lv !== undefined ? glyph(c.lv) : I[c.ic] || I.arrow;
      return `${h}<div class="opt${i === S.cmd.i ? ' act' : ''}${c.ai ? ' ai' : ''}" data-cmd="${i}"><span class="ic">${ic}</span><span class="lb">${esc(c.label)}</span>${c.sub ? `<span class="sub">${esc(c.sub)}</span>` : ''}</div>`;
    }).join('') : '<div class="empty">没有匹配的结果</div>';
    const act = $('#cmdk .opt.act'); if (act) act.scrollIntoView({ block: 'nearest' });
  }
  function openCmdk(q = '') {
    closeAssist();
    S.cmd = { items: [], i: 0 };
    $('#cmdk').innerHTML = `<div class="in">${I.search}<input placeholder="搜索页面、实例、SQL、维度、案例，或直接提问" autocomplete="off"><span class="kbd"><kbd>esc</kbd></span></div><div class="res"></div>
      <div class="foot"><span><kbd>↑</kbd><kbd>↓</kbd> 选择</span><span><kbd>↵</kbd> 打开</span><span>输入问题再回车，可直接问智能体</span></div>`;
    $('#cmdk input').value = q;
    $('#cmdk').classList.add('on'); $('#cmdk-scrim').classList.add('on');
    renderCmdRes();
    setTimeout(() => $('#cmdk input').focus(), 30);
  }
  function closeCmdk() { $('#cmdk').classList.remove('on'); $('#cmdk-scrim').classList.remove('on'); }
  function runCmd(i) { const c = S.cmd && S.cmd.items[i]; if (!c) return; closeCmdk(); c.go(); }
  function digToast(p) { toast(`原型：真实环境会在新标签页打开本人的 opencode 会话，并带上「${p.slice(0, 36)}${p.length > 36 ? '…' : ''}」`); }

  /* ------------------------------------------------------------ 事件 */
  const SEL = '[data-ask],[data-dig],[data-run],[data-by],[data-sql],[data-dim],[data-xdim],[data-wdim],[data-kbtab],[data-case],[data-close],[data-copy],[data-toast],[data-inst],[data-pick],[data-cmd],[data-assist-close],#scrim,#cmdk-scrim,#inst,#run-btn,#cmdk-btn,#ask-btn';
  document.addEventListener('click', (ev) => {
    const el = ev.target.closest(SEL);
    if (!el) { if (!ev.target.closest('#menu')) $('#menu').classList.remove('on'); return; }
    ev.preventDefault();
    const ds = el.dataset;
    if (ds.ask !== undefined) return ask(ds.ask);
    if (ds.dig !== undefined) return digToast(ds.dig);
    if (ds.run) { const [sk, f] = ds.run.split('|'); S.run[sk + ':' + S.key] = f; return render(false); }
    if (ds.by) { S.by = ds.by; return render(false); }
    if (ds.sql) return R.pages.topsql.openSql(ds.sql);
    if (ds.dim !== undefined) return R.pages.health.openDim(+ds.dim);
    if (ds.xdim) return R.pages.health.openXdim(ds.xdim);
    if (ds.wdim) return R.pages.wdr.openWdim(ds.wdim);
    if (ds.kbtab) { S.kbTab = ds.kbtab; if (S.page !== 'kb') location.hash = '#/kb'; return render(false); }
    if (ds.case) return R.pages.kb.openCase(ds.case);
    if (ds.copy) { if (navigator.clipboard) navigator.clipboard.writeText(ds.copy); return toast(`已复制 ${ds.copy}`); }
    if (ds.toast) return toast(ds.toast);
    if (ds.cmd !== undefined) return runCmd(+ds.cmd);
    if (ds.assistClose !== undefined) return closeAssist();
    if (ds.close !== undefined || el.id === 'scrim') return closeDrawer();
    if (el.id === 'cmdk-scrim') return closeCmdk();
    if (ds.inst) { S.key = ds.inst; closeDrawer(); return render(true); }
    if (ds.pick) { const [sk, f] = ds.pick.split('|'); S.run[sk + ':' + S.key] = f; closeDrawer(); return render(false); }
    if (el.id === 'cmdk-btn') return openCmdk();
    if (el.id === 'ask-btn') return $('#assist').classList.contains('on') ? closeAssist() : openAssist();
    if (el.id === 'inst') return menu(el, `<div class="mh">切换实例（状态为最近一次巡检）</div>${D.targets.map((t) => { const l = instLevel(t.key); return `<div class="mi ${t.key === S.key ? 'on' : ''}" data-inst="${esc(t.key)}">${glyph(l)}${esc(t.label)}<span class="r">${esc(lname(l))}，${esc(stamp(t.last_at))}</span></div>`; }).join('')}`);
    if (el.id === 'run-btn') {
      const sk = el.dataset.skill, cur = R.current(sk, S.key);
      return menu(el, `<div class="mh">${sk === 'wdr' ? '分析过的窗口' : '巡检报告'}，最近的在上</div>${[...D[sk][S.key].index].reverse().map((e) => (e.demo ? `<div class="mi dis">${glyph(R.entryLevel(e))}<span class="num">${esc(stamp(e.at))}</span><span class="r">示意</span></div>` : `<div class="mi ${cur && cur.entry.file === e.file ? 'on' : ''}" data-pick="${esc(sk)}|${esc(e.file)}">${glyph(R.entryLevel(e))}<span class="num">${esc(stamp(e.at))}</span><span class="r">${esc(lname(R.entryLevel(e)))}</span></div>`)).join('')}`, true);
    }
  });
  document.addEventListener('submit', (ev) => {
    const f = ev.target.closest('[data-askform]'); if (!f) return;
    ev.preventDefault();
    const inp = f.querySelector('input'), q = inp.value;
    inp.value = '';
    ask(q);
  });
  document.addEventListener('input', (ev) => { if (ev.target.closest('#cmdk .in')) { S.cmd.i = 0; renderCmdRes(); } });
  document.addEventListener('keydown', (ev) => {
    const mod = ev.metaKey || ev.ctrlKey, k = ev.key.toLowerCase();
    if (mod && k === 'k') { ev.preventDefault(); return $('#cmdk').classList.contains('on') ? closeCmdk() : openCmdk(); }
    if (mod && k === 'j') { ev.preventDefault(); return $('#assist').classList.contains('on') ? closeAssist() : openAssist(); }
    if ($('#cmdk').classList.contains('on')) {
      const n = S.cmd.items.length;
      if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') { ev.preventDefault(); S.cmd.i = (S.cmd.i + (ev.key === 'ArrowDown' ? 1 : -1) + n) % Math.max(1, n); return renderCmdRes(); }
      if (ev.key === 'Enter') { ev.preventDefault(); return runCmd(S.cmd.i); }
      if (ev.key === 'Escape') return closeCmdk();
      return;
    }
    if (ev.key === 'Escape') { if ($('#drawer').classList.contains('on')) closeDrawer(); else closeAssist(); }
  });
  const tip = $('#tip');
  document.addEventListener('mousemove', (ev) => {
    const el = ev.target.closest && ev.target.closest('[data-tip]');
    if (!el) { tip.classList.remove('on'); return; }
    tip.innerHTML = el.dataset.tip;
    tip.style.left = Math.min(ev.clientX + 14, window.innerWidth - tip.offsetWidth - 12) + 'px';
    tip.style.top = Math.min(ev.clientY + 16, window.innerHeight - tip.offsetHeight - 12) + 'px';
    tip.classList.add('on');
  });
  document.addEventListener('mouseover', (ev) => {
    const svg = $('#kbgraph'); if (!svg) return;
    const g = ev.target.closest && ev.target.closest('#kbgraph .node'), cs = g ? g.dataset.cases.split('|').filter(Boolean) : [];
    $$('.node', svg).forEach((n) => n.classList.toggle('hl', cs.some((c) => n.dataset.cases.split('|').includes(c))));
    $$('path', svg).forEach((p) => p.classList.toggle('hl', cs.includes(p.dataset.case)));
  });

  /* ------------------------------------------------------------ 启动 */
  $('#who').textContent = D.whoami.user_id;
  $('#av').textContent = D.whoami.user_id.slice(0, 1).toUpperCase();
  const q = new URLSearchParams(location.search);
  if (q.get('inst')) S.key = q.get('inst');
  if (q.get('by')) S.by = q.get('by');
  if (q.get('tab')) S.kbTab = q.get('tab');
  window.addEventListener('hashchange', route);
  route();
  // 只为截图验收:?dim= ?xdim= ?wdim= ?sql= ?case= 打开对应抽屉;?cmdk=<关键词> 打开命令面板;?assist=1 / ?ask=<问题> 打开智能体侧栏
  if (q.get('dim') && S.page === 'health') R.pages.health.openDim(+q.get('dim'));
  if (q.get('xdim')) R.pages.health.openXdim(q.get('xdim'));
  if (q.get('wdim')) R.pages.wdr.openWdim(q.get('wdim'));
  if (q.get('sql')) R.pages.topsql.openSql(q.get('sql'));
  if (q.get('case')) R.pages.kb.openCase(q.get('case'));
  if (q.has('cmdk')) openCmdk(q.get('cmdk'));
  if (q.get('assist')) openAssist();
  if (q.get('ask')) ask(q.get('ask'));
})(window.R6);
