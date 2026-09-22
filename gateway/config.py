"""网关的环境变量契约。

**这一层的核心是 fail closed。** 网关的职责是「凭工号建 Pod 并代理过去」,如果信任
配置漏了,它就是一个「任填工号即可进入任何人环境」的接口 —— 而且从外面看它工作得
好好的,没有任何报错。所以:没配信任来源就**拒绝启动**,而不是警告后继续跑。

同样的道理用在回收模式与闲置时长上:值不认识就拒绝启动,不要悄悄用默认值 ——
运维以为自己配的是 delete,实际跑的是 scale,Pod 一个都没回收,过一周才发现。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Mapping, Tuple

from . import reaper

DEFAULT_USER_HEADER = "X-Agent-User"
DEFAULT_ROLES_HEADER = "X-Agent-Roles"
DEFAULT_PORT = 8080

# 监听端口的变量名**刻意不叫 GATEWAY_PORT**:k8s 会给命名空间里每个 Service 自动注入
# 一组发现变量 `<SVCNAME>_PORT` / `<SVCNAME>_SERVICE_PORT` / `<SVCNAME>_SERVICE_HOST`。
# Service 叫 gateway,于是 GATEWAY_PORT 被注入成 `tcp://10.x.x.x:80` —— 2026-09-21 真部署
# 时网关因此拒绝启动(fail closed 在这里救了场:它没有悄悄用错端口)。
# Pod 里还额外设了 enableServiceLinks: false 把那组变量整个关掉,这里是第二道。
ENV_PORT = "GATEWAY_LISTEN_PORT"


class ConfigError(Exception):
    """配置不完整或不合法。启动时抛,不要留到运行时。"""


@dataclass(frozen=True)
class Config:
    namespace: str
    image_tag: str
    pull_policy: str = "IfNotPresent"
    port: int = DEFAULT_PORT
    user_header: str = DEFAULT_USER_HEADER
    roles_header: str = DEFAULT_ROLES_HEADER
    trust_secret: str = ""
    trust_cidrs: Tuple[str, ...] = ()
    reap_mode: str = reaper.MODE_SCALE
    idle_seconds: int = reaper.DEFAULT_IDLE_SECONDS
    sweep_seconds: int = reaper.DEFAULT_SWEEP_SECONDS
    activity_interval_s: int = 60
    ready_timeout_s: int = 120
    kb_admin_roles: Tuple[str, ...] = ("kb-admin",)
    # 控制 API 的独立密钥。空 = 控制 API 关闭(只提供代理入口)。
    admin_token: str = ""
    # 大盘前端的 Service 名:/dash/* 代理到 <frontend_service>.<namespace>.svc:80。
    # 前端镜像没部署时这条路 502,不影响对话与报告两条路。
    frontend_service: str = "frontend"

    @property
    def trust_configured(self) -> bool:
        return bool(self.trust_secret or self.trust_cidrs)


def _int(env: Mapping[str, str], key: str, default: int, minimum: int = 0) -> int:
    raw = (env.get(key) or "").strip()
    if not raw:
        return default
    try:
        v = int(raw)
    except ValueError:
        raise ConfigError("%s=%r 不是整数" % (key, raw))
    if v < minimum:
        raise ConfigError("%s=%d 太小,至少 %d" % (key, v, minimum))
    return v


def load(env: Mapping[str, str]) -> Config:
    ns = (env.get("GATEWAY_NAMESPACE") or "").strip()
    if not ns:
        raise ConfigError("缺 GATEWAY_NAMESPACE(网关要在哪个命名空间里建 Pod)")
    tag = (env.get("GATEWAY_IMAGE_TAG") or "").strip()
    if not tag:
        raise ConfigError("缺 GATEWAY_IMAGE_TAG(给用户 Pod 用哪个版本的镜像)——"
                          "不给默认值:默认成 latest 会让升级悄悄发生在下一次登录")

    secret = (env.get("GATEWAY_TRUST_SECRET") or "").strip()
    cidrs = tuple(c.strip() for c in (env.get("GATEWAY_TRUST_CIDRS") or "").split(",") if c.strip())
    if not secret and not cidrs:
        raise ConfigError(
            "GATEWAY_TRUST_SECRET 与 GATEWAY_TRUST_CIDRS 一个都没配。"
            "网关按请求头里的工号决定进谁的环境,不限制来源就等于「任填工号即可进入任何人的环境」,"
            "而且从外面看不出异常。至少配一个:"
            "GATEWAY_TRUST_SECRET=<与上游 SSO 约定的共享密钥>,"
            "或 GATEWAY_TRUST_CIDRS=<上游 SSO / Ingress 的网段>。")
    if secret and len(secret) < 16:
        raise ConfigError("GATEWAY_TRUST_SECRET 太短(%d 字符),至少 16" % len(secret))

    mode = (env.get("GATEWAY_REAP_MODE") or reaper.MODE_SCALE).strip()
    if mode not in (reaper.MODE_DELETE, reaper.MODE_SCALE):
        raise ConfigError("GATEWAY_REAP_MODE=%r 不认识,只能是 %s / %s"
                          % (mode, reaper.MODE_DELETE, reaper.MODE_SCALE))

    # 闲置时长下限 300 秒:模型跑一个长任务几分钟不产生流量是正常的,
    # 配得比这还短会在用户等结果的时候把 Pod 收掉。
    idle = _int(env, "GATEWAY_IDLE_SECONDS", reaper.DEFAULT_IDLE_SECONDS, minimum=300)

    return Config(
        namespace=ns, image_tag=tag,
        pull_policy=(env.get("GATEWAY_PULL_POLICY") or "IfNotPresent").strip(),
        port=_int(env, ENV_PORT, DEFAULT_PORT, minimum=1),
        user_header=(env.get("GATEWAY_USER_HEADER") or DEFAULT_USER_HEADER).strip(),
        roles_header=(env.get("GATEWAY_ROLES_HEADER") or DEFAULT_ROLES_HEADER).strip(),
        trust_secret=secret, trust_cidrs=cidrs,
        reap_mode=mode, idle_seconds=idle,
        sweep_seconds=_int(env, "GATEWAY_SWEEP_SECONDS", reaper.DEFAULT_SWEEP_SECONDS, minimum=10),
        activity_interval_s=_int(env, "GATEWAY_ACTIVITY_INTERVAL_SECONDS", 60, minimum=5),
        ready_timeout_s=_int(env, "GATEWAY_READY_TIMEOUT_SECONDS", 120, minimum=10),
        kb_admin_roles=tuple(r.strip().lower() for r in
                             (env.get("GATEWAY_KB_ADMIN_ROLES") or "kb-admin").split(",") if r.strip()),
        admin_token=(env.get("GATEWAY_ADMIN_TOKEN") or "").strip(),
        frontend_service=(env.get("GATEWAY_FRONTEND_SERVICE") or "frontend").strip(),
    )
