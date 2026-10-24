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

// ---------------------------------------------------------------- 索引里登记着、文件却不在
// 2026-10-24 测试环境:NAS 放在 Mac 的 /tmp 下,macOS 每天 0 点的 tmp_cleaner 把三天没碰过的报告文件删了;
// index.json 每次存档都重写,留了下来 —— 索引里登记着、文件 404,Top SQL 整页「报告读取失败 HTTP 404」。
// 文件被人手删、备份恢复不全也是同一个样子。所以取一份报告时:
// · 只在 404(文件不在)时往前退;网关 / 权限这类错误照常报错,不拿旧报告冒充;
// · 只退到「同一类」的上一份(same 由页面给:健康检查同一种口径、Top SQL 同一维度同一种榜);
// · 退过就在页面上说明(lib/missed.js),一份都取不到才报错(reason 'gone')。
const MAX_TRIES = 20;   // 被清掉的总是最老的一段,往前退一两份就到;封顶免得一路 404 下去

/** 想要的那份(file;空 = 索引里最新一份)及往前的同类条目,新的在前。file 不在索引里 → []。 */
export function chainFrom(idx, file, same = () => true) {
  const pos = file ? idx.findIndex((e) => e.file === file) : idx.length - 1;
  if (pos < 0) return [];
  const want = idx[pos];
  return [want, ...idx.slice(0, pos).reverse().filter((e) => same(e, want))];
}

/** 按顺序取第一份取得到的。missed0:已经知道取不到的条目(调用方先试过的那份)。返回
 *  {ok:true, rep, entry, missed} | {ok:false, reason:'gone', missed} | {ok:false, reason:'error', error}。 */
export async function fetchFirst(skill, key, cands, missed0 = []) {
  const missed = [...missed0];
  for (const e of cands.slice(0, MAX_TRIES)) {
    const rep = await fetchReport(skill, key, e.file);
    if (rep.ok) return { ok: true, rep, entry: e, missed };
    if (rep.status !== 404) return { ok: false, reason: 'error', error: rep.error };
    missed.push(e);
  }
  return { ok: false, reason: 'gone', missed };
}
export const fetchKbHealth = () => fetchJson('/reports/kb/health.json');
export const fetchKbQueries = () => fetchJsonl('/reports/kb/queries.jsonl');
// 知识库目录(案例 / 条款 / 关系图三个页签):由用户 Pod 从共享知识库现读现算,不是报告存档
export const fetchKbCatalog = () => fetchJson('/reports/_kb/catalog.json');
