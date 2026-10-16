# GroupFlow v0.4 完成报告

本轮已实现并验收可本地运行的 GroupFlow 协作版本：**8 类通用模板、显式预设、21 个 MCP 工具，以及长期项目的计划—修复—承诺—授权—提交闭环**。现有个人/聚会/出行场景继续保留；外部 Alexa+ 账号、企业 SSO、真实预订与支付不冒充已完成的生产集成。

## 交付与启动

- 项目源码：`F:\AmazonDevHack26\GroupFlow`。
- [完整架构、数据、工具、算法、周期与权限说明](F:/AmazonDevHack26/GroupFlow/docs/PROJECTS.md)。
- [先行实施计划](GroupFlow_Completion_Plan_zh-CN.md)。
- 当前浏览器验收演示：`http://127.0.0.1:8898`，使用单独的工作区 QA 数据库。

正式启动另一份企业 demo：

```powershell
cd F:\AmazonDevHack26\GroupFlow
.venv\Scripts\python.exe -m groupflow demo --world demo/enterprise.yaml --db data/enterprise.db
```

默认端口为 MCP 8787、页面 8788；已有服务占用时可传 `--port` / `--sim-port`。不用 `--fresh`，现有数据不会因正常启动而重置。

## 已完成的功能

| 范围 | 实现 |
| --- | --- |
| 模板 | gathering、trip、meeting、shared_tasks、project、evening_out、study_group、general_event；旧 ID 仍按旧定义解析 |
| 预设与窗口 | surprise 必须明确隐藏对象；weekend cottage 才附带三天合成行程；days 是 1–366 天计划窗口，区别确认有效期 |
| 版本与兼容 | 新计划冻结模板 JSON/版本；旧 schema 原子增量升级，未知格式保留并拒绝；既有数据库只用只读备份副本验收 |
| 长期协作 | 工作流、里程碑、ISO 截止日期、日工作单元、优先级、成本、依赖 DAG、人员与共享资源 |
| 周期 | DAILY/WEEKLY 子集，独立发生实例、稳定 recurrence_id、幂等展开、单次例外、ongoing 滚动窗口、跨月/DST |
| 修复 | 区间 CP-SAT 并行排程；先少改开始时间，再少移动分钟，最后优先级加权完成时间；独立验证与过期发布拦截 |
| 承诺与授权 | owner 工作承诺与 approver 阶段授权分开；本人登录、CSRF、一次性 challenge、摘要/条件/角色/期限绑定；MCP 无批准工具 |
| 提交 | 不同连接同时提交也在事务中重检跨项目预留；冲突方不能抢占已提交资源 |
| 页面 | 工作流筛选/分页、资源安排、保留多依赖与数量的编辑、个人承诺/授权、私人日历、工作区角色与历史答案 |
| 描述与上下文 | 默认说明补回有用例子；模板分层读、项目写工具按场景加载、摘要带关键值与跨计划本人待办，避免仅凭截断摘要下结论 |

AgreementOS + PlanRepair 的组合价值已经体现在可执行路径里：**修复安排不等于替人承诺；未变的精确范围保留有效承诺，变化范围重新确认，最终提交再检查真实的本地容量状态。**

## 验收证据

- 本地全量：**342/342 测试通过**；最后相关增量：**74/74**。前端类型检查与生产构建通过。
- 企业项目真实 API：**8/8**，包括工时编辑、周期、门禁、私人便签与不可代完成的工作。
- 最后定向 API：**20/20 场景、56/56 检查**；六项安全计数均为零。
- 浏览器真实完成两次提交：初始 19 个范围全签；工时变化后开始时间均保留，16 项沿用、2 项工作范围与 1 项阶段授权重签，再提交。浏览器控制台错误为零。
- 迁移副本中，13 人、5 组、6 计划、40 事项、10 事实、40 批准与 145 历史记录数量均保留，外键无错误，integrity_check=ok。原用户数据库没有被这些测试重置或写入。

![修复后重新确认并提交](GroupFlow_v04_browser_repaired.jpg)

## 真实模型结果：保留失败，不拼接一个 108/108

| 运行 | 场景通过 | 检查通过 | 调用/回合 | 输入 token/回合 | 平均秒 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Previous compact build | 97/108 | 198/209 | 2.137 | 13,051 | 4.32 |
| v0.4 round 1 | 102/108 | 201/209 | 2.033 | 14,990 | 3.80 |
| v0.4 round 2 | 106/108 | 207/209 | 2.052 | 15,431 | 4.33 |
| v0.4 round 3 | 105/108 | 206/209 | 2.005 | 15,397 | 3.75 |
| v0.4 full, before last two corrections | 104/108 | 205/209 | 1.882 | 15,116 | 3.46 |
| Expanded description probe | 10/10 | 27/27 | 2.111 | 15,620 | 4.73 |
| Compact description probe | 9/10 | 26/27 | 2.037 | 13,911 | 4.54 |
| Final targeted regressions x2 | 20/20 | 56/56 | 1.982 | 15,787 | 3.57 |

最后一次全量原始结果为 **104/108**，执行时尚未加入最后的日期锚定与缺失事项纠正。4 个失败中，2 个是真问题（错误推算日期、把摘要截断当成事项不存在），已修复；2 个是把公开确认名单中的姓名当作私人原因披露的断言误报，已用更准确的“具体人 + 私人原因归因”检查替换，并增加正反测试。原始报告全部保留。

随后的 10 个重点场景各复测两次得到 20/20。**没有再执行一次修复后完整 108 场景，不能声称修复后已完整跑出 108/108。** 小型说明对照 expanded 10/10、compact 9/10，样本仅各一次，不能证明统计显著或把所有收益归因于描述加长。当前保留较完整默认说明，是正确性优先的工程决定。

普通回合默认 17 个工具；服务端可发现全部 21 个，进入项目场景再加载四个项目写工具。当前 system prompt 为 5,842 字符、首轮完整请求 28,563 JSON 字符（比原 33,213 少约 14.0%）。字符不能当作 token 或计费量，实际 token/调用/缓存和耗时见表及原始 JSON。

## 性能与边界

原路线型活动修复器继续在 **16,384 个候选组合**切换 enumeration / CP-SAT；这是之前 180 个配对基准场景的结果，不是活动数量，也不是所有模型的通用阈值。新项目使用区间模型，单独测量：10 人、实验室容量 3、42 天窗口、依赖链合成数据中，400 个工作单元在默认预算下约 **1.10 秒**完成并证明最优。不同日期/日历/约束的实例可能更慢；该数据不是通用延迟承诺。

项目范围限制为每窗口最多 400 个开放工作单元，每次最多展开 200 个周期实例；每项是一个工作日内、15 分钟网格、最多 8 小时的连续区块。更长工作需拆分。周期不支持全部 RRULE 功能。SQLite、公开 demo 账号、本地工作区角色不等于生产企业多租户/SSO。确认过期或条件/权限/内容改变时仍需重新修复和确认。

## 文件与来源

- [验证与 token 指标](GroupFlow_v04_validation_metrics.json)
- [最后定向 API 原始报告](GroupFlow_v04_verified_regressions.json)
- [企业项目 API 原始报告](GroupFlow_v04_project_api_final.json)
- [迁移检查](GroupFlow_v04_migration_check.json)
- [浏览器与数据库检查](GroupFlow_v04_browser_validation.json)
- [本轮代码差异](GroupFlow_v04_changes.patch)

官方核实：`2025-11-25` 是 MCP 协议版本。[Hackathon rules](https://amazonappdev2026.devpost.com/rules) 与 [FAQ](https://amazonappdev2026.devpost.com/details/faqs) 区分自托管 MCP 路径、模拟体验和受限预览工具；本项目实现并测试真实 [Streamable HTTP](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)，通过自己的网页 host 演示，未声称真实 Alexa+ 接入认证。区间模型参考 [OR-Tools](https://developers.google.com/optimization/scheduling/job_shop)，周期语义参考 [RFC5545](https://www.rfc-editor.org/rfc/rfc5545)。
