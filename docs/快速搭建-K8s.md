# 快速搭建（K8s 测试环境）

按顺序执行。每条命令下面是**执行后应该看到的结果**。

前置：一台 Linux 跳板机，装好 `docker` 与 `kubectl`，能连到 K8s 集群。
需要准备的信息见最后一节「要填的值」。

---

## 1. 登录并拉镜像

```bash
docker login ghcr.io -u <你的GitHub用户名>
```
> 提示 Password 时粘贴令牌（令牌在 https://github.com/settings/tokens/new 生成，只勾 `read:packages`）。看到 `Login Succeeded`。

```bash
docker pull ghcr.io/sqlrush/gaussdb-agent-runtime:agent-v1.1.2-oc1.18.27
```

```bash
docker pull ghcr.io/sqlrush/gaussdb-agent-kb-import:agent-v1.1.2-oc1.18.27
```

```bash
docker pull ghcr.io/sqlrush/gaussdb-agent-gateway:agent-v1.1.2-oc1.18.27
```

```bash
docker pull ghcr.io/sqlrush/gaussdb-agent-frontend:agent-v1.1.2-oc1.18.27
```

```bash
docker pull opengauss/opengauss:7.0.0-RC1
```

```bash
docker images | grep -E "gaussdb-agent|opengauss"
```
> 5 行。

---

## 2. 取源码

```bash
git clone https://github.com/sqlrush/gh_agent_k8s.git && cd gh_agent_k8s
```
> 进入目录。**之后所有命令都在这里执行。**

---

## 3. 推到内网仓库

```bash
REG=<内网镜像仓库地址>
```

```bash
docker tag ghcr.io/sqlrush/gaussdb-agent-runtime:agent-v1.1.2-oc1.18.27 $REG/gaussdb-agent-runtime:agent-v1.1.2-oc1.18.27
docker tag ghcr.io/sqlrush/gaussdb-agent-kb-import:agent-v1.1.2-oc1.18.27 $REG/gaussdb-agent-kb-import:agent-v1.1.2-oc1.18.27
docker tag ghcr.io/sqlrush/gaussdb-agent-gateway:agent-v1.1.2-oc1.18.27 $REG/gaussdb-agent-gateway:agent-v1.1.2-oc1.18.27
docker tag ghcr.io/sqlrush/gaussdb-agent-frontend:agent-v1.1.2-oc1.18.27 $REG/gaussdb-agent-frontend:agent-v1.1.2-oc1.18.27
docker tag opengauss/opengauss:7.0.0-RC1 $REG/opengauss:7.0.0-RC1
```

```bash
docker push $REG/gaussdb-agent-runtime:agent-v1.1.2-oc1.18.27
docker push $REG/gaussdb-agent-kb-import:agent-v1.1.2-oc1.18.27
docker push $REG/gaussdb-agent-gateway:agent-v1.1.2-oc1.18.27
docker push $REG/gaussdb-agent-frontend:agent-v1.1.2-oc1.18.27
docker push $REG/opengauss:7.0.0-RC1
```
> 每条最后一行是 `digest: sha256:...`。

```bash
sed -i "s#image: gaussdb-agent-#image: $REG/gaussdb-agent-#" k8s/base/gateway.yaml k8s/base/frontend.yaml k8s/templates/runtime.yaml k8s/templates/kb-import.yaml
sed -i "s#image: opengauss/#image: $REG/opengauss:#" k8s/base/vectordb.yaml
```

```bash
grep -rh "image:" k8s/ | grep -v "#"
```
> 每行地址都以 `$REG` 开头。

---

## 4. 接上存储

```bash
cp k8s/base/nas-pv-nfs.example.yaml k8s/base/nas-pv-nfs.yaml
vi k8s/base/nas-pv-nfs.yaml
```

只改最后两行：

```yaml
  nfs:
    server: <NFS服务器地址>
    path: <NFS导出路径>
```

> `vi` 保存退出：按 `Esc`，输入 `:wq` 回车。

```bash
kubectl apply -f k8s/base/nas-pv-nfs.yaml
```
> `persistentvolume/nas-nfs created`

> 存储管理员需先在 NFS 导出目录下建好 `users/`、`admins/`、`kb/` 三个目录，属主 uid 1000。
> 属主不对的后果是**能对话、关掉浏览器历史全没了**，而且当时没有任何报错。

---

## 5. 创建资源

```bash
kubectl apply -k k8s/base
```

```bash
kubectl -n gaussdb-agent get pvc
```
> `nas` 的 STATUS 是 `Bound`。

---

## 6. 创建密钥

```bash
read -rs -p "模型服务密钥: " V && echo
kubectl -n gaussdb-agent create secret generic model-api --from-literal=MODEL_API_KEY="$V"
unset V
```

```bash
TRUST=$(openssl rand -hex 24)
kubectl -n gaussdb-agent create secret generic gateway-auth --from-literal=TRUST_SECRET="$TRUST" --from-literal=ADMIN_TOKEN="$(openssl rand -hex 24)"
echo "$TRUST"
```
> 把这串 48 位字符**抄下来**，第 10 步要用。

```bash
kubectl -n gaussdb-agent create secret generic vectordb-auth --from-literal=GS_PASSWORD="$(openssl rand -base64 12 | tr -d '/+=')Aa1@" --from-literal=KB_RW="$(openssl rand -base64 12 | tr -d '/+=')Rw1@" --from-literal=KB_RO="$(openssl rand -base64 12 | tr -d '/+=')Ro1@"
```

```bash
kubectl -n gaussdb-agent get secret
```
> `model-api`、`gateway-auth`、`vectordb-auth` 三行。

---

## 7. 填配置

```bash
kubectl -n gaussdb-agent edit configmap agent-config
```

改这四行引号里的内容：

```yaml
  GRMP_API_HOST: "<中间件地址>"
  GRMP_API_PORT: "<中间件端口>"
  MODEL_BASE_URL: "<模型服务地址,含/v1>"
  MODEL_ID: "<模型名称>"
```

```bash
kubectl -n gaussdb-agent edit configmap gateway-config
```

确认这行：

```yaml
  USER_IMAGE_TAG: "agent-v1.1.2-oc1.18.27"
```

---

## 8. 启动数据库

```bash
kubectl -n gaussdb-agent rollout status statefulset/vectordb --timeout=600s
```
> 卡住 1–5 分钟是正常的。最后出现 `partitioned roll out complete: 1 pods`。

```bash
bash scripts/k8s/init-vectordb.sh
```
> 出现 `✓ vector 类型在`、`kb 库已建`、`kb_ro 建表应被拒`。

---

## 9. 启动网关

```bash
kubectl -n gaussdb-agent rollout restart deploy/gateway
kubectl -n gaussdb-agent rollout status deploy/gateway --timeout=180s
```

```bash
kubectl -n gaussdb-agent logs -l app=agent-gateway --tail=3
```
> 出现一行以「网关就绪」开头的中文。

```bash
kubectl -n gaussdb-agent rollout status deploy/frontend --timeout=180s
```
> `deployment "frontend" successfully rolled out`（大盘前端，第 5 步 apply 时已建，这里等它起来）。

---

## 10. 开通用户并验证

```bash
read -rs -p "该工号的 GRMP 令牌: " V && echo
kubectl -n gaussdb-agent create secret generic grmp-token-<工号> --from-literal=GRMP_AUTH_TOKEN="$V"
unset V
```

```bash
kubectl -n gaussdb-agent port-forward svc/gateway 18080:80 &
sleep 3
```

```bash
STAFF=<工号>
curl -s http://127.0.0.1:18080/healthz
```
> `{"ok": true, "version": "gaussdb-agent-gateway"}`

```bash
curl -s -o /dev/null -w "%{http_code}\n" -H "X-Agent-User: $STAFF" http://127.0.0.1:18080/session
```
> `403`

```bash
curl -s -o /dev/null -w "%{http_code}\n" -H "X-Agent-Trust: $TRUST" -H "X-Agent-User: $STAFF" http://127.0.0.1:18080/global/health
```
> `200`（首次要等 10–30 秒，网关在建环境）

```bash
kubectl -n gaussdb-agent get deploy runtime-$STAFF
```
> `READY` 列是 `1/1`。

```bash
curl -s -o /dev/null -w "%{http_code}\n" -H "X-Agent-Trust: $TRUST" -H "X-Agent-User: $STAFF" http://127.0.0.1:18080/dash/health
```
> `200`（大盘经网关可达。页面上有没有内容取决于这个人跑没跑过健康检查）

**到这里测试环境就绪。**

---

## 要填的值

| 值 | 找谁要 |
|---|---|
| GitHub 用户名与令牌 | 自己生成，令牌只勾 `read:packages` |
| 内网镜像仓库地址 | 容器平台管理员 |
| NFS 服务器地址、导出路径 | 存储管理员 |
| 模型服务地址、密钥、模型名 | AI 平台管理员 |
| 中间件地址、端口 | 中间件团队 |
| 工号 + 该工号的 GRMP 令牌 | 中间件团队 |

---

## 命令没对上时

```bash
kubectl -n gaussdb-agent get pod
kubectl -n gaussdb-agent logs -l app=agent-gateway --tail=30
```

把这两条的完整输出发我方。

详细排查见 `命令卡-测试环境搭建.md`，架构与 SSO 对接见 `接入手册-SSO与K8s.md`，
各团队分工与验收见 `对接清单-各方要做什么.md`，参数逐项说明见 `参数手册.md`。
