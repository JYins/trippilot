# TripPilot 全库代码审查（2026-10-06）

审查范围：`trippilot/` 主包全部、`eval/`、`tests/`，并抽查 `fixtures/`。本次只审查，不修改实现。基线验证为 `.venv/bin/pytest -q`：78 passed；`eval/eval_runner.py`：12/12 PASS。后者的全绿不能排除下列假绿问题。

## blocker（正确性）

1. `trippilot/api.py:68-70,93-107`、`trippilot/graph.py:205-220,309-313`
   - 问题：确认协议只接收客户端布尔值和可原样伪造的 `pending_memory_confirms`，既不绑定上一轮、具体工具参数或候选摘要，也不保存待确认工具调用，攻击者可借任意 `confirm=true` 写入伪造敏感记忆，而正常用户仅回复“确认”时又无法继续上一轮工具调用。
   - 修改方向：服务端按 session 保存带摘要和一次性 nonce 的待确认动作，确认时只恢复该快照并拒绝客户端提交候选正文，同时增加“参数被改动”“重复确认”“空文本确认”的 API 反例测试。

2. `trippilot/api.py:99-106`
   - 问题：API 将每个请求都硬编码为同一个已认证 `owner`，导致 ABAC 身份规则在真实入口完全失效，所有调用者还共享 `owner` 的长期记忆命名空间。
   - 修改方向：从可信认证层构造 `user_attributes` 并以真实用户 ID 隔离记忆；在没有认证能力的原型阶段至少明确限制为单用户本地接口，不能伪装成已认证多用户入口。

3. `trippilot/policy_gate.py:146-151`、`trippilot/tools/tools.py:137-140`
   - 问题：`_action_of()` 未映射 `append`，所以有副作用的 `trip_log.append` 被当成 `read`，未认证用户也会获准执行；`TripLogTool` 又对任意 `trip_log.*` 名称都直接追加，形成确定的授权绕过。
   - 修改方向：用显式的完整工具名到资源/动作映射并默认拒绝未知操作，工具实现也必须逐个校验支持的操作；补未认证 append 和伪造后缀的反例测试。

4. `trippilot/policy_gate.py:88-92`、`trippilot/graph.py:194-201`
   - 问题：行驶中三选项只产生 `degrade` 决策，图却把它等同于 allow，原调用的 `option_count=3` 原样进入执行器，安全降级从未发生。
   - 修改方向：让门禁返回并执行受限参数或在执行前确定性改写为最多两个短选项，并断言实际调用参数和最终输出都已降级，而不只断言 reason code。

5. `trippilot/memory/store.py:130-149`、`trippilot/policy_gate.py:182-195`
   - 问题：Memory Gate 完全信任调用方提供的 `sensitivity`，直接调用 `remember()` 时把家庭住址标成默认 `normal` 即可无确认写盘，因此所谓“唯一入口先过 Gate”并没有阻止敏感内容绕过。
   - 修改方向：在 Gate 内基于内容做保守的独立敏感分类或要求不可伪造的分类结果，未知/疑似 PII 默认确认，并为默认参数下的住址、证件号和公司精确地址补绕过测试。

6. `trippilot/memory/extract.py:19-23,53-58`
   - 问题：抽取层的敏感兜底只识别少量关键词和手机号，例如“记住北京市朝阳区阜通东大街6号”会被标为 normal 并自动写入，精确地址保护存在漏口。
   - 修改方向：把精确地址、证件号等识别集中到独立的确定性 PII 分类函数并由 Memory Gate 再判一次，fixture 必须加入不含“我家/住址”字样的敏感反例。

7. `trippilot/graph.py:120-134,165-171`
   - 问题：`memory_recall` 虽然查出偏好并存到 state，planner 的 context 却没有 `preferences`，所以“偏好召回”对规划和回答没有任何作用，现有测试只验证查到了数据而误称为 inject。
   - 修改方向：定义最小且脱敏的偏好上下文并显式传给 planner，同时用 spy LLM 断言模型输入确实包含召回结果及用户隔离信息。

8. `trippilot/graph.py:150-162`、`trippilot/policy_gate.py:125-134`
   - 问题：任意非空 `clarify_answer` 都会设置 `clarify_resolved`，既不校验答案属于歧义选项，也不把答案写回 destination，随后 Policy Gate 直接跳过地点歧义检查，可在仍使用错误目的地时放行。
   - 修改方向：把澄清结果解析并绑定到原歧义实体，只有合法选项才能清除歧义且必须同步更新计划上下文；补无关回答和答案/目的地不一致的反例。

9. `trippilot/tools/tools.py:97-117`
   - 问题：删除提醒不清理 `_idempotency`，且新 ID 用 `len(_store)+1` 生成，删除非末尾提醒后新建会覆盖仍存在的提醒，再次提交已删除内容还会返回指向不存在对象的“成功”结果。
   - 修改方向：使用不可复用 ID，删除时同步处理幂等映射并明确“删除后重建”的语义；增加建两条、删第一条、再建一条及重放第一条的回归测试。

10. `trippilot/graph.py:351-365`
    - 问题：恢复节点只保留失败工具名并把原参数全部替换成 `fixture/default/session_id`，提醒重试必然丢失 content/time，其他工具也可能悄悄改查不同地点或时间。
    - 修改方向：从失败结果对应的原 `ToolCall` 复制完整参数，只对明确允许回退的 fixture 字段做有记录的改写，并测试副作用工具不会因恢复而改变语义。

11. `trippilot/tools/base.py:37-51`、`fixtures/recorded/map_default.json:1`
    - 问题：录制工具按 fixture 名返回固定数据但不核对调用参数，当前“去国贸”的用例会成功返回目的地为“中关村”的路线，verifier 仍宣称完成。
    - 修改方向：fixture 应携带并校验请求关键字段，参数不匹配就明确失败；为 origin、destination、area 与响应不一致加入断言。

12. `trippilot/graph.py:255-281`
    - 问题：deny 路由直接进入 verifier 后因“没有执行禁用工具”而得到 `ok=true`，最终回复“本次没有执行工具调用”，既未把任务未完成标为失败，也未向用户说明拒绝原因，违反 verifier 必须诚实标记失败的规范。
    - 修改方向：分开记录“安全策略执行成功”和“用户任务完成失败”，deny 时输出稳定的可读原因并让任务完成状态为失败。

13. `eval/eval_runner.py:92-123`
    - 问题：评测读取了 `success_criteria` 却从不逐项断言，只检查节点、工具集合和 reason code，因此错误路线、未实际降级、未创建提醒、未抑制重复或未写/拒记忆都可能获得 PASS。
    - 修改方向：为每个受支持的 success criterion 写确定性断言并对未知 criterion 直接报错，判定应检查结果 payload、实际调用参数和记忆最终状态。

14. `eval/eval_runner.py:46-63`
    - 问题：敏感记忆审计只要求写入前出现过任意一次 `human_confirm`，没有核对确认对象，工具确认可被错误地当作敏感记忆确认，掩盖“搭便车确认”。
    - 修改方向：给每个待确认项生成稳定 ID/摘要，在 trace 中关联 request、prompt、confirmation 和 write 四个事件，并按同一 ID 校验顺序。

15. `eval/harness_audit.py:103-142`
    - 问题：文档声称检查“model-visible ⟺ logged”，实现只做模型输入到 trace 的单向字符串包含检查，并明确忽略 planner context，既抓不到未传入模型的召回偏好，也抓不到日志额外泄露的敏感数据。
    - 修改方向：把不变式拆成两个诚实命名的检查，结构化比对允许进入模型的字段与 trace 字段，并加入 context 缺失和敏感字段多记两类反例。

16. `eval/judge.py:56-68`、`trippilot/graph.py:380-385`
    - 问题：DeterministicJudge 把“访问 clarify 节点”当成“发生澄清”，但图中每条轨迹都必经 clarify，普通无需澄清的成功请求因此固定被打 0.6，advisory 分数系统性失真。
    - 修改方向：依据 `clarify_checked.need_clarify`/停止状态等事件语义评分，不要依据节点是否出现；用真实普通轨迹而非手写缺少 clarify 的轨迹补测试。

17. `trippilot/api.py:45-53,85-88`
    - 问题：脱敏只认敏感键和一种“中文+数字号”地址模式，顶层 `text/asr_text` 中的手机号、生日、提醒正文及不带门牌号的住址都会原样写入日志，和模块声称的敏感日志脱敏不符。
    - 修改方向：请求原文默认不落日志或只记长度/摘要，确需记录时使用统一 PII 脱敏器并对 text、ASR、policy detail 做覆盖测试。

18. `trippilot/api.py:60-70,99-104`
    - 问题：请求模型没有约束 `asr_confidence` 和 `vehicle_state`，非法值直到端点内部构造 `ASRResult/TripPilotState` 才抛 Pydantic 异常，容易把客户端输入错误变成 500。
    - 修改方向：在 `TurnRequest` 直接使用受限类型和 `Field(ge=0, le=1)`，让 FastAPI 稳定返回 422，并增加边界值及非法枚举测试。

## suggestion（人味与清晰度）

1. `trippilot/graph.py:106-403`
   - 问题：`build_graph()` 超过 290 行并内嵌十个节点，澄清话术、记忆写盘、恢复策略等业务逻辑都堆在编排文件里，初级工程师很难单独理解和测试某个节点。
   - 修改方向：按现有领域边界把节点函数移到少量直白模块，`graph.py` 只保留依赖组装、边和路由，避免新增 manager/handler 层。

2. `trippilot/policy_gate.py:16-18,30-32,106-108,154-156,178-180`、`trippilot/tools/tools.py:12-14,37-39,52-54`
   - 问题：大段分隔线和“ABAC 主入口/工具调用级检查”等注释只是复述紧随其后的函数与类，视觉噪音很像模板生成代码。
   - 修改方向：删除纯导航式横幅，保留真正解释规则来源、阈值取舍和安全原因的注释，让函数名与文件结构承担“是什么”。

3. `trippilot/policy_gate.py:20-24`、`trippilot/state.py:19-20`、`trippilot/tools/tools.py:6`
   - 问题：`READ_ONLY_TOOLS`、`SIDE_EFFECT_TOOLS`、`CONFIRM_ALWAYS`、`InteractionMode` 和 `json` import 都未使用，其中前三个还会让读者误以为授权逻辑由这些集合驱动。
   - 修改方向：删除死代码，或让一份显式工具策略表真正成为 `_action_of` 和确认规则的单一来源。

4. `trippilot/api.py:67-70`
   - 问题：Pydantic 请求模型使用 `{}`、`[]` 作为字段默认值，虽然当前版本会复制默认值，但写法容易让读者怀疑共享可变状态。
   - 修改方向：统一改成 `Field(default_factory=dict/list)`，把“每个请求独立容器”的意图写进类型定义而非依赖框架细节。

5. `tests/test_memory_extract.py:43-51`、`eval/harness_audit.py:170-173`
   - 问题：赋值 lambda 配 `# noqa`、三元嵌套行续接等 clever 写法节省不了多少代码，却增加阅读停顿。
   - 修改方向：改成有名字的小函数和普通 if/elif，优先让测试表达意图一眼可读。

6. `tests/test_graph_memory.py:15-22`、`tests/test_harness_audit.py:60-70`
   - 问题：测试名和注释使用“inject/模型可见”的强表述，实际只断言 state 中存在召回结果与 count 对得上，名字替实现做了尚未成立的保证。
   - 修改方向：要么把测试改名为“recall stores preferences”，要么真正用 recording LLM 断言 planner 输入，测试名称只描述它实际证明的事实。

7. `tests/test_graph_smoke.py:1-110`、`tests/test_policy_gate.py:1-112`
   - 问题：测试集中在 happy path 和单函数 reason code，没有覆盖 API 多轮协议、门禁到执行器的参数保持、未知操作默认拒绝、删除后幂等性等跨层边界，因此全绿给出了过强安全感。
   - 修改方向：按用户可见流程补少量高价值端到端反例，并为 `api.py`、`llm.py`、`voice.py`、`tools/base.py` 建立与模块对应的测试文件。

8. `eval/eval_runner.py:66-152`、`eval/harness_audit.py:209-235`
   - 问题：临时目录和 Qdrant store 依赖手动 close/cleanup，图执行或 judge 前置逻辑一旦抛错就会泄漏资源，评测器也缺少失败上下文。
   - 修改方向：用 `with TemporaryDirectory()` 和 `try/finally` 管理 store，并在用例级异常中保留 case_id 后明确失败。
