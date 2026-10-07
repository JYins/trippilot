# SQLite vs PostgreSQL：审计日志的选型与迁移路径

日期：2026-10-08 ｜ 状态：已决策，SQLite 已落地（trippilot/memory/audit.py）

## 背景

记忆写入链路（抽取→Memory Gate→store）缺 durable 审计。
敏感写入的人工确认是 Day 1 铁律，但确认记录只活在内存 state 里，
进程重启即失——"确认过"这件事本身不可验证，铁律缺一环。
需要一处极轻的持久化，记"谁、何时、确认/写入/更新/删除了哪条记忆"。

约束：单用户本地原型，无运维人力；AGENTS.md 第 7 节简历白名单里
数据库一项是 PostgreSQL/SQLite；不许过度设计。

## 候选方案

1. **SQLite（stdlib sqlite3），审计文件与 qdrant 数据放同一目录**：
   单表 memory_events + (user_id, ts) 索引，记 written / updated /
   forgotten / forgotten_all。代价：单写者锁、无服务端权限体系。
2. **PostgreSQL**：并发写、角色权限、JSONB、pgvector 都是现成的。
   代价：要运维 server、账号、备份；原型阶段这些能力零收益。
3. **不落库**（现状，审计只放内存/trace）：代价：敏感确认无 durable
   审计，铁律"可验证"缺一环。

## 选择

方案 1。落点：trippilot/memory/audit.py（MemoryAudit，只用标准库，
不引入 ORM），PreferenceStore 默认在数据目录下建 audit.db，
remember/update/forget/forget_all 成功后记审计事件。

## 为什么

**现在用 SQLite**：单用户单进程，SQLite 零运维、零依赖（stdlib），
文件就在 ~/.trippilot 下，和 qdrant 本地文件模式的架构一致
（"本地文件"是当前整个存储层的统一答案）。PostgreSQL 的
server/账号/备份全是原型阶段用不上的成本——为用不上的能力付运维税，
不值。

**多用户/服务端阶段换 PostgreSQL**：SQLite 的单写者锁扛不住并发写，
也没有角色权限管不住多用户数据——那是明确的切换触发线，不是"可能"。
迁移路径：audit.py 只用可移植 SQL（INTEGER PRIMARY KEY
AUTOINCREMENT → SERIAL，ts 存 ISO8601 TEXT），届时换连接工厂 +
微调 DDL，log_event / list_events 调用方不动。

**不选 ORM**：单表，stdlib 够用；SQLAlchemy 是过度设计，
多一个重依赖换不来任何东西。

**audit 失败诚实抛，不静默吞**（AGENTS.md 第 2 节）：sqlite 本地写失败
基本意味着磁盘满之类"一切都坏了"的情况，吞掉只会丢审计还假装成功。
敢抛的底气：重试是幂等的——dedup 机制（见
20261008-memory-update-dedup.md）让重复 remember 变成 update
而不是 duplicate，不会写出两条。

**诚实记下放弃的东西**：PostgreSQL 的并发/权限/JSONB/pgvector
想象空间；承认 SQLite 就是"现在的够用"，不包装成"未来也不用换"。
也承认审计表和 qdrant 数据同一信任边界（本机文件）：现在没问题，
上服务端切 PostgreSQL 时这张表必须加访问控制，代码注释里已写明。

## 对 eval 的影响

- eval 每个 case 用临时目录建 PreferenceStore，audit.db 落临时目录，
  随 TemporaryDirectory 回收；33 条回归要求零变化。
- 新增 tests/test_memory_audit.py：written/updated/forgotten 事件记录、
  list_events 倒序与 limit。
- 新增回归维度：审计事件是 Day 1 铁律的 durable 证据，以后动
  remember/update/forget 任一方法必须同步更新审计测试。
