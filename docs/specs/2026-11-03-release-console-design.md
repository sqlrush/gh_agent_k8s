# 发版台设计(2026-11-03)

界面与已拍板的决定见 `docs/prototypes/release-r1/`(R1 原型 + README 8 条);池子在线替换的实验与算法见
`docs/specs/2026-11-03-release-pool-swap-experiment.md`。本文写**怎么实现**:边界、发布包格式、release.yaml、
各步骤的做法、状态与接口、测试方案。范围 = 方案里的 P0 + P1(白名单只核对、池子按槽位在线替换、不做登录)。

## 1. 边界

- **形态**:一个容器镜像 `gaussdb-agent-release`,跑在跳板机上,一条命令起来:
  ```
  docker run -d --name gaussdb-release -p 127.0.0.1:8090:8090 \
    -v /data/releases:/releases:ro -v /data/release-state:/state -v ~/.kube/config:/kube/config:ro \
    gaussdb-agent-release:<版本>
  ```
  `/releases` = 发布包目录(把离线包原样拷进来);`/state` = 发版台自己的状态(SQLite、渲染出的清单、日志);
  kubeconfig 只读挂进来。默认只监听本机;不做登录(封闭环境、只给运维)。
- **技术**:Python 3.12 标准库 + PyYAML(与网关同),`kubectl` 二进制随镜像带(kubeconfig 的各种认证方式、`kustomize`、
  `diff`、`exec` 都靠它),SQLite 存状态。前端是静态文件(R1 原型改成读接口),不需要构建。
- **代码位置**:本仓库 `release/`(Python 包,`python3 -m release` 启动),测试在 `release/tests/`;
  镜像 `docker/Dockerfile.release`。联合仓库同步同一份。
- **发版台不做的事**:不建命名空间以外的集群级对象(PV、命名空间本身由平台团队按发版台给出的命令执行一次,发版台核对);
  不执行白名单 SQL;不改客户业务库;不管用户自己的 Deployment(那是网关用 server-side apply 管的,`fieldManager=gaussdb-agent-gateway`)。

## 2. 发布包与 release.yaml

### 2.1 现有离线包(package-images.sh 产出,v1.4.3 实物核对过)

```
gaussdb-agent-<ver>-oc<oc>/
  MANIFEST.txt
  gaussdb-agent-<ver>-oc<oc>-amd64.tar.gz (+ .sha256)    5 个镜像,OCI 目录格式(oci-layout / index.json / blobs/)
  gaussdb-agent-<ver>-oc<oc>-arm64.tar.gz (+ .sha256)    打包机本架构的标签不带后缀,另一架构带 -<arch>
  gaussdb-agent-<ver>-oc<oc>-k8s.tar.gz   (+ .sha256)    顶层直接是 k8s/ 与 docs/
```
镜像:runtime、kb-import、gateway、frontend(联合仓库形态没有)、kbstd。每个命名条目是单架构 index(平台清单 + buildx 证明清单)。

### 2.2 新增 release.yaml(打包时生成,放在包目录里)

仓库里新增 `k8s/release/changes.yaml`:**人写**的逐版本变化(从 agent-v1.3.2 写起,与 `docs/版本说明.md` 同步,有守卫测试)。
`package-images.sh` 打包时把它与本次的事实(标签、提交、各镜像各架构的清单 digest、文件 sha256、白名单清单)合成 `release.yaml`:

```yaml
schema: 1
version: agent-v1.4.4            # 不含 -oc 后缀
tag: agent-v1.4.4-oc1.18.27
skills: skills-v12.23
commit: 1a2b3c4
packed: 2026-11-10
files:                           # 与 .sha256 一致;发版台先核 sha256 再读别的
  k8s: gaussdb-agent-v1.4.4-oc1.18.27-k8s.tar.gz
  images: {amd64: …-amd64.tar.gz, arm64: …-arm64.tar.gz}
images:                          # 每个镜像在各架构包里的条目名与平台清单 digest
  runtime:  {amd64: {ref: "gaussdb-agent-runtime:agent-v1.4.4-oc1.18.27-amd64", digest: "sha256:…"}, arm64: {…}}
  …
whitelist: {file: k8s/release/whitelist.json, count: 130}   # 在 k8s 包里:每条脚本名 + 内容指纹
history:                         # 新的在前,累计到最早支持升级的版本
  - version: agent-v1.4.4
    mixed_with: [agent-v1.4.3]   # 可与这些版本混跑 → 池子按槽位在线替换;不在里面的按 Pod 整体替换
    config:                      # 本版本新增 / 改变的配置项
      - {map: agent-config, key: EXAMPLE_KEY, required: false, default: ""}
    whitelist: {added: [health.x, …], changed: []}
    steps: []                    # 特有步骤:rebuild_kb_import / apply_policy:kbstd-policy / remove_vectordb / kb_migrate
  - version: agent-v1.4.3
    …
```

**升级计划 = 当前版本到目标版本之间各版本 history 的并集**:配置项取并集(后版本覆盖前版本的默认值与是否必填),
白名单取目标版本的全量清单核对(不按增量),特有步骤按版本顺序去重,`mixed_with` 只看目标版本那一条是否包含当前版本。
从 agent-v1.4.0 起的历史要补写进 changes.yaml(§14 升级路径、附录 B 已有全部信息)。

### 2.3 镜像入库(不依赖 Docker)

读架构包(gzip 流式解 tar):`index.json` 按 `io.containerd.image.name` 找到五个镜像 → 取 `linux/<arch>` 的平台清单 →
对内网仓库走 Registry HTTP API v2:逐个 blob `HEAD` 查在不在,不在就 `POST` + `PUT` 单次上传(大 blob 分块 `PATCH`),
最后 `PUT /v2/<名>/manifests/<标签>`。**只推平台清单**(不推带证明清单的 index):客户仓库对 `unknown/unknown` 平台的兼容性
参差不齐,推平台清单最稳;推完 `HEAD` 回读 digest 必须等于 release.yaml 里记的那个。标签去掉 `-<arch>` 后缀。
推之前先核对清单引用的每个 blob 都在包里(v1.1.5 出现过缺 blob 的包)。认证:Basic / Bearer token(`WWW-Authenticate` 握手)。
只推节点架构对应的那一个包(资源扫描时读节点 `kubernetes.io/arch`;混合架构集群两个都推、再建多架构 index —— 第一版不做,扫描时报出)。

## 3. 资源扫描(开跑前的关卡)

| 资源 | 检查 | 失败时提示 |
|---|---|---|
| 发布包目录 | 可读;发现的包 sha256 全对、release.yaml 可解析 | 哪个文件不对 |
| 内网镜像仓库 | `/v2/` 握手 + 认证 + 试推一个 1 字节 blob 后删除(仓库不支持删就只核对推送权限) | 认证 / 证书 / 权限 |
| Kubernetes | `kubectl auth can-i` 逐条核对 §4 需要的权限;节点架构;NetworkPolicy 是否被 CNI 执行(第一版只提示) | 缺哪条权限 |
| NAS | PVC `Bound`;用临时 Pod(uid 1000)在 `users/` 写读删一个文件 | 属主 / 只读 / 未绑定 |
| 模型 | 从集群内(临时 Pod)请求 `<MODEL_BASE_URL>/models` 或一次最小补全 | 不通 / 401 |
| 中间件 | 从集群内连 `GRMP_API_HOST:PORT`(TCP)+ 一次签名的无副作用请求 | 不通 / 签名不对 |
| 白名单核对数据源 | 只读账号能查 `script_config`,或已上传导出文件 | 见 §6 |
| 统一认证 | 用信任凭据模拟一次登录(冒烟账号),网关 200 | 凭据不对 |
| 集群余量 | 节点可分配内存 ≥ 替换期间多出来的那一个容器 + 余量 | 差多少 |

首次部署时 Kubernetes 一项改为核对命名空间与 PV 是否已由平台团队建好(没有就给出命令)。

## 4. 配置与渲染(「配置迁移」)

**`kubectl apply -k k8s/base` 不能直接用**:它把三个 ConfigMap 重置为清单里的值(连 `agent-roles` 的知识库管理员名单一起清空)、
把网络策略的网段改回占位值。发版台的做法:

1. 读包里的清单:`kubectl kustomize k8s/base` 得到对象列表;`render-pool.py`(包里带)按配置渲染 pool.yaml。
2. **ConfigMap**:目标值 = 集群里现在的值(保留运维改过的一切)+ 本次计划里新增的配置项(有资源配置里填的就用填的,
   否则用默认值;`required` 且没填 → 开跑前就拦下,不进流程)。`agent-roles` 只增不改。
3. **镜像**:所有 `image:` 加内网仓库前缀、换成目标标签;`USER_IMAGE_REGISTRY` / `USER_IMAGE_TAG` 同步改(网关与用户镜像同版本)。
4. **网络策略**:apiserver 地址、中间件网段与端口、模型网段与端口取自资源配置(首次部署时由扫描读出 apiserver endpoints)。
5. **Secret 不进渲染**:发版台只核对存在与键齐全;首次部署时用资源配置里填的值创建(值写进集群后发版台不留存,只记指纹与时间)。
6. 每次渲染记一个**配置版本**(资源配置 + 渲染结果的快照),回滚就是回到上一个配置版本。
7. 变更预览 = `kubectl diff -f <渲染结果>`(不含 Secret、不含用户 Deployment)。应用 = `kubectl apply -f`(客户端 apply,与手工做法一致,不 prune)。

## 5. 步骤

| 步 | 升级 | 首次部署 |
|---|---|---|
| 读包与校验 | sha256、release.yaml、清单 digest 与包内一致 | 同 |
| 生成升级计划 | 识别当前版本(§5.1)、累计 history | 当前版本 = 无 |
| 镜像入库 | §2.3 | 同 |
| 配置迁移 | §4 | 渲染全量 + 建 Secret |
| 变更预览 · 确认 | diff,运维确认 | 同(全是新增) |
| 白名单核对 | §6 | 同 |
| 网关滚动更新 | 应用 gateway-config + Deployment,`rollout status` | 部署基础资源(ConfigMap、网络策略、网关、前端、PVC) |
| 池子在线替换 | 实验记录里的算法;不可混跑时按 Pod 整体替换 | 应用 pool.yaml,等 Pod 就绪、槽位全部空闲 |
| 导入环境 | 计划要求时删 `role=kb-import` 的 Deployment(NAS 不动) | 跳过 |
| 知识库标准包 | 应用新 Job,等 Completed,读日志 `[installed]` / `[skipped]`,删旧 Job | 同 |
| 冒烟验证 | §7 | 同 |
| 完成 | 记当前版本与配置版本、发布报告 | 同 |

### 5.1 识别当前版本

`gateway-config.USER_IMAGE_TAG`、`deploy/gateway` 镜像标签、池子每个容器的 spec 镜像。三者一致 → 当前版本;不一致 → 上次发布没走完,
计划从「池子在线替换」接着做(只换还没换的容器)。识别结果与发版台自己记的「当前版本」对不上时提示,以集群为准。

### 5.2 执行引擎

- 一次发布 = 一行 run + 每步一行 step + 日志行,全在 SQLite;步骤实现**幂等**(重跑只做没做完的部分)。
- 关卡(变更确认、白名单未通过、请求长时间不结束)是持久化的等待状态,发版台重启后照样停在那里。
- 失败 → 停在这一步,运维选「从这一步续跑」或「回滚」。发版台重启时正在跑的步骤标为「被中断」,同样由运维选。
- **回滚** = 以上一个版本为目标、上一个配置版本为配置,走同一套步骤(镜像已在仓库,白名单只增不删不用动,知识库标准包跳过)。
- 每个动作写操作记录;日志与操作记录里不出现任何密钥的值。

## 6. 白名单核对

**运行时只按脚本名找脚本**(`common/grmp/client.py`:先拉列表建 `{cmd_name: id}`,再按 id 调用),不比对正文 ——
正文不一致时**静默执行旧 SQL**,参数定义不一致时中间件拒绝调用。所以「核对」必须由发版台按内容做。

- **应有清单**:交付给白名单团队的 `scripts.sql`(全量,v1.4.3 为 128 条;gh_skill 与联合仓库 `docs/delivery/`)。
  本仓库目前没有这份文件 → 同步 gh_skill 时一并带上 `docs/delivery/`(与联合仓库同),打包时解析成 `k8s/release/whitelist.json`:
  每条 `script_name`、`md5(script_content)`(存储正文,含末尾换行,与 `tools/delivery_delta.py::_stored` 同口径)、
  参数的 key 与 type(忽略 value / autoAcquire)。
- **已登记**:表 `grmp.script_config`,`scene='AGENT'`;正文列是 `script_content`(不是 script),参数在 `parameter_config`(JSON)。
  两种读法(资源配置里选):
  1. 只读账号直连(只授予 `SELECT grmp.script_config`):`SELECT script_name, md5(script_content), parameter_config, is_valid
     FROM grmp.script_config WHERE scene = 'AGENT'`;
  2. 导出文件:界面上给出同一条 SELECT,白名单团队执行后把结果(CSV)上传。
- **比对口径**(逐条):已登记且一致 / **未登记** / **正文不一致**(md5 不同)/ **参数不一致** / **重名**(同名多行)/ **未生效**(is_valid≠1)。
  只多不少的脚本(中间件里有、本版本不需要)只列出、不算失败。全部一致才放行;否则停在关卡上,可导出未成功清单。
- **测试环境**:mock 中间件存 SQLite(`~/kf-verify/gdaa/grmp/script_config.db`,表同名、无 uuid 列);端到端走「导出文件」那条路
  (从 SQLite 导出同格式 CSV),只读账号那条路用单元测试覆盖。`agent/grmp_middleware/fixtures/script_config.db` 已过期,不能用。

## 7. 冒烟验证

经网关走用户的真实路径。发版台用 `kubectl port-forward svc/gateway` 起一个本机端口(或资源配置里填的统一认证入口地址):

1. **模拟登录**:请求头 `X-Agent-User: <冒烟工号>` + `X-Agent-Trust: <TRUST_SECRET>`(共享密钥,不是签名;值在冒烟时从
   Secret `gateway-auth` 现读,只在内存里)。无信任头 → 必须 403;有 → 200。冒烟工号要有 `grmp-token-<工号>`(扫描时核对)。
2. **一次真实对话**:`POST /session {"title": "发版台冒烟 <版本>"}` → `POST /session/<id>/message`(同步,90 秒上限)。
   默认问一句只读的问题;资源配置里给了「冒烟实例」时,问对该实例做一次只读健康检查(经模型、技能、白名单、中间件全链路)。
   核对:有文字回答、`info.error` 为空、用了哪个技能。
3. **报告**:健康检查之后 `/reports/health/targets.json` 里该实例的 count 增加,`/reports/health/<key>/latest.json` 有 `overall`。
4. **知识库检索**:对话里请它查一次知识库(如「死锁」),核对 `/reports/kb/queries.jsonl` 多了一行且有命中。
冒烟会话留在冒烟工号的历史里(标题带版本),不删。

### 原型要更正的地方(实现前端时一并改)

- 镜像是 **5 个**(runtime、kb-import、gateway、frontend、kbstd);联合仓库形态 4 个(没有 frontend)。原型写成了 4 个。
- 白名单核对的 SELECT 里列名是 `script_content`,不是 `script`。
- 报告不在 `/nas/me/reports/*.md`,是 `reports/<技能>/<key>/<时间>.json`,经网关读 `/reports/...`。

## 8. 接口

`GET /api/overview` · `GET /api/packages` · `GET/PUT /api/config` · `POST /api/secrets/<名>`(只写)·
`POST /api/scan` · `GET /api/scan` · `POST /api/runs`(开始)· `GET /api/runs/<id>`(含步骤、子步骤、关卡)·
`GET /api/runs/<id>/logs?after=<序号>` · `POST /api/runs/<id>/{pause,resume,retry,rollback}` · `POST /api/runs/<id>/gate`(确认 / 重新核对 / 继续等 / 强制转保留)·
`GET /api/cluster`(大盘:网关副本、池子各槽位与管家、导入环境、Job)· `GET /api/history` · `GET /api/audit`。
前端每秒轮询 run 与 cluster(第一版不用 SSE)。大盘的槽位数据经 `kubectl exec` 进网关容器调 `/admin/pool`(管理密钥只在容器环境变量里)。

## 9. 测试

- 单元:release.yaml 合成与累计、计划生成、渲染(ConfigMap 保留现值、新增项、镜像前缀)、OCI 读取与推送(进程内假仓库)、
  池子替换算法(假集群:忙 / 不忙 / 写回不干净 / 核验超时 / 换管家)、引擎(关卡、中断、续跑、回滚)、接口。
- 端到端(OrbStack 测试环境):
  1. 本地起 `registry:2` 当内网仓库;
  2. **从零部署**到一个新命名空间(独立 NAS 目录,不碰 `gaussdb-agent`);
  3. 现有环境 **agent-v1.4.3 → agent-v1.4.4** 升级,期间 u9500 保持一个会话,换完答出暗号;
  4. 回滚一次。
