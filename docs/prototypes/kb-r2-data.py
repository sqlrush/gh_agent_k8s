#!/usr/bin/env python3
"""知识库设计稿 R2 的数据:从一份真实知识库目录读出案例、条款、关系图,写成 kb-r2-data.js。

只给设计稿用(docs/prototypes 不随包发出)。
  python3 docs/prototypes/kb-r2-data.py ~/kb-verify/modes/kb
"""
import glob
import json
import pathlib
import re
import sys

import yaml

kb = pathlib.Path(sys.argv[1]).expanduser()
out = pathlib.Path(__file__).with_name("kb-r2-data.js")


def split_md(text):
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
    meta = yaml.safe_load(m.group(1)) if m else {}
    body = m.group(2) if m else text
    secs, cur = {}, None
    for line in body.splitlines():
        h = re.match(r"^##\s+(.+)$", line)
        if h:
            cur = h.group(1).strip()
            secs[cur] = []
        elif cur:
            secs[cur].append(line)
    return meta, {k: "\n".join(v).strip() for k, v in secs.items()}


cases = []
for p in sorted(kb.glob("cases/*.md")):
    meta, secs = split_md(p.read_text(encoding="utf-8"))
    cases.append({"id": meta.get("id") or p.stem, "title": meta.get("title", p.stem),
                  "system": meta.get("system", ""), "severity": meta.get("severity", ""),
                  "occurred_at": str(meta.get("occurred_at", "")), "conclusion": meta.get("conclusion", ""),
                  "primary_factor": meta.get("primary_factor", ""), "source": meta.get("source", ""),
                  "objects": meta.get("objects") or [], "signals": meta.get("signals") or [],
                  "rules": meta.get("rules") or [], "sections": secs})

groups = []
for p in sorted(kb.glob("rules/*.yaml")):
    first = p.read_text(encoding="utf-8").splitlines()[0]
    items = yaml.safe_load(p.read_text(encoding="utf-8")) or []
    groups.append({"file": p.name, "title": first.lstrip("# ").strip(),
                   "rules": [{k: r.get(k, "") for k in ("id", "severity", "check", "rule", "rationale",
                                                         "criteria", "keywords", "source")} for r in items]})

# 节点归一用产品自己的 graphfiles.canonical_id:同一现象的原话与代号(STALE_STATS)合成一个节点,
# 与上线后的图完全一致。显示名取别名表里的第一条中文原话,没有就用原名。
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "agent"))
from common.kb import graphfiles  # noqa: E402

aliases, _ = graphfiles.load_canonical(kb)
canon = yaml.safe_load((kb / "graph" / "canonical.yaml").read_text(encoding="utf-8")) if (kb / "graph" / "canonical.yaml").is_file() else {}
display = {str(k): (v or [str(k)])[0] for k, v in (canon or {}).items()}


def node(raw, case_id):
    if raw["kind"] == "case":
        return {"kind": "case", "id": "case:" + (case_id or raw["name"]), "label": raw["name"]}
    cid = graphfiles.canonical_id(raw["kind"], raw["name"], aliases)
    return {"kind": raw["kind"], "id": cid, "label": display.get(cid, raw["name"])}


edges = []
for p in sorted(kb.glob("graph/*.yaml")):
    if p.name == "canonical.yaml":
        continue
    for e in yaml.safe_load(p.read_text(encoding="utf-8")) or []:
        if e.get("status") != "accepted":
            continue
        edges.append({"src": node(e["src"], e.get("case")), "dst": node(e["dst"], e.get("case")),
                      "rel": e["rel"], "source": e.get("source", ""), "case": e.get("case", "")})

out.write_text("window.KB = " + json.dumps({"cases": cases, "groups": groups, "edges": edges},
                                            ensure_ascii=False, indent=1) + ";\n", encoding="utf-8")
print("案例 %d · 条款 %d · 边 %d → %s" % (len(cases), sum(len(g["rules"]) for g in groups), len(edges), out))
