# 后端镜像的环境契约（给平台）

镜像：`gaussdb-agent-runtime`（每用户一个 Pod）、`gaussdb-agent-kb-import`（知识库管理员）。两者的接口相同，差异见末尾。

## 环境变量

| 变量 | 必填 | 值 | 来源 | 说明 |
|---|---|---|---|---|
| `GSDB_USER_ID` | 是 | 工号 | 平台 | 写进 findings 信封与 health 报告的「执行人」；配了 `GRMP_USER_ID_*` 时也是发给中间件的那个工号（**同一个来源**，两处取值不一致比不传更糟） |
| `POD_NAME` | 建议 | Pod 名 | downward API `metadata.name` | `.owner` 独占标记里的持有者；缺省用 `hostname` |
| `GRMP_API_HOST` | runtime 是 | 中间件主机名或 IP | ConfigMap | 每次启动重新渲染进 `config.yaml`，改地址重建 Pod 即生效 |
| `GRMP_API_PORT` | 否 | 默认 `8080` | ConfigMap | |
| `GRMP_AUTH_TOKEN` | runtime 是 | 本人令牌 | Secret，按人 | 只存在环境变量里；镜像与 NAS 都不落盘 |
| `GRMP_APPKEY` | 中间件开了签名校验时是 | 与中间件约定的应用名（整个智能体一个） | ConfigMap | 2026-09-18 中间件加固：请求头再带 Appkey / Timestamp / Signature；为空 = 不签名 |
| `GRMP_SIGN_MODE` | 否 | `full`（默认，三个头）/ `headers-only`（只 Appkey + Timestamp） | ConfigMap | 2026-09-20 客户过渡期：密钥审批未下来，先只加 Appkey 与 Timestamp。`headers-only` 时不读私钥。**拿不到私钥不会自动降级**——降级必须显式配置，否则生产上 Secret 挂载失败会悄悄变成不签名 |
| `GRMP_SM2_PRIVATE_KEY` | `GRMP_APPKEY` 非空且 `GRMP_SIGN_MODE=full` 时是 | 该 Appkey 的 SM2 私钥（64 位 hex 或 PEM），客户签发 | Secret `grmp-sm2`，全体 runtime 共用 | 模板里 `optional: true`；该配而拿不到时 Pod 启动即失败并提示可改用 `headers-only`。kb-import 不需要 |
| `GRMP_SIGN_USER_ID` / `GRMP_SIGN_TIMESTAMP` / `GRMP_SIGN_FORMAT` / `GRMP_SIGN_ENCODING` / `GRMP_SIGN_PAYLOAD` | 否 | 签名旋钮：userId（默认国标 `1234567812345678`，OpenSSL 默认是空串）、`ms`/`s`、`raw`/`der`、`hex`/`base64`、原文模板（默认 `{path}+{timestamp}`） | ConfigMap | 按中间件口径填；值不认识时建连接即报错 |
| `GRMP_USER_ID_HEADER` / `GRMP_USER_ID_PARAM` | 中间件要收工号时填其一 | 请求头名（如 `X-User-Id`）/ 报文顶层键名（如 `userId`） | ConfigMap | 2026-09-20 客户确认要显式收调用人工号，但字段名与位置未给，故两处都做成开关。值取自 `GSDB_USER_ID`。两个都为空 = 不发工号（行为与加此特性前一致）。**填了字段名而 `GSDB_USER_ID` 为空时建连接即报错，绝不发空工号** |
| `MODEL_BASE_URL` | 是 | 模型服务的 OpenAI 兼容地址（含 `/v1`） | ConfigMap | |
| `MODEL_API_KEY` | 是 | 模型服务密钥 | Secret | 渲染进 `/data/oc/opencode.json`（0600，emptyDir，随 Pod 消失） |
| `MODEL_ID` | 是 | 模型 id | ConfigMap | opencode 默认模型 = `<MODEL_PROVIDER_NAME>/<MODEL_ID>` |
| `MODEL_PROVIDER_NAME` | 否 | 默认 `model-service` | ConfigMap | |
| `OPENCODE_SERVER_PASSWORD` | 建议 | 本 Pod 随机口令 | 网关生成 | opencode 自带的 HTTP 基本认证；配套 `OPENCODE_SERVER_USERNAME`（默认 `opencode`） |
| `OPENCODE_PORT` | 否 | 默认 `4096` | — | |
| `NAS_ME` / `NAS_KB` | 否 | 默认 `/nas/me`、`/nas/kb` | — | 挂载点变了才需要改 |
| `GSDB_KB_INBOX` | kb-import 内置 | `/nas/kb/inbox/uploads` | 镜像 | 用户上传待导入文件的收件目录 |

entrypoint 自己设置、平台**不要**覆盖的：`XDG_DATA_HOME=$NAS_ME/xdg-data`、`XDG_STATE_HOME=$NAS_ME/xdg-state`、`XDG_CONFIG_HOME=/data/config`（启动时从镜像 `/opt/agent/config` 复制，因为 opencode 加载配置要往这里写 `.gitignore`）、`XDG_CACHE_HOME=/data/cache`（同理，从 `/opt/agent/cache` 复制）、`GSDB_HOME=$NAS_ME/gdaa`、`GSDB_KB_DIR=$NAS_KB`、`HOME=/data/home`、`OPENCODE_CONFIG=/data/oc/opencode.json`。

## 挂载

| 容器路径 | 来源 | 权限 | 内容 |
|---|---|---|---|
| `/nas/me` | NAS PVC，`subPath: users/<工号>`（kb-import 用 `admins/<工号>`） | 读写 | `xdg-data/opencode/opencode.db`（会话、对话、压缩摘要）、`xdg-state/`、`workspace/`（上传文件、报告）、`gdaa/`（登录会话、每次启动重生成的 `config.yaml`）、`backup/`（退出备份，3 份）、`.owner` |
| `/nas/kb` | NAS PVC，`subPath: kb` | runtime **只读**；kb-import 读写 | 知识库文件；`inbox/uploads/` 是管理员上传待导入文件的收件目录（网关写入） |

用户上传的文件由**网关**直接写进 NAS（普通用户 `users/<工号>/workspace/uploads/`，管理员另有 `kb/inbox/uploads/`），Pod 只读到结果；镜像不提供上传接口。
| `/data` | emptyDir | 读写 | 渲染出的配置、`HOME`、`env.sh`；随 Pod 消失 |

NAS 目录对 uid 1000 可写（镜像以 `agent`，uid 1000 运行）。NFS 要求见 spec §4：`nfsvers=4.1`、`hard`、`local_lock=all`。

## 端口与探针

- 容器端口 `4096`（HTTP，`opencode serve`）。
- 就绪 / 存活：`GET /global/health` → `{"healthy":true,"version":"1.18.27"}`。镜像自带 `HEALTHCHECK` 用同一路径。

## 停机

`terminationGracePeriodSeconds: 60`。收到 SIGTERM 后 entrypoint 依次：等 opencode 退出 → `PRAGMA wal_checkpoint(TRUNCATE)` → 复制 `opencode.db` 到 `backup/`（保留 3 份）→ 删 `.owner`。网关先摘流量再删 Pod（`preStop: sleep 5`）。

## 启动时发生什么（日志里能看到）

1. 建目录；渲染 `/data/oc/opencode.json` 与 `$GSDB_HOME/config.yaml`（配置不是状态，每次重生成）。
2. `.owner` 独占：另一个 Pod 90 秒内刷新过标记 → 最多等 55 秒 → 仍被占则退出 1，不打开 db。
3. `opencode.db` 完整性自检：损坏 → 改名留证 → 从 `backup/` 最新一份恢复；没有备份 → 空库启动。每种情况都写日志，不静默。
4. 删 7 天前的 `log/*.log`。
5. 首次启动把 `/nas/me/workspace` 初始化成 git 仓库（空提交），opencode 以此识别「项目」，Web 界面按项目列会话。
6. `opencode serve --hostname 0.0.0.0 --port 4096`，工作目录 `/nas/me/workspace`。`serve` 自带 Web 界面（`/`）与 HTTP API（`/doc` 是 OpenAPI）。

## 镜像里没有的东西

令牌、密钥、客户地址、`auth.json`、任何会话数据。`share` 在渲染的配置里固定为 `disabled`。

## 两个镜像的差异

| | runtime | kb-import |
|---|---|---|
| skills | 16 个诊断 skill + `gaussdb-kb`（查询） | `gaussdb-kb` + `gaussdb-kb-import` + `gaussdb-kb-init`（入库前标准化） |
| `/nas/kb` | 只读 | 读写 |
| `GRMP_API_HOST` / `GRMP_AUTH_TOKEN` | 必填 | 不需要（给个占位值即可，不接触数据库） |
| 出站网络 | 中间件、模型服务、DNS | 模型服务、DNS |
| 谁能拿到 | 所有用户 | 知识库管理员 |
