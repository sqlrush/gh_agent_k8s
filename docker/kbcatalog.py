"""知识库目录:给大盘「案例 / 条款 / 关系图」三个页签用的一份 JSON。

报告端口 4097 的 `/_kb/catalog.json` 现读现算(按文件修改时间缓存),不落盘:
知识库是全体共享的,由管理员在导入环境更新 —— 每个人的报告目录里各存一份只会越来越旧。

**只读三类东西,别的一律不碰**:
  cases/*.md       案例(解析失败的不进目录,和检索时的口径一致)
  rules/*.yaml     现行条款(已废止的不进 —— 与 RULES.md 同一个口径)
  graph/*.yaml     已确认(accepted)的关系边;canonical.yaml 只用来把同一对象的不同叫法合成一个节点
收件目录 inbox/、原始材料 sources/ 与 raw/、索引 index/ 都不读:那里是未审核的材料与客户原件,
大盘上不该出现。

解析全部复用 common/kb 里检索用的那几个函数(cases / rulesfile / graphfiles),
所以大盘上看到的与模型检索到的是同一份东西、同一个口径。
"""
from __future__ import annotations

import os
import pathlib
import sys
import time
from typing import Any, Dict, List, Optional

# common/ 在镜像里装在技能目录下;开发机上在仓库 agent/ 下
for _p in (os.environ.get("GSDB_SKILLS_DIR", ""), "/opt/agent/config/opencode/skills",
           str(pathlib.Path(__file__).resolve().parents[1] / "agent")):
    if _p and (pathlib.Path(_p) / "common" / "kb" / "__init__.py").is_file():
        sys.path.insert(0, _p)
        break

import yaml  # noqa: E402

from common.kb import cases as kbcases  # noqa: E402
from common.kb import graphfiles  # noqa: E402
from common.kb import rulesfile  # noqa: E402

# 只有这三个子目录会被读;改这张表之前想清楚它会不会把未审核材料端到大盘上
READ_DIRS = ("cases", "rules", "graph")


def _mtime(kb: pathlib.Path) -> float:
    """三个目录里所有文件的最新修改时间 —— 缓存键。"""
    latest = 0.0
    for sub in READ_DIRS:
        root = kb / sub
        if not root.is_dir():
            continue
        for p in root.rglob("*"):
            try:
                latest = max(latest, p.stat().st_mtime)
            except OSError:
                pass
    return latest


def _display_names(kb: pathlib.Path) -> Dict[str, str]:
    """canonical.yaml 里每个节点 id 的第一条别名 = 给人看的名字(中文原话,而不是 STALE_STATS 这种代号)。"""
    path = kb / "graph" / "canonical.yaml"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v[0]) for k, v in data.items() if isinstance(v, list) and v}


def _case(c) -> Dict[str, Any]:
    return {"id": c.id, "title": c.title, "system": c.system, "severity": c.severity,
            "occurred_at": c.occurred_at, "conclusion": c.conclusion, "primary_factor": c.primary_factor,
            "source": c.source, "objects": list(c.objects), "signals": list(c.signals),
            "rules": list(c.rules), "sections": dict(c.sections)}


def _rule(e: dict) -> Dict[str, Any]:
    kw = e.get("keywords") or []
    return {"id": str(e.get("id") or ""), "severity": str(e.get("severity") or ""),
            "check": str(e.get("check") or ""), "rule": str(e.get("rule") or ""),
            "rationale": str(e.get("rationale") or ""), "criteria": str(e.get("criteria") or ""),
            "keywords": [str(k) for k in kw] if isinstance(kw, list) else [], "source": str(e.get("source") or "")}


def build(kb: pathlib.Path) -> Dict[str, Any]:
    """读知识库,返回目录。知识库不存在时 attached=False,不抛。"""
    kb = pathlib.Path(kb)
    if not (kb / "cases").is_dir() and not (kb / "rules").is_dir():
        return {"attached": False, "reason": "知识库目录不存在或为空", "cases": [], "groups": [], "edges": [], "warnings": 0}
    cases, case_findings = kbcases.load_cases(kb)
    groups: List[Dict[str, Any]] = []
    warnings = sum(1 for lvl, _ in case_findings if lvl == "error")
    for path, err, active in rulesfile.iter_active_rules(kb):
        if err:
            warnings += 1
            continue
        groups.append({"file": path.name, "title": rulesfile.first_heading(path),
                       "rules": [_rule(e) for e in active if isinstance(e, dict)]})
    triples, graph_findings = graphfiles.load_triples(kb, case_ids=[c.id for c in cases])
    warnings += sum(1 for lvl, _ in graph_findings if lvl == "error")
    names = _display_names(kb)

    def node(ref) -> Dict[str, str]:
        return {"kind": ref.kind, "id": ref.id, "label": names.get(ref.id, ref.name)}

    edges = [{"src": node(t.src), "dst": node(t.dst), "rel": t.rel, "source": t.source, "case": t.case_id}
             for t in triples if t.status == "accepted"]
    return {"attached": True, "cases": [_case(c) for c in cases], "groups": groups, "edges": edges,
            "warnings": warnings}


class Cached:
    """按三个目录的最新修改时间缓存:知识库一天改不了几次,大盘却每次刷新都要。"""

    def __init__(self, kb: pathlib.Path):
        self.kb = pathlib.Path(kb)
        self._key: Optional[float] = None
        self._value: Optional[Dict[str, Any]] = None

    def get(self) -> Dict[str, Any]:
        key = _mtime(self.kb)
        if self._value is None or key != self._key:
            value = build(self.kb)
            value["built_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            self._key, self._value = key, value
        return self._value
