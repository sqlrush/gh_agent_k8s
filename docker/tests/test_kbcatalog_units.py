"""知识库目录(大盘案例 / 条款 / 关系图页签的数据)。

最要紧的是**只端该端的**:收件目录里未审核的材料、客户原件、已废止条款、候选边都不能上大盘。
"""
import json
import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "docker"))

import kbcatalog  # noqa: E402

CID = "S2-20250908-CBST-灌数未ANALYZE计划跳变致CPU打满"


def _kb(tmp_path):
    kb = tmp_path / "kb"
    (kb / "cases").mkdir(parents=True)
    (kb / "cases" / (CID + ".md")).write_text(f"""---
id: {CID}
title: 灌数未 ANALYZE 计划跳变致 CPU 打满
system: CBST
occurred_at: '2025-09-08'
conclusion: 已确认
source: 工单导出-2025Q3.csv#row=3
severity: S2
primary_factor: 灌数后统计信息失真
rules: [GS-OPS-001]
---
## 现场
CPU 占 DB time 84%
## 判断
灌数后未 ANALYZE
## 处置
补 ANALYZE
""", encoding="utf-8")
    (kb / "rules").mkdir()
    (kb / "rules" / "ops.yaml").write_text("""# 运维操作规范
- id: GS-OPS-001
  severity: error
  rule: 批量灌数之后必须显式 ANALYZE
  keywords: [ANALYZE, 统计信息]
  source: 运维规范 §5.3
- id: GS-OPS-009
  severity: warn
  status: deprecated
  rule: 已废止的条款
""", encoding="utf-8")
    (kb / "graph").mkdir()
    (kb / "graph" / "canonical.yaml").write_text("symptom:dbtime_cpu_heavy:\n- CPU 占 DB time 84% 以上\n- DBTIME_CPU_HEAVY\n",
                                                  encoding="utf-8")
    (kb / "graph" / "t.yaml").write_text(f"""- src: {{kind: case, name: 灌数未 ANALYZE, canonical: 'case:{CID}'}}
  rel: exhibits
  dst: {{kind: symptom, name: DBTIME_CPU_HEAVY}}
  confidence: 1.0
  status: accepted
  source: cases/{CID}.md#现场
  case: {CID}
- src: {{kind: symptom, name: CPU 占 DB time 84% 以上}}
  rel: caused_by
  dst: {{kind: rootcause, name: 统计信息失真}}
  confidence: 1.0
  status: accepted
  source: cases/{CID}.md#判断
  case: {CID}
- src: {{kind: rootcause, name: 统计信息失真}}
  rel: handled_by
  dst: {{kind: action, name: 候选处置}}
  confidence: 0.6
  status: candidate
  source: cases/{CID}.md#处置
  case: {CID}
""", encoding="utf-8")
    # 这些都不许出现在目录里
    (kb / "inbox" / "x").mkdir(parents=True)
    (kb / "inbox" / "x" / "未审核.md").write_text("未审核的工单 SECRET-INBOX", encoding="utf-8")
    (kb / "sources").mkdir()
    (kb / "sources" / "客户原件.md").write_text("客户原件 SECRET-SOURCE", encoding="utf-8")
    return kb


def test_catalog_has_cases_active_rules_and_accepted_edges(tmp_path):
    cat = kbcatalog.build(_kb(tmp_path))
    assert cat["attached"] is True
    assert [c["id"] for c in cat["cases"]] == [CID]
    c = cat["cases"][0]
    assert c["sections"]["现场"] == "CPU 占 DB time 84%" and c["rules"] == ["GS-OPS-001"]
    assert [r["id"] for g in cat["groups"] for r in g["rules"]] == ["GS-OPS-001"], "已废止条款不进目录"
    assert cat["groups"][0]["title"] == "运维操作规范"
    assert all(e["rel"] != "handled_by" for e in cat["edges"]), "候选边不上大盘"
    assert len(cat["edges"]) == 2


def test_same_symptom_under_two_names_is_one_node_with_the_readable_name(tmp_path):
    """同一现象在案例边里叫 DBTIME_CPU_HEAVY、在因果边里叫原话 —— 必须是同一个节点,显示原话。"""
    cat = kbcatalog.build(_kb(tmp_path))
    ids = {e["dst"]["id"] for e in cat["edges"] if e["rel"] == "exhibits"} & {e["src"]["id"] for e in cat["edges"] if e["rel"] == "caused_by"}
    assert ids == {"symptom:dbtime_cpu_heavy"}
    labels = {e["dst"]["label"] for e in cat["edges"] if e["rel"] == "exhibits"}
    assert labels == {"CPU 占 DB time 84% 以上"}


def test_inbox_and_sources_never_leak(tmp_path):
    text = json.dumps(kbcatalog.build(_kb(tmp_path)), ensure_ascii=False)
    assert "SECRET-INBOX" not in text and "SECRET-SOURCE" not in text


def test_missing_kb_is_not_an_error(tmp_path):
    cat = kbcatalog.build(tmp_path / "nope")
    assert cat["attached"] is False and cat["cases"] == []


def test_cache_rebuilds_when_a_file_changes(tmp_path):
    import os
    kb = _kb(tmp_path)
    c = kbcatalog.Cached(kb)
    first = c.get()
    assert c.get() is first, "没变就用缓存"
    p = kb / "rules" / "ops.yaml"
    p.write_text(p.read_text(encoding="utf-8").replace("必须显式 ANALYZE", "必须显式 ANALYZE 相关表"), encoding="utf-8")
    st = p.stat()
    os.utime(p, (st.st_atime, st.st_mtime + 5))
    assert "相关表" in c.get()["groups"][0]["rules"][0]["rule"]
