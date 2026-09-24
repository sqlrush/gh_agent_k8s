// 取数层。所有路径以 /reports/ 开头:网关按请求头里的工号把它们路由到**这个人自己的** runtime Pod
// 的 4097 端口。路径里没有工号 —— 同一份代码,不同的人跑,拿到的是各自的报告。
//
// 返回形状统一:{ok:true, data} | {ok:false, status, error}。404 是常态(还没跑过某个技能),
// 页面据此画引导卡,不画空表。

const OPTS = { credentials: 'same-origin', cache: 'no-store' };

export async function fetchJson(path) {
  let r;
  try {
    r = await fetch(path, OPTS);
  } catch (e) {
    return { ok: false, status: 0, error: String(e && e.message || e) };
  }
  if (!r.ok) return { ok: false, status: r.status, error: `HTTP ${r.status}` };
  try {
    return { ok: true, data: await r.json() };
  } catch (e) {
    return { ok: false, status: r.status, error: '不是合法的 JSON' };
  }
}

/** queries.jsonl:一行一个 JSON,坏行跳过不拖累整页。 */
export async function fetchJsonl(path) {
  let r;
  try {
    r = await fetch(path, OPTS);
  } catch (e) {
    return { ok: false, status: 0, error: String(e && e.message || e) };
  }
  if (!r.ok) return { ok: false, status: r.status, error: `HTTP ${r.status}` };
  const text = await r.text();
  const rows = [];
  for (const line of text.split('\n')) {
    const t = line.trim();
    if (!t) continue;
    try { rows.push(JSON.parse(t)); } catch (_) { /* 坏行跳过 */ }
  }
  return { ok: true, data: rows };
}

export const fetchWhoami = () => fetchJson('/reports/whoami.json');
export const fetchTargets = (skill) => fetchJson(`/reports/${skill}/targets.json`);
export const fetchLatest = (skill, key) => fetchJson(`/reports/${skill}/${key}/latest.json`);
export const fetchIndex = (skill, key) => fetchJson(`/reports/${skill}/${key}/index.json`);
export const fetchReport = (skill, key, file) => fetchJson(`/reports/${skill}/${key}/${file}`);
export const fetchSqltune = (key, sqlId) => fetchJson(`/reports/sqltune/${key}/${encodeURIComponent(sqlId)}.json`);
export const fetchKbHealth = () => fetchJson('/reports/kb/health.json');
export const fetchKbQueries = () => fetchJsonl('/reports/kb/queries.jsonl');
// 知识库目录(案例 / 条款 / 关系图三个页签):由用户 Pod 从共享知识库现读现算,不是报告存档
export const fetchKbCatalog = () => fetchJson('/reports/_kb/catalog.json');
