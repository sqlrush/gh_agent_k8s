window.KB = {
 "cases": [
  {
   "id": "S1-20250314-CBST-批量大事务致WALWriteLock等待冲高TP",
   "title": "批量大事务致 WALWriteLock 等待冲高 TPS 跌六成",
   "system": "CBST",
   "severity": "S1",
   "occurred_at": "2025-03-14",
   "conclusion": "已确认",
   "primary_factor": "批量单事务 20 万行,提交时 WAL 刷盘串行;synchronous_commit=on 让每次提交都等 fsync",
   "source": "工单导出-2025Q1.csv#row=5",
   "objects": [
    "WALWriteLock",
    "synchronous_commit",
    "WAIT_LWLOCK_HEAVY"
   ],
   "signals": [
    "WAIT_LWLOCK_HEAVY",
    "top 等待事件为 WALWriteLock 且时间与批量窗口重合"
   ],
   "rules": [
    "GS-GUC-004",
    "GS-OPS-003"
   ],
   "sections": {
    "现场": "批量期间 WAIT_LWLOCK_HEAVY 告警,等待事件 top1 是 WALWriteLock,占 DB time 40%,联机 TPS 跌六成",
    "判断": "批量单事务 20 万行,提交时 WAL 刷盘串行;synchronous_commit=on 让每次提交都等 fsync;调 wal_buffers 实测无效",
    "处置": "拆批到 5000 行/事务;批量窗口内经变更审批把 synchronous_commit 临时设为 local,窗口结束恢复;走变更单、23:00–06:00 窗口、双人复核",
    "复发标志": "WAIT_LWLOCK_HEAVY 且 top 等待事件为 WALWriteLock,时间与批量窗口重合"
   }
  },
  {
   "id": "S2-20241105-CBMS-exchange分区后未ANALYZE致存过计划",
   "title": "exchange 分区后未 ANALYZE 致存过计划跳变",
   "system": "CBMS",
   "severity": "S2",
   "occurred_at": "2024-11-05",
   "conclusion": "已确认",
   "primary_factor": "gs_loader 灌临时表后 alter table exchange 同步到分区表,exchange 不更新统计信息,分区表长期无有效统计",
   "source": "工单导出-2025Q1.csv#row=7",
   "objects": [
    "msc.cbms_custpckg_cust_idx"
   ],
   "signals": [
    "STALE_STATS",
    "STALE_STATS 命中分区表且该表有 exchange 作业"
   ],
   "rules": [
    "GS-OPS-001"
   ],
   "sections": {
    "现场": "业务反馈存过执行时间长,怀疑一张表长时间未做 analyze 导致执行计划跳变",
    "判断": "业务通过 gs_loader 批量导入临时表,再 alter table exchange 同步到分区表 msc.cbms_custpckg_cust_idx;exchange 操作不更新统计信息,分区表长期无有效统计",
    "处置": "exchange 操作后在代码中增加 analyze 分区表逻辑(analyze msc.cbms_custpckg_cust_idx),不依赖 autoanalyze",
    "复发标志": "STALE_STATS 命中分区表且该表有 exchange 作业"
   }
  },
  {
   "id": "S2-20250120-CBST-空闲事务持锁阻塞60余会话",
   "title": "空闲事务持锁阻塞 60 余会话",
   "system": "CBST",
   "severity": "S2",
   "occurred_at": "2025-01-20",
   "conclusion": "已确认",
   "primary_factor": "根会话所在应用实例的连接池 removeAbandoned 未开,异常分支漏 commit/rollback,空闲事务持锁",
   "source": "工单导出-2025Q1.csv#row=9",
   "objects": [
    "cbst.trans_journal"
   ],
   "signals": [
    "LOCK_ROOT_IDLE_XACT",
    "同一 application_name 反复出现"
   ],
   "rules": [
    "GS-OPS-002"
   ],
   "sections": {
    "现场": "锁等待链的根会话 state=idle in transaction 持续 40 分钟,后面挂了 60 多个会话,涉及 cbst.trans_journal",
    "判断": "根会话所在应用实例的连接池 removeAbandoned 未开,异常分支漏 commit/rollback",
    "处置": "数据库侧不 kill 业务会话;通过应用运维平台按 application_name 与 client_addr 定位实例,由应用方重启连接池,并补 removeAbandoned 配置",
    "复发标志": "LOCK_ROOT_IDLE_XACT 且同一 application_name 反复出现"
   }
  },
  {
   "id": "S2-20250224-CBST-小热表autovacuum过频致单条update",
   "title": "小热表 autovacuum 过频致单条 update 偶发 3s",
   "system": "CBST",
   "severity": "S2",
   "occurred_at": "2025-02-24",
   "conclusion": "已确认",
   "primary_factor": "cbst.cosp_asyn_task_dtl 小表更新极频繁,默认阈值下 autovacuum 每几分钟触发,尾部 page 回收持 8 级锁,DML cancel autovacuum 与重试之间卡住 update",
   "source": "工单导出-2025Q1.csv#row=2",
   "objects": [
    "cbst.cosp_asyn_task_dtl",
    "autovacuum_vacuum_threshold"
   ],
   "signals": [
    "单条 update 偶发 3s",
    "autovacuum_count 增速异常高"
   ],
   "rules": [
    "GS-VAC-001"
   ],
   "sections": {
    "现场": "业务偶现单条 update 走索引执行耗时 3s,平时 10ms 以内,无规律",
    "判断": "autovacuum 检测到表尾部 1000 page 或尾部空页占比达 1/16 时触发 page 回收,持有 8 级锁;正常 DML 进来会 cancel 掉 autovacuum,cancel 与重试之间正好卡住这条 update;cbst.cosp_asyn_task_dtl 是小表但更新极频繁,阈值按默认值算每几分钟就触发一次",
    "处置": "表级调大 autovacuum_vacuum_threshold(本行口径 5 万),减少 vacuum 频率;不按通用做法调小阈值",
    "复发标志": "单条 update 偶发 3s 且该表 autovacuum_count 增速异常高"
   }
  },
  {
   "id": "S2-20250405-CBST-报表全表扫描挤缓存致命中率低",
   "title": "报表全表扫描挤缓存致命中率低",
   "system": "CBST",
   "severity": "S2",
   "occurred_at": "2025-04-05",
   "conclusion": "已确认",
   "primary_factor": "报表 SQL 对 cbst.trans_journal 全表扫描,把联机热点页挤出缓存",
   "source": "工单导出-2025Q1.csv#row=4",
   "objects": [
    "cbst.trans_journal",
    "shared_buffers",
    "CACHE_LOW"
   ],
   "signals": [
    "CACHE_LOW",
    "CACHE_LOW 与报表时段重合"
   ],
   "rules": [
    "GS-GUC-001"
   ],
   "sections": {
    "现场": "健康检查 CACHE_LOW,缓存命中率 96%,白天报表时段 blks_read 冲高",
    "判断": "报表 SQL 对 cbst.trans_journal 全表扫描,把联机热点页挤出缓存;生产实例 shared_buffers 已按 NUMA 绑核方案固定",
    "处置": "shared_buffers 由 NUMA 绑核方案固定,不因命中率调整;把报表 SQL 迁到只读实例",
    "复发标志": "CACHE_LOW 与报表时段重合"
   }
  },
  {
   "id": "S2-20250812-CBMS-两过程更新顺序相反致死锁频发",
   "title": "两过程更新顺序相反致死锁频发",
   "system": "CBMS",
   "severity": "S2",
   "occurred_at": "2025-08-12",
   "conclusion": "已确认",
   "primary_factor": "proc_a 先更新 cbms.customer 再更新 cbms.credit_limit,proc_b 相反,并发时必然死锁",
   "source": "工单导出-2025Q1.csv#row=8",
   "objects": [
    "cbms.customer",
    "cbms.credit_limit"
   ],
   "signals": [
    "DEADLOCKS",
    "死锁的两条语句涉及 customer/credit_limit"
   ],
   "rules": [
    "GS-DML-001"
   ],
   "sections": {
    "现场": "pg_stat_database.deadlocks 每天增加十几次,应用日志有 deadlock detected 后重试成功",
    "判断": "proc_a 先更新 cbms.customer 再更新 cbms.credit_limit,proc_b 相反;并发时必然死锁,重试能过只是掩盖",
    "处置": "改 proc_b 的更新顺序与 proc_a 一致(客户表→额度表),这是本行固定的多表更新顺序;不采用通用的加重试方案",
    "复发标志": "DEADLOCKS 计数上升且死锁的两条语句涉及 customer/credit_limit"
   }
  },
  {
   "id": "S2-20250908-CBST-灌数未ANALYZE计划跳变致CPU打满",
   "title": "灌数未 ANALYZE 计划跳变致 CPU 打满",
   "system": "CBST",
   "severity": "S2",
   "occurred_at": "2025-09-08",
   "conclusion": "已确认",
   "primary_factor": "cbst.trans_journal 前一晚灌了 3000 万行未 analyze,Top SQL 计划从 hash join 变成 nested loop,行数估算差三个数量级",
   "source": "工单导出-2025Q1.csv#row=6",
   "objects": [
    "cbst.trans_journal",
    "cbst.acct_balance",
    "DBTIME_CPU_HEAVY"
   ],
   "signals": [
    "DBTIME_CPU_HEAVY",
    "Top SQL 计划形状变化或大表 last_analyze 早于最近一次批量"
   ],
   "rules": [
    "GS-OPS-001",
    "GS-GUC-001"
   ],
   "sections": {
    "现场": "DBTIME_CPU_HEAVY 连续 5 个快照 CPU 占 DB_TIME 84% 以上,主机 CPU 打满,运维想申请扩容",
    "判断": "Top SQL 计划从 hash join 变成 nested loop,行数估算差三个数量级;cbst.trans_journal 前一晚灌了 3000 万行未 analyze",
    "处置": "CPU 高先查统计信息与计划,ANALYZE cbst.trans_journal 与 cbst.acct_balance 后计划回到 hash join,CPU 回落到 30%;不先扩 CPU、不调 work_mem",
    "复发标志": "DBTIME_CPU_HEAVY 伴随 Top SQL 计划形状变化或大表 last_analyze 早于最近一次批量"
   }
  },
  {
   "id": "S3-20250210-CBST-未使用索引按台账流程观察后再删",
   "title": "未使用索引按台账流程观察后再删",
   "system": "CBST",
   "severity": "S3",
   "occurred_at": "2025-02-10",
   "conclusion": "已确认",
   "primary_factor": "该索引为已下线报表建,但月末批量有一条 SQL 仍可能用到,不能直接删除",
   "source": "工单导出-2025Q1.csv#row=3",
   "objects": [
    "cbst.trans_journal_rpt_idx",
    "INDEX_UNUSED"
   ],
   "signals": [
    "INDEX_UNUSED",
    "观察窗口不足 30 天或未覆盖月末批量"
   ],
   "rules": [
    "GS-IDX-001",
    "GS-OPS-003"
   ],
   "sections": {
    "现场": "健康检查报 INDEX_UNUSED:cbst.trans_journal_rpt_idx 14 天 idx_scan=0,占 800MB",
    "判断": "该索引为已下线报表建,但月末批量有一条 SQL 仍可能用到",
    "处置": "先在索引台账登记、观察满 30 天(覆盖一次月末批量)、确认 idx_scan 仍为 0,再提变更单在 23:00–06:00 窗口 DROP,双人复核",
    "复发标志": "INDEX_UNUSED 且窗口不足 30 天或未覆盖月末"
   }
  }
 ],
 "groups": [
  {
   "file": "dml.yaml",
   "title": "开发规范:增删改查",
   "rules": [
    {
     "id": "GS-DML-001",
     "severity": "error",
     "check": "advisory",
     "rule": "对账户类表的更新必须按主键升序、每 5000 行一个事务;涉及多表时固定顺序:客户表 → 额度表 → 账户表",
     "rationale": "固定加锁顺序与拆批更新,避免账户类核心表死锁与长事务",
     "criteria": "看到账户类表批量更新时,核对是否按主键升序、是否每 5000 行一个事务、多表是否按客户表→额度表→账户表顺序;任一不满足即违规",
     "keywords": [
      "账户表",
      "主键升序",
      "批量更新",
      "5000 行",
      "加锁顺序",
      "死锁"
     ],
     "source": "《GaussDB 运维规范(摘录)》v5 附:开发规范 §5.1"
    },
    {
     "id": "GS-DML-002",
     "severity": "error",
     "check": "deterministic",
     "rule": "分区表查询条件不得对分区键使用函数或表达式(如 to_char(trans_date, 'YYYYMM')),否则分区裁剪失效",
     "rationale": "分区键被函数包裹后优化器无法做分区裁剪,查询退化为全分区扫描",
     "criteria": "",
     "keywords": [
      "分区表",
      "分区键",
      "分区裁剪",
      "partition pruning",
      "to_char",
      "函数"
     ],
     "source": "《GaussDB 运维规范(摘录)》v5 附:开发规范 §5.3"
    }
   ]
  },
  {
   "file": "guc.yaml",
   "title": "参数管理规范(全局参数、连接池、WAL 相关参数)",
   "rules": [
    {
     "id": "GS-GUC-001",
     "severity": "warn",
     "check": "advisory",
     "rule": "shared_buffers、work_mem、max_connections 等全局参数由 NUMA 绑核方案与容量模型统一确定,不得因单次指标(如缓存命中率、连接数)调整;报表类需求一律走只读实例,或由应用在会话级设置参数",
     "rationale": "全局参数牵一发动全身,按单次指标调参会破坏容量模型",
     "criteria": "出现调整全局参数的建议或变更时,核对是否由 NUMA 方案/容量模型统一发起;因单次指标(缓存命中率低、连接数紧张等)提出的全局调参即违规",
     "keywords": [
      "shared_buffers",
      "work_mem",
      "max_connections",
      "全局参数",
      "缓存命中率",
      "会话级参数"
     ],
     "source": "《GaussDB 运维规范(摘录)》v5 §4.2"
    },
    {
     "id": "GS-GUC-002",
     "severity": "error",
     "check": "advisory",
     "rule": "应用必须使用本行统一的 druid 连接池模板,每实例 maxActive 不超过 20",
     "rationale": "统一连接池模板、限制每实例连接数,防止连接风暴打满 max_connections",
     "criteria": "看到应用连接池配置时核对是否为本行 druid 模板、单实例 maxActive 是否超过 20;超过即违规",
     "keywords": [
      "druid",
      "连接池",
      "maxActive",
      "连接数",
      "connection pool"
     ],
     "source": "《GaussDB 运维规范(摘录)》v5 §4.3"
    },
    {
     "id": "GS-GUC-003",
     "severity": "error",
     "check": "advisory",
     "rule": "禁止短连接",
     "rationale": "短连接频繁建断带来连接开销与负载抖动",
     "criteria": "看到应用每次操作新建/关闭物理连接、未走连接池,即违规",
     "keywords": [
      "短连接",
      "长连接",
      "连接池",
      "short connection"
     ],
     "source": "《GaussDB 运维规范(摘录)》v5 §4.3"
    },
    {
     "id": "GS-GUC-004",
     "severity": "warn",
     "check": "advisory",
     "rule": "WALWriteLock 类等待冲高时,优先调整批量的拆批与提交策略;不调整 wal_buffers",
     "rationale": "本行口径:WAL 写入等待的根因在批量提交模式,调 wal_buffers 不解决问题",
     "criteria": "出现 WALWriteLock 类等待冲高时,处置建议应落在拆批与提交策略上;建议调大 wal_buffers 即与本行规范不符",
     "keywords": [
      "WALWriteLock",
      "wal_buffers",
      "WAL 等待",
      "拆批",
      "提交策略"
     ],
     "source": "《GaussDB 运维规范(摘录)》v5 §8.1"
    }
   ]
  },
  {
   "file": "idx.yaml",
   "title": "索引规范",
   "rules": [
    {
     "id": "GS-IDX-001",
     "severity": "error",
     "check": "advisory",
     "rule": "未使用索引不得直接删除:先在索引台账登记,观察满 30 天且覆盖一次月末批量,确认 idx_scan 仍为 0 后,提变更单删除",
     "rationale": "idx_scan 短期为 0 不代表索引无用,月末批量等低频路径可能依赖它",
     "criteria": "看到未使用索引(idx_scan 为 0)时,直接建议或执行 DROP INDEX 即违规;合规路径是登记台账、观察满 30 天且覆盖一次月末批量后提变更单",
     "keywords": [
      "未使用索引",
      "idx_scan",
      "无用索引",
      "删除索引",
      "DROP INDEX",
      "索引台账"
     ],
     "source": "《GaussDB 运维规范(摘录)》v5 §7.1"
    }
   ]
  },
  {
   "file": "ops.yaml",
   "title": "运维操作规范(统计信息、会话处置、变更管理)",
   "rules": [
    {
     "id": "GS-OPS-001",
     "severity": "error",
     "check": "advisory",
     "rule": "批量灌数、alter table exchange partition、gs_loader 导入之后,必须在作业代码里显式 ANALYZE 相关表,不依赖 autoanalyze;CPU 或执行计划异常时先核对统计信息与计划,再讨论资源扩容",
     "rationale": "批量数据变更后统计信息滞后会导致计划劣化,autoanalyze 时机不可控",
     "criteria": "看到批量灌数、exchange partition、gs_loader 导入类作业时核对作业代码是否含显式 ANALYZE;没有即违规",
     "keywords": [
      "ANALYZE",
      "统计信息",
      "autoanalyze",
      "exchange partition",
      "gs_loader",
      "批量灌数"
     ],
     "source": "《GaussDB 运维规范(摘录)》v5 §5.3"
    },
    {
     "id": "GS-OPS-002",
     "severity": "error",
     "check": "advisory",
     "rule": "长事务(超过 8 小时)与空闲事务(idle in transaction 超过 10 分钟)一律经应用运维平台定位并由应用方处理,数据库侧不 kill 业务会话",
     "rationale": "本行口径:业务会话由应用方处置,数据库侧直接 kill 有业务风险",
     "criteria": "发现长事务/空闲事务时,处置路径应是应用运维平台定位、应用方处理;给出数据库侧 kill 业务会话的建议即与本行规范不符",
     "keywords": [
      "长事务",
      "空闲事务",
      "idle in transaction",
      "kill 会话",
      "8 小时"
     ],
     "source": "《GaussDB 运维规范(摘录)》v5 §3.4"
    },
    {
     "id": "GS-OPS-003",
     "severity": "error",
     "check": "advisory",
     "rule": "生产变更(DROP INDEX、REINDEX、exchange partition、参数临时调整等)一律提变更单,在 23:00–06:00 窗口执行,双人复核",
     "rationale": "生产高风险操作必须走变更管控,窗口内执行降低业务影响",
     "criteria": "看到生产环境 DROP INDEX/REINDEX/exchange partition/参数临时调整类操作时,核对是否有变更单、是否在 23:00–06:00 窗口、是否双人复核;任一不满足即违规",
     "keywords": [
      "变更单",
      "变更窗口",
      "双人复核",
      "DROP INDEX",
      "REINDEX",
      1380
     ],
     "source": "《GaussDB 运维规范(摘录)》附:变更管理办法 §2.1"
    }
   ]
  },
  {
   "file": "prc.yaml",
   "title": "开发规范:存储过程",
   "rules": [
    {
     "id": "GS-PRC-001",
     "severity": "error",
     "check": "advisory",
     "rule": "存储过程循环内不得逐条提交,每 5000 行批量提交一次;动态 SQL 必须使用绑定变量;exception 分支必须回滚",
     "rationale": "循环内逐条提交放大 WAL 与事务开销;动态 SQL 不绑定变量易引发注入与硬解析;exception 不回滚会留下半完成事务",
     "criteria": "审查存储过程源码时核对:循环内是否有逐条 commit、批量提交是否按 5000 行、动态 SQL 是否使用绑定变量、exception 分支是否有 rollback;任一不满足即违规",
     "keywords": [
      "存储过程",
      "循环提交",
      "批量提交",
      "绑定变量",
      "动态 SQL",
      "exception 回滚"
     ],
     "source": "《GaussDB 运维规范(摘录)》v5 附:开发规范 §6.1"
    }
   ]
  },
  {
   "file": "vac.yaml",
   "title": "vacuum 规范",
   "rules": [
    {
     "id": "GS-VAC-001",
     "severity": "warn",
     "check": "advisory",
     "rule": "行数少于 10 万的热表(更新频繁)按表级调大 autovacuum_vacuum_threshold,本行口径 5 万;不按默认阈值频繁 vacuum",
     "rationale": "小热表按默认阈值会被频繁触发 vacuum,徒增 IO 与锁开销",
     "criteria": "看到行数少于 10 万且更新频繁的表 vacuum 触发频繁时,处置建议应是表级调大 autovacuum_vacuum_threshold 至 5 万;按默认阈值接受频繁 vacuum 即与本行口径不符",
     "keywords": [
      "autovacuum_vacuum_threshold",
      "vacuum 阈值",
      "热表",
      "死元组",
      "频繁 vacuum"
     ],
     "source": "《GaussDB 运维规范(摘录)》v5 §6.2"
    }
   ]
  }
 ],
 "edges": [
  {
   "src": {
    "kind": "case",
    "id": "case:S2-20241105-CBMS-exchange分区后未ANALYZE致存过计划",
    "label": "exchange 分区后未 ANALYZE 致存过计划跳变"
   },
   "dst": {
    "kind": "symptom",
    "id": "symptom:stale_stats",
    "label": "存过执行时间长,执行计划跳变"
   },
   "rel": "exhibits",
   "source": "cases/S2-20241105-CBMS-exchange分区后未ANALYZE致存过计划.md#现场",
   "case": "S2-20241105-CBMS-exchange分区后未ANALYZE致存过计划"
  },
  {
   "src": {
    "kind": "symptom",
    "id": "symptom:stale_stats",
    "label": "存过执行时间长,执行计划跳变"
   },
   "dst": {
    "kind": "rootcause",
    "id": "rootcause:exchange_不更新统计信息分区表长期无有效统计",
    "label": "exchange 不更新统计信息,分区表长期无有效统计"
   },
   "rel": "caused_by",
   "source": "cases/S2-20241105-CBMS-exchange分区后未ANALYZE致存过计划.md#判断",
   "case": "S2-20241105-CBMS-exchange分区后未ANALYZE致存过计划"
  },
  {
   "src": {
    "kind": "rootcause",
    "id": "rootcause:exchange_不更新统计信息分区表长期无有效统计",
    "label": "exchange 不更新统计信息,分区表长期无有效统计"
   },
   "dst": {
    "kind": "action",
    "id": "action:exchange_后在作业代码中显式_analyze_分区表",
    "label": "exchange 后在作业代码中显式 ANALYZE 分区表"
   },
   "rel": "handled_by",
   "source": "cases/S2-20241105-CBMS-exchange分区后未ANALYZE致存过计划.md#处置",
   "case": "S2-20241105-CBMS-exchange分区后未ANALYZE致存过计划"
  },
  {
   "src": {
    "kind": "case",
    "id": "case:S2-20250120-CBST-空闲事务持锁阻塞60余会话",
    "label": "空闲事务持锁阻塞 60 余会话"
   },
   "dst": {
    "kind": "symptom",
    "id": "symptom:lock_root_idle_xact",
    "label": "空闲事务持锁,60 余会话被阻塞"
   },
   "rel": "exhibits",
   "source": "cases/S2-20250120-CBST-空闲事务持锁阻塞60余会话.md#现场",
   "case": "S2-20250120-CBST-空闲事务持锁阻塞60余会话"
  },
  {
   "src": {
    "kind": "symptom",
    "id": "symptom:lock_root_idle_xact",
    "label": "空闲事务持锁,60 余会话被阻塞"
   },
   "dst": {
    "kind": "rootcause",
    "id": "rootcause:连接池_removeabandoned_未开异常分支漏_commitrollback",
    "label": "连接池 removeAbandoned 未开,异常分支漏 commit/rollback"
   },
   "rel": "caused_by",
   "source": "cases/S2-20250120-CBST-空闲事务持锁阻塞60余会话.md#判断",
   "case": "S2-20250120-CBST-空闲事务持锁阻塞60余会话"
  },
  {
   "src": {
    "kind": "rootcause",
    "id": "rootcause:连接池_removeabandoned_未开异常分支漏_commitrollback",
    "label": "连接池 removeAbandoned 未开,异常分支漏 commit/rollback"
   },
   "dst": {
    "kind": "action",
    "id": "action:应用运维平台定位实例应用方重启连接池并补_removeabandoned_配置",
    "label": "应用运维平台定位实例,应用方重启连接池并补 removeAbandoned 配置"
   },
   "rel": "handled_by",
   "source": "cases/S2-20250120-CBST-空闲事务持锁阻塞60余会话.md#处置",
   "case": "S2-20250120-CBST-空闲事务持锁阻塞60余会话"
  },
  {
   "src": {
    "kind": "case",
    "id": "case:S3-20250210-CBST-未使用索引按台账流程观察后再删",
    "label": "未使用索引按台账流程观察后再删"
   },
   "dst": {
    "kind": "symptom",
    "id": "symptom:index_unused",
    "label": "索引 14 天 idx_scan=0 占 800MB"
   },
   "rel": "exhibits",
   "source": "cases/S3-20250210-CBST-未使用索引按台账流程观察后再删.md#现场",
   "case": "S3-20250210-CBST-未使用索引按台账流程观察后再删"
  },
  {
   "src": {
    "kind": "symptom",
    "id": "symptom:index_unused",
    "label": "索引 14 天 idx_scan=0 占 800MB"
   },
   "dst": {
    "kind": "rootcause",
    "id": "rootcause:索引为已下线报表所建月末批量_sql_仍可能用到",
    "label": "索引为已下线报表所建,月末批量 SQL 仍可能用到"
   },
   "rel": "caused_by",
   "source": "cases/S3-20250210-CBST-未使用索引按台账流程观察后再删.md#判断",
   "case": "S3-20250210-CBST-未使用索引按台账流程观察后再删"
  },
  {
   "src": {
    "kind": "rootcause",
    "id": "rootcause:索引为已下线报表所建月末批量_sql_仍可能用到",
    "label": "索引为已下线报表所建,月末批量 SQL 仍可能用到"
   },
   "dst": {
    "kind": "action",
    "id": "action:台账登记观察满_30_天覆盖月末批量后提变更单删除",
    "label": "台账登记观察满 30 天覆盖月末批量后提变更单删除"
   },
   "rel": "handled_by",
   "source": "cases/S3-20250210-CBST-未使用索引按台账流程观察后再删.md#处置",
   "case": "S3-20250210-CBST-未使用索引按台账流程观察后再删"
  },
  {
   "src": {
    "kind": "case",
    "id": "case:S2-20250224-CBST-小热表autovacuum过频致单条update",
    "label": "小热表 autovacuum 过频致单条 update 偶发 3s"
   },
   "dst": {
    "kind": "symptom",
    "id": "symptom:单条_update_偶发_3s",
    "label": "单条 update 偶发秒级耗时"
   },
   "rel": "exhibits",
   "source": "cases/S2-20250224-CBST-小热表autovacuum过频致单条update.md#现场",
   "case": "S2-20250224-CBST-小热表autovacuum过频致单条update"
  },
  {
   "src": {
    "kind": "symptom",
    "id": "symptom:单条_update_偶发_3s",
    "label": "单条 update 偶发秒级耗时"
   },
   "dst": {
    "kind": "rootcause",
    "id": "rootcause:小热表默认阈值下_autovacuum_过频cancel_与重试卡住_dml",
    "label": "小热表默认阈值下 autovacuum 过频,cancel 与重试卡住 DML"
   },
   "rel": "caused_by",
   "source": "cases/S2-20250224-CBST-小热表autovacuum过频致单条update.md#判断",
   "case": "S2-20250224-CBST-小热表autovacuum过频致单条update"
  },
  {
   "src": {
    "kind": "rootcause",
    "id": "rootcause:小热表默认阈值下_autovacuum_过频cancel_与重试卡住_dml",
    "label": "小热表默认阈值下 autovacuum 过频,cancel 与重试卡住 DML"
   },
   "dst": {
    "kind": "action",
    "id": "action:表级调大_autovacuum_vacuum_threshold_至_5_万",
    "label": "表级调大 autovacuum_vacuum_threshold 至 5 万"
   },
   "rel": "handled_by",
   "source": "cases/S2-20250224-CBST-小热表autovacuum过频致单条update.md#处置",
   "case": "S2-20250224-CBST-小热表autovacuum过频致单条update"
  },
  {
   "src": {
    "kind": "case",
    "id": "case:S1-20250314-CBST-批量大事务致WALWriteLock等待冲高TP",
    "label": "批量大事务致 WALWriteLock 等待冲高 TPS 跌六成"
   },
   "dst": {
    "kind": "symptom",
    "id": "symptom:wait_lwlock_heavy",
    "label": "WALWriteLock 等待冲高,联机 TPS 跌六成"
   },
   "rel": "exhibits",
   "source": "cases/S1-20250314-CBST-批量大事务致WALWriteLock等待冲高TP.md#现场",
   "case": "S1-20250314-CBST-批量大事务致WALWriteLock等待冲高TP"
  },
  {
   "src": {
    "kind": "symptom",
    "id": "symptom:wait_lwlock_heavy",
    "label": "WALWriteLock 等待冲高,联机 TPS 跌六成"
   },
   "dst": {
    "kind": "rootcause",
    "id": "rootcause:批量单事务_20_万行提交时_wal_刷盘串行synchronous_commiton_每次提交等_fsync",
    "label": "批量单事务 20 万行提交时 WAL 刷盘串行,synchronous_commit=on 每次提交等 fsync"
   },
   "rel": "caused_by",
   "source": "cases/S1-20250314-CBST-批量大事务致WALWriteLock等待冲高TP.md#判断",
   "case": "S1-20250314-CBST-批量大事务致WALWriteLock等待冲高TP"
  },
  {
   "src": {
    "kind": "rootcause",
    "id": "rootcause:批量单事务_20_万行提交时_wal_刷盘串行synchronous_commiton_每次提交等_fsync",
    "label": "批量单事务 20 万行提交时 WAL 刷盘串行,synchronous_commit=on 每次提交等 fsync"
   },
   "dst": {
    "kind": "action",
    "id": "action:拆批到_5000_行事务变更窗口内_synchronous_commit_临时设为_local",
    "label": "拆批到 5000 行/事务,变更窗口内 synchronous_commit 临时设为 local"
   },
   "rel": "handled_by",
   "source": "cases/S1-20250314-CBST-批量大事务致WALWriteLock等待冲高TP.md#处置",
   "case": "S1-20250314-CBST-批量大事务致WALWriteLock等待冲高TP"
  },
  {
   "src": {
    "kind": "case",
    "id": "case:S2-20250405-CBST-报表全表扫描挤缓存致命中率低",
    "label": "报表全表扫描挤缓存致命中率低"
   },
   "dst": {
    "kind": "symptom",
    "id": "symptom:cache_low",
    "label": "缓存命中率低,报表时段 blks_read 冲高"
   },
   "rel": "exhibits",
   "source": "cases/S2-20250405-CBST-报表全表扫描挤缓存致命中率低.md#现场",
   "case": "S2-20250405-CBST-报表全表扫描挤缓存致命中率低"
  },
  {
   "src": {
    "kind": "symptom",
    "id": "symptom:cache_low",
    "label": "缓存命中率低,报表时段 blks_read 冲高"
   },
   "dst": {
    "kind": "rootcause",
    "id": "rootcause:报表_sql_全表扫描把联机热点页挤出缓存",
    "label": "报表 SQL 全表扫描把联机热点页挤出缓存"
   },
   "rel": "caused_by",
   "source": "cases/S2-20250405-CBST-报表全表扫描挤缓存致命中率低.md#判断",
   "case": "S2-20250405-CBST-报表全表扫描挤缓存致命中率低"
  },
  {
   "src": {
    "kind": "rootcause",
    "id": "rootcause:报表_sql_全表扫描把联机热点页挤出缓存",
    "label": "报表 SQL 全表扫描把联机热点页挤出缓存"
   },
   "dst": {
    "kind": "action",
    "id": "action:报表_sql_迁到只读实例shared_buffers_不因命中率调整",
    "label": "报表 SQL 迁到只读实例,shared_buffers 不因命中率调整"
   },
   "rel": "handled_by",
   "source": "cases/S2-20250405-CBST-报表全表扫描挤缓存致命中率低.md#处置",
   "case": "S2-20250405-CBST-报表全表扫描挤缓存致命中率低"
  },
  {
   "src": {
    "kind": "case",
    "id": "case:S2-20250812-CBMS-两过程更新顺序相反致死锁频发",
    "label": "两过程更新顺序相反致死锁频发"
   },
   "dst": {
    "kind": "symptom",
    "id": "symptom:deadlocks",
    "label": "死锁频发,deadlocks 每天增加十几次"
   },
   "rel": "exhibits",
   "source": "cases/S2-20250812-CBMS-两过程更新顺序相反致死锁频发.md#现场",
   "case": "S2-20250812-CBMS-两过程更新顺序相反致死锁频发"
  },
  {
   "src": {
    "kind": "symptom",
    "id": "symptom:deadlocks",
    "label": "死锁频发,deadlocks 每天增加十几次"
   },
   "dst": {
    "kind": "rootcause",
    "id": "rootcause:两个存储过程更新_customercredit_limit_顺序相反",
    "label": "两个存储过程更新 customer/credit_limit 顺序相反"
   },
   "rel": "caused_by",
   "source": "cases/S2-20250812-CBMS-两过程更新顺序相反致死锁频发.md#判断",
   "case": "S2-20250812-CBMS-两过程更新顺序相反致死锁频发"
  },
  {
   "src": {
    "kind": "rootcause",
    "id": "rootcause:两个存储过程更新_customercredit_limit_顺序相反",
    "label": "两个存储过程更新 customer/credit_limit 顺序相反"
   },
   "dst": {
    "kind": "action",
    "id": "action:统一多表更新顺序为客户表额度表",
    "label": "统一多表更新顺序为客户表→额度表"
   },
   "rel": "handled_by",
   "source": "cases/S2-20250812-CBMS-两过程更新顺序相反致死锁频发.md#处置",
   "case": "S2-20250812-CBMS-两过程更新顺序相反致死锁频发"
  },
  {
   "src": {
    "kind": "case",
    "id": "case:S2-20250908-CBST-灌数未ANALYZE计划跳变致CPU打满",
    "label": "灌数未 ANALYZE 计划跳变致 CPU 打满"
   },
   "dst": {
    "kind": "symptom",
    "id": "symptom:dbtime_cpu_heavy",
    "label": "CPU 占 DB time 84% 以上,主机 CPU 打满"
   },
   "rel": "exhibits",
   "source": "cases/S2-20250908-CBST-灌数未ANALYZE计划跳变致CPU打满.md#现场",
   "case": "S2-20250908-CBST-灌数未ANALYZE计划跳变致CPU打满"
  },
  {
   "src": {
    "kind": "symptom",
    "id": "symptom:dbtime_cpu_heavy",
    "label": "CPU 占 DB time 84% 以上,主机 CPU 打满"
   },
   "dst": {
    "kind": "rootcause",
    "id": "rootcause:灌数后未_analyze计划从_hash_join_跳变为_nested_loop",
    "label": "灌数后未 analyze,计划从 hash join 跳变为 nested loop"
   },
   "rel": "caused_by",
   "source": "cases/S2-20250908-CBST-灌数未ANALYZE计划跳变致CPU打满.md#判断",
   "case": "S2-20250908-CBST-灌数未ANALYZE计划跳变致CPU打满"
  },
  {
   "src": {
    "kind": "rootcause",
    "id": "rootcause:灌数后未_analyze计划从_hash_join_跳变为_nested_loop",
    "label": "灌数后未 analyze,计划从 hash join 跳变为 nested loop"
   },
   "dst": {
    "kind": "action",
    "id": "action:analyze_相关表使计划回到_hash_join不扩_cpu_不调_work_mem",
    "label": "ANALYZE 相关表使计划回到 hash join,不扩 CPU 不调 work_mem"
   },
   "rel": "handled_by",
   "source": "cases/S2-20250908-CBST-灌数未ANALYZE计划跳变致CPU打满.md#处置",
   "case": "S2-20250908-CBST-灌数未ANALYZE计划跳变致CPU打满"
  }
 ]
};
