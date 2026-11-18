// 资源大盘(运维管理员):取数与建模。数据只有一个来源 —— 网关的 /dash/api/resources(只给运维管理员,网关缓存 20 秒),
// 这里每 30 秒刷新一次。哪一块后端标了 available:false 就照它给的原因显示,不补数、不猜。
// 「智能体解读」按数据规则写成,不调模型;建议问题点了带着问题去对话页(data-dig)。

import { fetchResources } from '../lib/data.js';

export const REFRESH_MS = 30000;
export const BUSY_S = 300;                      // 最近这么多秒内有交互算「在用」,否则「闲置」(等清扫器收尾暂停)
export const KIND = { runtime: '诊断', 'kb-import': '导入' };
export const STATE = { use: '运行·在用', idle: '运行·闲置', pause: '暂停', nosync: '暂停·没回写成', other: '启动中 / 其他', free: '空余' };

export async function load() {
  const r = await fetchResources();
  if (!r.ok) return { ok: false, status: r.status, error: r.status === 403 ? '资源大盘只给运维管理员看。' : r.error || '读取失败' };
  return model(r.data, Date.now() / 1000);
}

const avail = (b) => !!(b && b.available !== false);

/** 一台沙箱的显示状态:运行中按最近交互分「在用 / 闲置」;暂停按暂停前回写成没成分两种。 */
export function stateOf(s, now) {
  if (s.state === 'Running') return now - (+s.last_active || 0) < BUSY_S ? 'use' : 'idle';
  if (s.state === 'Paused') return s.synced_before_pause === false ? 'nosync' : 'pause';
  return 'other';
}

function groupsOf(d, sbx) {
  const plat = d.platform;
  if (!avail(plat)) return null;
  return (plat.groups || []).map((g) => {
    if (g.missing) return { ...g, cells: [], pods: [] };
    const mine = sbx.filter((s) => (s.agent_pod_group ? s.agent_pod_group === g.name : g.kinds.includes(s.kind)));
    const byId = Object.fromEntries(mine.map((s) => [s.id, s]));
    const pods = (g.pods || []).map((p) => ({ name: p.name, short: shortPod(p.name), sbx: p.sandboxes.map((id) => byId[id]).filter(Boolean) }));
    const anon = Math.max(0, (g.agent_pods || 0) - pods.length);
    for (let i = 0; i < anon; i++) pods.push({ name: '', short: '空闲承载 Pod', sbx: [] });
    const placed = new Set(pods.flatMap((p) => p.sbx.map((s) => s.id)));
    const c = { use: 0, idle: 0, pause: 0, nosync: 0, other: 0, old: 0 };
    mine.forEach((s) => { c[s.view] += 1; if (s.outdated) c.old += 1; });
    const occ = mine.length;
    return { ...g, pods, unplaced: mine.filter((s) => !placed.has(s.id)), counts: c, occupied: occ, free: Math.max(0, (g.capacity || 0) - occ) };
  });
}

export const shortPod = (name) => { const m = /-(\d+-\d{3})$/.exec(name || ''); return m ? `pod-${m[1]}` : name || ''; };

export function model(d, now) {
  const fy = d.fuyao, ops = d.ops;
  const reqBy = {};
  if (avail(ops)) (ops.requests.by_user || []).forEach((r) => { reqBy[`${r.user_id}|${r.kind}`] = r.count; });
  const plat = avail(d.platform) ? d.platform.sandboxes || {} : {};
  const sbx = avail(fy) ? (fy.sandboxes || []).map((s) => {
    const p = plat[s.sandbox] || null;
    return { id: s.sandbox, user: s.user_id, kind: s.kind, state: s.state, view: stateOf(s, now), template: s.template, outdated: !!s.outdated,
      last_active: s.last_active, paused_at: s.paused_at, synced: s.synced_before_pause, retain_left: s.paused_at && fy.policy ? s.paused_at + fy.policy.retain_seconds - now : null,
      agent_pod: p ? p.agent_pod : '', agent_pod_group: p ? p.group : '', phase: p ? p.phase : '', requests: reqBy[`${s.user_id}|${s.kind}`] || 0 };
  }) : [];
  const groups = groupsOf(d, sbx);
  const t = { use: 0, idle: 0, pause: 0, nosync: 0, other: 0, old: 0 };
  sbx.forEach((s) => { t[s.view] += 1; if (s.outdated) t.old += 1; });
  const cap = groups ? groups.reduce((a, g) => a + (g.capacity || 0), 0) : null;
  const capMax = groups ? groups.reduce((a, g) => a + (g.capacity_max || 0), 0) : null;
  const comps = avail(d.components) ? d.components.items : [];
  const reserve = reserveOf(comps, groups);
  const m = { d, now, fy, ops, sbx, groups, totals: { ...t, run: t.use + t.idle, paused: t.pause + t.nosync, occupied: sbx.length, cap, capMax,
    free: cap === null ? null : Math.max(0, cap - sbx.length) }, comps, reserve, fuyao: d.mode === 'fuyao' };
  m.insight = insight(m);
  m.sources = sources(d);
  return m;
}

function reserveOf(comps, groups) {
  let cpu = 0, mem = 0, known = true;
  comps.forEach((c) => {
    const r = c.per_replica && c.per_replica.requests;
    if (!r || r.cpu === null || r.mem === null) { known = false; return; }
    cpu += r.cpu * c.replicas; mem += r.mem * c.replicas;
  });
  (groups || []).forEach((g) => {
    if (g.missing || !g.agent_reserve) return;
    cpu += (g.agent_reserve.requests.cpu + 0.1) * g.agent_pods;            // 每个承载 Pod 另有平台代理 100m / 128Mi(实测)
    mem += (g.agent_reserve.requests.mem + 128) * g.agent_pods;
  });
  return { cpu, mem, known, withPlatform: !!groups };
}

// ---------------------------------------------------------------- 智能体解读(规则,不调模型)

const pct = (a, b) => (b ? Math.round((a / b) * 100) : 0);

export function insight(m) {
  const t = m.totals, fy = m.fy, lines = [], chips = [];
  if (!m.fuyao) {
    return { head: `当前不是 Fuyao 模式(运行模式 ${m.d.mode}),沙箱与承载 Pod 的视图不适用`,
      lines: ['下面只列本命名空间的组件与资源配置;固定池化的槽位请看运维接口 /admin/pool。'], chips: [['资源大盘怎么看', '请介绍一下大盘的资源大盘页怎么看。']] };
  }
  const pol = (fy && fy.policy) || {};
  const days = pol.retain_seconds ? Math.round(pol.retain_seconds / 86400) : null, idleMin = pol.idle_seconds ? Math.round(pol.idle_seconds / 60) : null;
  let head;
  if (t.cap === null) head = `共 ${t.occupied} 台沙箱:运行 ${t.run}、暂停 ${t.paused};容量要读平台的沙箱组(需授权只读)`;
  else if (t.free === 0 && t.cap > 0) head = `容量 ${t.cap} 台已满:运行 ${t.run}、暂停 ${t.paused},新工号进来会看到等待页`;
  else head = `${t.cap} 台容量已占 ${t.occupied} 台:运行 ${t.run}、暂停 ${t.paused},还能再进 ${t.free} 人`;
  const run = (m.groups || []).find((g) => !g.missing && g.kinds.includes('runtime'));
  if (run && run.pod_max > run.agent_pods) head += `;诊断组可从 ${run.agent_pods} 个承载 Pod 扩到 ${run.pod_max} 个(${run.capacity} → ${run.capacity_max} 台)`;
  else if (run && t.free === 0) head += `;诊断组已到上限(最大 ${run.pod_max} 个承载 Pod)`;
  if (t.paused) lines.push(`<b>暂停的沙箱占了${t.cap ? `容量的 ${pct(t.paused, t.cap)}%` : ` ${t.paused} 台`}</b>:保留期${days ? ` ${days} 天` : ''}内本人回来直接恢复,代价是容量要按「保留期内用过的人数」规划,而不是在线人数。`);
  if (t.idle) lines.push(`<b>${t.idle} 台运行·闲置</b>(${BUSY_S / 60} 分钟内没有交互),闲置满 ${idleMin || '—'} 分钟会先收尾回写再暂停。`);
  if (t.old) lines.push(`<b>${t.old} 台仍是旧版模板</b>(当前 ${esc((fy.templates || {}).runtime || '')}),闲置后自动换新,在用的不打断。`);
  const stuck = fy ? fy.unsynced_over_retention || 0 : 0;
  if (stuck) lines.push(`<b>${stuck} 台暂停前没回写成且已过保留期</b>,网关没有自动删除(删了会丢最后一次成功同步之后的改动),需运维先恢复再删除,或确认可丢后强制删除。`);
  else if (t.nosync) lines.push(`<b>${t.nosync} 台暂停前没回写成</b>,清扫器不会自动删除它们。`);
  if (avail(m.ops)) {
    const top = (m.ops.requests.by_user || [])[0];
    lines.push(`今天 <b>${m.ops.requests.users} 人</b>共 ${m.ops.requests.total} 次交互请求${top ? `,最多的是 ${esc(top.user_id)}(${KIND[top.kind] || top.kind},${top.count} 次)` : ''}。`);
  }
  const gaps = [['metrics', '实际 CPU / 内存'], ['platform', '容量与承载 Pod'], ['ops', '请求计数与趋势']].filter(([k]) => !avail(m.d[k])).map(([, n]) => n);
  if (gaps.length) lines.push(`读不到的:${gaps.join('、')}(原因见各块的标签),这些地方不显示数字。`);
  if (!lines.length) lines.push('目前没有沙箱。第一个人发起交互时网关才为他建沙箱。');
  chips.push(['扩到 2000 人要多少承载 Pod', `资源大盘显示${run ? `诊断组 ${run.agent_pods} 个承载 Pod × 每 Pod ${run.per_pod} 台、最多 ${run.pod_max} 个` : '沙箱组容量读不到'},现在 ${t.occupied} 人有沙箱。按现在的暂停比例,扩到 2000 人需要多少承载 Pod 和节点?`]);
  if (t.paused) chips.push(['保留期改短能腾出多少容量', `资源大盘里暂停的沙箱有 ${t.paused} 台${t.cap ? `,占容量 ${pct(t.paused, t.cap)}%` : ''},保留期 ${days || '—'} 天。保留期改成 3 天大概能腾出多少容量?代价是什么?`]);
  if (stuck || t.nosync) chips.push([`那 ${stuck || t.nosync} 台没回写成的怎么处理`, '资源大盘里有暂停前没回写成的沙箱。怎么安全地处理它们?']);
  return { head, lines, chips, src: '依据 /dash/api/resources · 规则生成', ask: '问资源大盘,比如:按现在的暂停比例,保留期设几天合适?' };
}

/** 数据来源表:每一块从哪里来、现在能不能取到(直接用后端给的 available / reason)。 */
function sources(d) {
  const row = (what, from, b) => ({ what, from, ok: avail(b), why: avail(b) ? '' : (b && b.reason) || '不可用' });
  return [
    row('沙箱列表、状态、工号、模板、旧版、暂停前回写', '网关清扫器(/admin/fuyao 同一份)', d.fuyao),
    row('沙箱组容量、承载 Pod 数与上下限、沙箱所在承载 Pod', '平台 sandbox.flux.io(只读)', d.platform),
    row('组件副本、就绪、request / limit', 'K8s API(本命名空间)', d.components),
    row('标准知识库安装等 Job', 'K8s API(本命名空间)', d.jobs),
    row('实际 CPU / 内存', 'metrics.k8s.io(metrics-server)', d.metrics),
    row('今天各工号请求次数、24 小时趋势、NAS 用量', '状态服务(网关上报)', d.ops),
    row('节点容量', 'K8s API(集群级 nodes)', d.nodes),
    row('token 用量', '交互耗时报告汇总', d.tokens),
  ];
}

function esc(v) { return String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

/** 普通风格的资源大盘页。 */
export async function render(root, ctx) {
  const view = await import('../views/normal/resources.js');
  return view.render(root, ctx, load);
}
