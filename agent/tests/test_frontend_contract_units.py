"""前端 ↔ 报告的跨语言契约守卫。

frontend/lib/contract.json 列出每个页面读到的字段路径;这里用 scripts/dash-samples.py **同一批构造函数**
(真实的 HealthEvidence / wdr Evidence / StmtRow / kb.health_dict,经 reports.archive 落盘)生成样例,
逐条核对路径存在。页面引用了报告里不存在的字段 → 这里红,不会等到上线才是一片空白。
没有 JS 工具链也能跑:这是两种语言之间唯一的钉子。
"""
import importlib.util
import json
import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_REPO = _ROOT.parent
sys.path.insert(0, str(_ROOT))

CONTRACT = json.loads((_REPO / "frontend" / "lib" / "contract.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def samples(tmp_path_factory):
    out = tmp_path_factory.mktemp("reports")
    spec = importlib.util.spec_from_file_location("dash_samples", _REPO / "scripts" / "dash-samples.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["dash_samples"] = mod
    spec.loader.exec_module(mod)
    assert mod.main([str(out)]) == 0
    return out


def _walk(obj, path):
    """按 a.b[].c 取值;[] 表示列表的每个元素都要往下走。返回 (found: bool, where: str)。"""
    parts = path.split(".")
    cur = [obj]
    for i, p in enumerate(parts):
        nxt = []
        if p.endswith("[]"):
            key = p[:-2]
            for o in cur:
                if not isinstance(o, dict) or key not in o:
                    return False, ".".join(parts[:i]) + " 下没有 " + key
                if not isinstance(o[key], list):
                    return False, key + " 不是列表"
                if not o[key]:
                    return False, key + " 是空列表(样例得给一个元素,否则测不到里面的字段)"
                nxt.extend(o[key])
        else:
            for o in cur:
                if not isinstance(o, dict) or p not in o:
                    return False, ".".join(parts[:i]) + " 下没有 " + p
                nxt.append(o[p])
        cur = nxt
    return True, ""


def _check(name, obj):
    missing = []
    for path in CONTRACT[name]:
        optional = path.endswith("?")
        ok, why = _walk(obj, path.rstrip("?"))
        if not ok and not optional:
            missing.append("%s → %s(%s)" % (name, path, why))
    assert not missing, "前端引用了报告里不存在的字段:\n  " + "\n  ".join(missing)


def _load(p):
    return json.loads(p.read_text(encoding="utf-8"))


def test_health_contract(samples):
    _check("health", _load(samples / "health" / "og5" / "latest.json"))


def test_topsql_contract(samples):
    _check("topsql", _load(samples / "topsql" / "og5" / "latest.json"))


def test_wdr_contract(samples):
    _check("wdr", _load(samples / "wdr" / "og5" / "latest.json"))


def test_sqltune_contract(samples):
    _check("sqltune", _load(samples / "sqltune" / "og5" / "1a9f.json"))


def test_kb_health_contract(samples):
    _check("kb_health", _load(samples / "kb" / "health.json"))


def test_kb_queries_contract(samples):
    rows = [json.loads(l) for l in (samples / "kb" / "queries.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    assert rows
    for r in rows:
        _check("kb_queries", r)


def test_index_targets_whoami_contract(samples):
    for e in _load(samples / "health" / "og5" / "index.json"):
        _check("index", e)
    for t in _load(samples / "health" / "targets.json"):
        _check("targets", t)
    _check("whoami", _load(samples / "whoami.json"))


def test_contract_lists_every_page_the_frontend_has():
    """frontend/pages/ 里每个页面在契约里都要有对应的段 —— 加了页面忘了写契约,守卫就守不到它。"""
    pages = {p.stem for p in (_REPO / "frontend" / "pages").glob("*.js")}
    covered = {"health", "topsql", "wdr", "kb"}
    assert pages == covered, "pages/ 与契约不一致:%s" % (pages ^ covered)


def test_a_bogus_field_would_be_caught(samples):
    """钉子本身要能红:故意加一个不存在的字段。"""
    obj = _load(samples / "health" / "og5" / "latest.json")
    ok, why = _walk(obj, "dims[].no_such_field")
    assert not ok and "no_such_field" in why


def test_sqltune_skip_contract(samples):
    _check("sqltune_skip", _load(samples / "sqltune" / "og5" / "0b1c…e7.json"))


# ---------------------------------------------------------------- 维度名 / 列名:不只是字段路径
# 第一版契约只核对字段路径(dims[].rows 存在就算过),而样例的维度名和行列形状是按设计稿编的:
# 样例上全绿,真集群上 WDR 的 8 个 KPI 全是「未采集」、健康检查的维度中文名一个都没对上。
# 这三条直接拿技能源码里的常量与表头去对页面源码 —— 技能改名或页面写错,这里先红。

import re  # noqa: E402

_SKILLS = _ROOT / "skills"
_PAGES = _REPO / "frontend" / "pages"


def _dim_constants(skill: str) -> set:
    src = (_SKILLS / skill / "scripts" / "model.py").read_text(encoding="utf-8")
    return set(re.findall(r'^DIM_\w+\s*=\s*"([^"]+)"', src, re.MULTILINE))


def _js_block(page: str, name: str) -> str:
    src = (_PAGES / page).read_text(encoding="utf-8")
    m = re.search(r"const %s = \{(.*?)\};" % name, src, re.S)
    assert m, "%s 里找不到 %s" % (page, name)
    return m.group(1)


def test_wdr_page_dimension_names_are_the_skills_constants():
    used = set(re.findall(r":\s*'([^']+)'", _js_block("wdr.js", "DIMS")))
    real = _dim_constants("gaussdb-wdr")
    assert used and used <= real, "wdr.js 的 DIMS 有技能里不存在的维度名:%s(真实的:%s)" % (sorted(used - real), sorted(real))


def test_health_page_titles_are_keyed_by_the_skills_constants():
    used = set(re.findall(r"'([^']+)'\s*:", _js_block("health.js", "TITLES")))
    real = _dim_constants("gaussdb-health")
    assert used and used <= real, "health.js 的 TITLES 有技能里不存在的维度名:%s" % sorted(used - real)
    # 健康检查实际会产出的 8 个维度都要有中文名,不然页面上中英混排
    produced = set(re.findall(r"dimension=(DIM_\w+)", (_SKILLS / "gaussdb-health" / "scripts" / "collectors.py").read_text(encoding="utf-8")))
    src = (_SKILLS / "gaussdb-health" / "scripts" / "model.py").read_text(encoding="utf-8")
    names = {k: v for k, v in re.findall(r'^(DIM_\w+)\s*=\s*"([^"]+)"', src, re.MULTILINE)}
    missing = {names[k] for k in produced if k in names} - used
    assert not missing, "健康检查会产出、页面却没有中文名的维度:%s" % sorted(missing)


def test_wdr_kpi_columns_exist_in_the_collectors_headers():
    src = (_PAGES / "wdr.js").read_text(encoding="utf-8")
    cols = set(re.findall(r"col:\s*'([^']+)'", src)) | set(re.findall(r"val\(d, '\w+', '([^']+)'\)", src))
    coll = (_SKILLS / "gaussdb-wdr" / "scripts" / "collectors.py").read_text(encoding="utf-8")
    headers = [h for block in re.findall(r"headers=\[([^\]]*)\]", coll) for h in re.findall(r'"([^"]+)"', block)]
    missing = [c for c in cols if not any(h.startswith(c) for h in headers)]
    assert cols and not missing, "WDR 页按列名取值,采集代码的表头里没有这些列:%s" % missing
