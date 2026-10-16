# GroupFlow：模板、工具与上下文按需加载

版本：v0.5，2026-10-16。历史 v0.4 数值保留，当前测量见末节。优化目标是可靠完成动作与保留权限语义；字符数、模型 token、总调用次数与通过率分别报告。

## 三层模板

1. `get_context(detail="summary")` / `list_templates` 给短目录，默认最多 12 条，并有分页。
2. `get_template` 读取单个模板的类型、策略、条件格式、显式预设与版本，包含必要说明，不带所有场地。
3. `get_context(plan=..., detail="full")` / `search_plan` 读取具体计划与场地。

默认目录有 gathering、trip、meeting、shared_tasks、project、evening_out、study_group、general_event。旧 ID 仍解析原规格，旧场景继续工作；新计划保存模板版本快照。surprise 和 weekend cottage 是预设。新增 YAML 后重启服务，不能用修改模板静默改变已有计划的策略。

## 逐步加载工具

服务端 `tools/list` 固定可发现 21 个工具，权限检查与 host 选择独立。本地模拟器普通回合给模型 17 个工具（包括 `get_project` 入口）；选中项目、进入项目主题或读到 project 模板后，加载 configure_project、upsert_work、manage_recurrence、repair_project 的详细 schema。同轮创建项目也会激活。

这是本地 host 策略；真实 Alexa+ 如何缓存/选取工具须由官方客户端验证。不能把工具未展示视为权限控制。MCP 始终没有代批准或授予角色工具。

## 摘要和聚焦

摘要包含计划索引、版本、未读数、待决数、本人开放任务和项目确认待办，以及每个计划最多 8 个关键事项的名称、ID、类型和策略。活动名称也参与路由，防止把“摘要没展开”当作“事情不存在”。索引只列短类型/策略，其他模板内容按需读。

无焦点且有多个计划时不发每个计划的完整 body；只有一个可见计划时自动聚焦。焦点只是提示，“我有什么要做”仍含其他可见计划中的本人任务与更新。UI 选中的 plan 通过 `/api/say` 传递；切换群组清除聊天与焦点，服务端数据继续保留。

项目计划最多发 12 个任务摘要，大项目使用 `get_project` 的工作流筛选和 offset/limit 分页。条件只发给本人；看不到别人的条件不代表别人没有限制。公开模板 tags 与场地 tags 都不能作为个人需求的证据。

## 分页、通知与 schema

- plan/group 不扩大 bearer 身份的权限，隐藏 ID 与不存在 ID 同样拒绝。
- `mark_read=true` 只消费本次返回的通知，截断的通知保留。
- 目录、待办、待决、影响、更新都有上限；`truncated` / `next_offset` 表示还有内容。
- `get_context` 的 API 默认 full 继续兼容；本地代理显式选 summary。
- 模型适配器移除重复 `$schema` / schema title，保留真正名为 title 的参数、类型、枚举、required、边界与引用。

## 说明长度与动作检查

默认说明补回私人便签、否定句、支持提案、转述退出、转述条件、开放清单与任务更新的例子。模型可以记录 tentative/request，服务端决定最终效果；不要把记录意图变成一次不必要的许可追问。代理最多执行一次纠正回合：未成功写入却宣称已完成、询问是否记录已经说出的请求、未经工具检查就猜测没有编辑权限、或回答 why 却没有解释证据。

quote 绑定当前真实输入，显式私人意图必须先建个人空间；否定值、非显式改名、与显式清单类型冲突的新增事项会被拒绝并给出恢复路径。这些是自然语言对齐保护；真正的数据可见性、owner 策略、人工批准和最终门禁仍由确定性代码执行。

## 请求大小：当前版本

真实本地 MCP 会话、Alice 首轮无历史、脚本模型和 API 序列化器，未调用外部模型。原始数值在 [context_measurement.json](context_measurement.json)。

| 指标 | 初始完整版本 | v0.4 当前 |
| --- | ---: | ---: |
| 普通回合工具数 | 14 | 17（服务端共 21） |
| 工具定义 JSON 字符 | 19,494 | 16,128 |
| system prompt 字符 | 6,919 | 5,842 |
| 首轮 context JSON 字符 | 5,149 | 4,915 |
| 完整首轮请求 JSON 字符 | 33,213 | 28,563 |
| 首轮完整计划数 | 3 | 0，改为 3 条索引 |

当前首轮字符减少约 14.0%。上一轮激进压缩曾到 19,622 字符，但真实评测为 97/108；这是历史快照，不能把其 40.9% 缩减比例当作当前结果。上表单位是字符，不能换称 token 或费用；按需读取可能增加往返，实际总量见 [EVALUATION.md](EVALUATION.md)。

小型说明对照采用同阶段代码、同 10 个重点场景：expanded 10/10，compact 9/10。样本很小、每项只跑一次，不证明统计显著或通用因果；保留较完整说明是正确性优先的工程选择。之后的修复还包括服务端规则、摘要和纠正逻辑，不能把全部提升都归因于描述长度。

## 复测

```powershell
python scripts/measure_context.py
python -m groupflow eval --out report.md --json report.json
python -m groupflow eval --only DA07,HM18,T01,E05,TR03,TR04,TM01,M01,N02,HM07 --descriptions compact --out compact.md --json compact.json
```

真实模型报告记录通过率、模型调用、输入/输出/缓存 token、耗时与所有失败。大型运行逐场景写 `.json.partial`，完成后写正式 JSON；partial 不能冒充完整验收。真实 Alexa+ 账号接入仍未验证。


## v0.5 信息结构补全（2026-10-16）

完整复测发现摘要缺少实时依赖证据，导致模型在其他人修改日期后漏报需要重新完成的预订。现在 your_tasks 返回当前可见阻塞事项的标准 id、标签、负责人和原因，并携带可见提醒；完成阻塞项后，这些信息随下一次读取更新。来源沿用原有可见性检查，不把隐藏依赖的信息带给其他人。

上下文还提供分页的本人 active groups 目录（id/name），plan_index 带 group_id，减少显示名与标准 ID 的混淆。目录不装载完整企业成员表。建计划的读者范围与活动参与者分开；对明确 just/only with 措辞，漏传 audience 会被拒绝，host 将实际原话绑定到 create_plan。此检查覆盖明确模式，不是任意自然语言读者意图的完备解析。

当前测量：默认工具 17 个，system prompt 5,842 字符，初始上下文 5,060 字符，首个完整请求 28,989 JSON 字符。默认仍是短目录、聚焦计划与按需读取。字符数不是 token；实际调用与 token 结果另见最终评测。

可视化从独立只读 MCP panel session 生成；它不作为额外数据库全文、图结构或管理员状态塞回模型。当前快照与实际上一轮工具消息分开，且标注是否送入后续模型请求。参见 [可视化与证据边界](MEMORY_VISUALIZATION.md)。
