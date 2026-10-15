/* R10「运维日报」的头版:报头、头条(健康检查)、本期数字与八项检查一览、折线下的三个小栏目。
   四个页面合成一份报纸;路由只决定翻到哪一版。文字沿用 R6 的确定性生成,数字都取自报告。
   必须在 shell.js 之前加载。 */
(function (R) {
  'use strict';
  const { D, S, $, esc, lname, stamp, fmtInt, cn } = R;
  const H = R.pages.health, T = R.pages.topsql, W = R.pages.wdr, K = R.pages.kb, P = R.press;
  const pp = (R.paper = {});
  pp.sortF = (fs) => [...(fs || [])].sort((a, b) => (b.severity | 0) - (a.severity | 0));
  const sum = (c) => Object.values(c || {}).reduce((a, b) => a + (+b || 0), 0);
  const DF = new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', year: 'numeric', month: 'numeric', day: 'numeric', weekday: 'long' });
  const toDate = (s) => { const m = /^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z$/.exec(s || ''); return m ? new Date(Date.UTC(+m[1], m[2] - 1, +m[3], +m[4], +m[5], +m[6])) : new Date(s); };
  pp.fullDate = (at) => { const p = DF.formatToParts(toDate(at)), g = (t) => (p.find((x) => x.type === t) || {}).value; return `${g('year')} 年 ${g('month')} 月 ${g('day')} 日 ${g('weekday')}`; };
  pp.jump = (id, label) => `<a href="#" class="jump" data-jump="${id}">${esc(label)} →</a>`;
  pp.kicker = (t) => `<div class="kicker">${esc(t)}</div>`;
  pp.paras = (lines) => lines.filter(Boolean).map((l) => `<p>${l}</p>`).join('');
  pp.bizTop = (t) => [...(t.rep.rows || [])].filter((r) => !T.isSys(t, r)).sort((a, b) => b.total_sec - a.total_sec)[0];

  /* ------------------------------------------------------------ 头条 */
  function lead() {
    const cur = R.current('health', S.key);
    if (!cur) return `<article class="lead">${pp.kicker('头条 · 健康检查')}<h2 class="hl">本期没有巡检报告</h2><p class="deck">这个实例还没有健康检查报告。在会话里做一次巡检，下一期就会刊出。</p>${R.primary('请对当前实例做一次健康检查。', '做一次健康检查')}</article>`;
    const r = cur.data, lvl = R.repLevel(r), fs = pp.sortF(r.findings), ins = H.insightFor(cur, r, lvl, fs), segs = H.checks(r);
    const failed = (r.sub_skills || []).filter((s) => !s.ok).map((s) => s.skill.replace('gaussdb-', ''));
    const d = cur.prev ? fs.length - sum(cur.prev.entry.counts) : null;
    const deck = lvl === null ? `${r.conn} 本期 ${(r.dims || []).length} 个维度全部采集失败${failed.length ? `，子技能 ${failed.join('、')} 也未执行成功` : ''}。`
      : `${r.conn} 本期 ${fs.length} 条越线发现，${d === null ? '这是第一次巡检' : d > 0 ? `比上期多 ${d} 条` : d < 0 ? `比上期少 ${-d} 条` : '与上期持平'}；${failed.length ? `子技能 ${failed.join('、')} 未能执行，相关检查未纳入。` : '子技能全部执行成功。'}`;
    const intro = `本期例行巡检于 ${stamp(cur.entry.at)} 在 ${r.conn} 实例上进行，共检查 ${segs.length} 项，巡检脚本按阈值判定总体为「${lname(lvl)}」。以下各条由智能体依据本次报告整理，数字均取自报告原文。`;
    return `<article class="lead">${pp.kicker('头条 · 健康检查')}<h2 class="hl">${ins.head}</h2><p class="deck">${esc(R.pangu(deck))}</p>
      <div class="byline">智能体 撰　·　${esc(stamp(cur.entry.at))}　·　级别由巡检脚本按阈值判定，模型不改</div>
      <figure class="fig">${P.trend('health', S.key, cur.entry.file, '条')}<figcaption>图　近 ${cur.index.length} 次巡检的越线发现条数。空心点为原型补的示意数据；点一次巡检可翻到那一期。</figcaption></figure>
      <div class="body cols2"><p class="dropcap">${esc(R.pangu(intro))}</p>${pp.paras(ins.lines)}<p class="more">${pp.jump('sec-health', '全部发现与八个维度见 A2 版')}</p></div></article>`;
  }

  /* ------------------------------------------------------------ 右栏:本期数字、八项检查一览 */
  function numbers() {
    const items = [], cur = R.current('health', S.key);
    if (cur) {
      const r = cur.data, lvl = R.repLevel(r), fs = pp.sortF(r.findings);
      if (lvl === null) items.push([`${(r.dims || []).filter((x) => x.available === false).length}/${(r.dims || []).length}`, '个维度采集失败', '一个维度都没有采到']);
      else {
        const d = cur.prev ? fs.length - sum(cur.prev.entry.counts) : null;
        items.push([String(fs.length), '条越线发现', d === null ? '第一次巡检' : d ? `比上期${d > 0 ? '多' : '少'} ${Math.abs(d)} 条` : '与上期持平']);
        const best = fs.map((f) => ({ f, x: R.times(f) })).filter((o) => o.x).sort((a, b) => b.x - a.x)[0];
        if (best) items.push([`${best.x.toFixed(1)}×`, `${R.dt(best.f.dimension)}超出阈值`, `${(H.METRIC[best.f.metric] || [best.f.metric])[0]} ${best.f.value}，阈值 ${best.f.threshold}`]);
      }
    }
    const wc = R.current('wdr', S.key);
    if (wc && wc.prev) { const a = W.kpiVal(W.KPIS[0], wc.data), b = W.kpiVal(W.KPIS[0], wc.prev.data); if (a !== null && b) items.push([`${(a / b).toFixed(1)}×`, 'DB Time 对上一窗口', `${fmtInt(Math.round(b))} → ${fmtInt(Math.round(a))} 秒`]); }
    const t = T.data();
    if (t) { const rows = t.rep.rows || [], s = rows.reduce((a, x) => a + (+x.total_sec || 0), 0) || 1, top = [...rows].sort((a, b) => b.total_sec - a.total_sec)[0];
      items.push([`${(((+top.total_sec || 0) / s) * 100).toFixed(1)}%`, '耗时第一的语句占上榜', `${top.sql_id}${T.isSys(t, top) ? '，系统 SQL' : ''}`]); }
    const m = K.model();
    if (m.cov !== null) items.push([`${m.cov}%`, '知识库向量覆盖', `${m.misses.length} 类诊断发现查不到案例`]);
    return `<div class="box"><h4>本期数字</h4>${items.map(([v, l, s]) => `<div class="numb"><span class="v">${esc(v)}</span><span class="l">${esc(l)}</span><span class="s">${esc(R.pangu(cn(s)))}</span></div>`).join('')}</div>`;
  }
  function dimsBox() {
    const cur = R.current('health', S.key); if (!cur) return '';
    return `<div class="box"><h4>八项检查一览</h4><table class="ptable mini"><tbody>${(cur.data.dims || []).map((d, i) => { const l = H.dimLevel(d), k = d.available === false ? null : (H.KEYFIG[d.dimension] || (() => null))(d);
      return `<tr data-dim="${i}"><td>${esc(R.dt(d.dimension))}</td><td class="r num">${k ? `${esc(k.v)}<small>${esc(String(k.u).split(' ')[0])}</small>` : '—'}</td><td class="r">${R.status(l)}</td></tr>`; }).join('')}</tbody></table>
      <p class="fine">点一行看明细。读数为各维度的关键数，单位见明细。</p></div>`;
  }

  /* ------------------------------------------------------------ 折线下:三个小栏目 */
  const story = (kick, head, body, more) => `<article class="story">${pp.kicker(kick)}<h3>${head}</h3>${body}${more ? `<p class="more">${more}</p>` : ''}</article>`;
  function storyWdr() {
    const cur = R.current('wdr', S.key);
    if (!cur) return story('负载 · WDR 分析', '本期无窗口分析', '<p>这个实例还没有 WDR 报告。在会话里对一个快照窗口做分析，下一期就会刊出。</p>');
    const d = cur.data, p = cur.prev && cur.prev.data, ins = W.insightFor(cur, d, p, pp.sortF(d.findings));
    return story('负载 · WDR 分析', ins.head, P.pairs([0, 5, 4].map((i) => W.KPIS[i]), d, p, W.kpiVal, W.showK, true) + pp.paras(ins.lines.slice(0, 1)), pp.jump('sec-wdr', '八项指标对照见 C1 版'));
  }
  function storyTop() {
    const t = T.data();
    if (!t) return story('慢语句 · Top SQL', '本期无 Top SQL 榜单', '<p>这个实例还没有 Top SQL 报告。在会话里取一次，下一期就会刊出。</p>');
    const ins = T.insightFor(t), b = pp.bizTop(t);
    return story('慢语句 · Top SQL', ins.head, P.stack(t.rep.rows || [], t.tune, b && b.sql_id) + pp.paras(ins.lines.slice(1, 2)), pp.jump('sec-topsql', '完整榜单与调优札记见 B1 版'));
  }
  function storyKb() {
    const m = K.model(), ins = K.insightFor(m);
    return story('知识 · 知识库', ins.head, P.bars(m.misses.map((x) => [x.code, x.n, '次', '查不到案例']), true) + pp.paras(ins.lines.slice(1, 2)), pp.jump('sec-kb', '案例摘编与检索记录见 D1 版'));
  }

  /* ------------------------------------------------------------ 整份报纸 */
  function paper() {
    return `<section class="front"><div class="front-main">${lead()}</div><aside class="front-side">${numbers()}${dimsBox()}</aside></section>
      <div class="fold"><span>折线以下</span></div>
      <section class="below">${storyWdr()}${storyTop()}${storyKb()}</section>${R.sections()}
      <footer class="colophon"><b>运维日报</b>　由 GaussDB 智能体依据巡检、WDR、Top SQL 与知识库报告自动编排。级别由巡检脚本按阈值判定，模型不改；文中数字均取自报告。<br>本期为设计原型，数据来自样例报告；空心点与「示意」字样的条目为原型补充。</footer>`;
  }
  Object.keys(R.pages).forEach((k) => { R.pages[k].render = paper; });

  function masthead() {
    const cur = R.current('health', S.key), at = cur ? cur.entry.at : (D.targets.find((x) => x.key === S.key) || {}).last_at;
    $('#issue-date').textContent = at ? pp.fullDate(at) : '';
    $('#issue-no').textContent = cur ? `第 ${cur.pos + 1} 期` : '创刊前';
  }
  const goto = (id, smooth) => { const el = id && document.getElementById(id); window.scrollTo({ top: el ? el.getBoundingClientRect().top + window.scrollY - 64 : 0, behavior: smooth ? 'smooth' : 'auto' }); };
  R.afterRender = masthead;
  R.afterRoute = () => goto(S.page === 'health' ? null : 'sec-' + S.page, false);
  document.addEventListener('click', (ev) => {
    const el = ev.target.closest('[data-jump]');
    if (!el) return;
    ev.preventDefault();
    goto(el.dataset.jump, true);
  });
})(window.R6);
