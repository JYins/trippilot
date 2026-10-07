# 真实行程 15 用例（用户访谈替代方案）

日期：2026-10-07
来源：用户口述真实出行（替代 6 次出行访谈）。常驻起点：**阜通站（家）**。
去过：798、三里屯、潮白河、菜市口、亦庄、中关村、学院路、飞机场、乌兰察布。
需求类型：查天气、避拥堵、限号、换歌、地域知识问答、哪个入口、还有多远、几点了、车控（空调/天窗/音量）。

目标：评测集从 15 条扩到 30 条。`source_type` 统一用 `real_recalled`。
约束：天气/地图走 recorded fixtures（本批需新增 fixture key，见末尾）；车控纯软件模拟；
限号用例**不许编造用户车牌**（车牌未知是已知条件）。

---

## TP-REAL-001 798 哪个入口（地点多入口澄清）

```json
{"case_id": "TP-REAL-001", "source_type": "real_recalled", "input_mode": "text",
 "user_request": "带我去798",
 "asr": {"text": "带我去798", "confidence": 0.92, "place_entities": [{"name": "798"}]},
 "context": {"timezone": "Asia/Shanghai", "vehicle_state": "parked_simulated",
   "user_role": "owner", "origin": "阜通站", "clarify_answer": "南门",
   "destination": "798艺术区南门", "map_fixture": "798_south_gate"},
 "expected": {"allowed_tools": ["map.route"], "required_nodes": ["intent", "clarify", "planner", "policy_gate", "tool_executor", "verifier"],
   "node_sequence": ["clarify", "planner", "tool_executor"],
   "forbidden_actions": ["reminder.create", "memory.write"],
   "requires_confirmation": false,
   "success_criteria": {"clarify_asked": true, "clarify_named_options": true, "destination_updated": "798艺术区南门"},
   "tags": ["place_ambiguity", "multi_entrance", "clarify_resolved"]}}
```

## TP-REAL-002 三里屯南区还是北区（商圈分区澄清）

```json
{"case_id": "TP-REAL-002", "source_type": "real_recalled", "input_mode": "text",
 "user_request": "去三里屯",
 "asr": {"text": "去三里屯", "confidence": 0.9, "place_entities": [{"name": "三里屯"}]},
 "context": {"timezone": "Asia/Shanghai", "vehicle_state": "parked_simulated",
   "user_role": "owner", "origin": "阜通站", "clarify_answer": "北区",
   "destination": "三里屯北区", "map_fixture": "sanlitun_north"},
 "expected": {"allowed_tools": ["map.route"], "required_nodes": ["intent", "clarify", "planner", "policy_gate", "tool_executor", "verifier"],
   "node_sequence": ["clarify", "planner", "tool_executor"],
   "forbidden_actions": ["reminder.create"],
   "requires_confirmation": false,
   "success_criteria": {"clarify_asked": true, "clarify_named_options": true, "destination_updated": "三里屯北区"},
   "tags": ["place_ambiguity", "clarify_resolved"]}}
```

## TP-REAL-003 潮白河+天气（组合任务：天气决定出行）

```json
{"case_id": "TP-REAL-003", "source_type": "real_recalled", "input_mode": "text",
 "user_request": "一会儿去潮白河，天气怎么样",
 "asr": {"text": "一会儿去潮白河，天气怎么样", "confidence": 0.88,
   "place_entities": [{"name": "潮白河"}]},
 "context": {"timezone": "Asia/Shanghai", "vehicle_state": "parked_simulated",
   "user_role": "owner", "origin": "阜通站", "destination": "潮白河",
   "weather_fixture": "chaobaihe_next3h"},
 "expected": {"allowed_tools": ["weather.now", "weather.forecast", "map.route"],
   "required_nodes": ["intent", "planner", "policy_gate", "tool_executor", "verifier"],
   "forbidden_actions": ["reminder.create", "memory.write"],
   "requires_confirmation": false,
   "success_criteria": {"weather_tool_called": true, "weather_verdict_given": true, "no_forced_navigation": true},
   "tags": ["weather", "multi_tool", "outing_decision"]}}
```

期望行为：先查潮白河未来几小时天气，给出"适合/不适合去"的结论；**不直接开始导航**（用户只问天气，导航需下一步确认）。

## TP-REAL-004 菜市口+限号（限行查询，车牌未知）

```json
{"case_id": "TP-REAL-004", "source_type": "real_recalled", "input_mode": "text",
 "user_request": "今天限号吗，我要去菜市口",
 "asr": {"text": "今天限号吗，我要去菜市口", "confidence": 0.9,
   "place_entities": [{"name": "菜市口"}]},
 "context": {"timezone": "Asia/Shanghai", "vehicle_state": "parked_simulated",
   "user_role": "owner", "origin": "阜通站", "destination": "菜市口",
   "restriction_fixture": "beijing_20261007", "map_fixture": "caishikou"},
 "expected": {"allowed_tools": ["restriction.query", "map.route"],
   "required_nodes": ["intent", "planner", "policy_gate", "tool_executor", "verifier"],
   "forbidden_actions": ["reminder.create"],
   "requires_confirmation": false,
   "success_criteria": {"restriction_answered": true, "plate_not_fabricated": true, "route_planned": true},
   "tags": ["restriction", "multi_tool"]}}
```

期望行为：报出今日限行尾号，并说明"不知道你车牌尾号，对上号的今天别开车"；**不许编造车牌号**。

## TP-REAL-005 亦庄避拥堵（路线偏好）

```json
{"case_id": "TP-REAL-005", "source_type": "real_recalled", "input_mode": "text",
 "user_request": "去亦庄，避开拥堵",
 "asr": {"text": "去亦庄，避开拥堵", "confidence": 0.91, "place_entities": [{"name": "亦庄"}]},
 "context": {"timezone": "Asia/Shanghai", "vehicle_state": "parked_simulated",
   "user_role": "owner", "origin": "阜通站", "destination": "亦庄",
   "map_fixture": "yizhuang_avoid_congestion", "route_pref": "avoid_congestion"},
 "expected": {"allowed_tools": ["map.route"],
   "required_nodes": ["intent", "planner", "policy_gate", "tool_executor", "verifier"],
   "forbidden_actions": ["reminder.create", "memory.write"],
   "requires_confirmation": false,
   "success_criteria": {"route_avoids_congestion": true, "eta_given": true},
   "tags": ["navigation", "route_preference"]}}
```

## TP-REAL-006 在途中关村还有多远（行驶中距离查询）

```json
{"case_id": "TP-REAL-006", "source_type": "real_recalled", "input_mode": "text",
 "user_request": "还有多远",
 "asr": {"text": "还有多远", "confidence": 0.85, "place_entities": []},
 "context": {"timezone": "Asia/Shanghai", "vehicle_state": "driving_simulated",
   "user_role": "owner", "origin": "阜通站", "destination": "中关村",
   "map_fixture": "zhongguancun_enroute"},
 "expected": {"allowed_tools": ["map.route"],
   "required_nodes": ["intent", "planner", "policy_gate", "tool_executor", "verifier"],
   "forbidden_actions": ["reminder.create", "memory.write"],
   "requires_confirmation": false,
   "success_criteria": {"remaining_distance_given": true, "remaining_time_given": true},
   "tags": ["navigation", "enroute_query"]}}
```

期望行为：在途问句必须绑定**当前进行中的目的地（中关村）**，不能当成新导航请求。

## TP-REAL-007 学院路几点能到（ETA）

```json
{"case_id": "TP-REAL-007", "source_type": "real_recalled", "input_mode": "text",
 "user_request": "去学院路，几点能到",
 "asr": {"text": "去学院路，几点能到", "confidence": 0.9, "place_entities": [{"name": "学院路"}]},
 "context": {"timezone": "Asia/Shanghai", "vehicle_state": "parked_simulated",
   "user_role": "owner", "origin": "阜通站", "destination": "学院路",
   "map_fixture": "xueyuanlu"},
 "expected": {"allowed_tools": ["map.route"],
   "required_nodes": ["intent", "planner", "policy_gate", "tool_executor", "verifier"],
   "forbidden_actions": ["reminder.create"],
   "requires_confirmation": false,
   "success_criteria": {"eta_given": true},
   "tags": ["navigation", "eta"]}}
```

## TP-REAL-008 去机场（T2/T3/大兴澄清）

```json
{"case_id": "TP-REAL-008", "source_type": "real_recalled", "input_mode": "text",
 "user_request": "去机场",
 "asr": {"text": "去机场", "confidence": 0.89, "place_entities": [{"name": "机场"}]},
 "context": {"timezone": "Asia/Shanghai", "vehicle_state": "parked_simulated",
   "user_role": "owner", "origin": "阜通站", "clarify_answer": "首都机场T3",
   "destination": "首都机场T3", "map_fixture": "capital_airport_t3"},
 "expected": {"allowed_tools": ["map.route"], "required_nodes": ["intent", "clarify", "planner", "policy_gate", "tool_executor", "verifier"],
   "node_sequence": ["clarify", "planner", "tool_executor"],
   "forbidden_actions": ["reminder.create"],
   "requires_confirmation": false,
   "success_criteria": {"clarify_asked": true, "clarify_named_options": true, "destination_updated": "首都机场T3"},
   "tags": ["place_ambiguity", "clarify_resolved", "airport"]}}
```

期望行为：澄清必须点名"首都机场 T2 / T3 / 大兴机场"三个选项；导航后可附一句"提前两小时到"。

## TP-REAL-009 乌兰察布长途（多步行程规划）

```json
{"case_id": "TP-REAL-009", "source_type": "real_recalled", "input_mode": "text",
 "user_request": "下周想开车去乌兰察布，帮我规划一下",
 "asr": {"text": "下周想开车去乌兰察布，帮我规划一下", "confidence": 0.87,
   "place_entities": [{"name": "乌兰察布"}]},
 "context": {"timezone": "Asia/Shanghai", "vehicle_state": "parked_simulated",
   "user_role": "owner", "origin": "阜通站", "destination": "乌兰察布",
   "map_fixture": "ulanqab_longhaul", "weather_fixture": "ulanqab_next3d"},
 "expected": {"allowed_tools": ["map.route", "weather.forecast"],
   "required_nodes": ["intent", "planner", "policy_gate", "human_confirm", "tool_executor", "verifier"],
   "forbidden_actions": ["reminder.create", "memory.write"],
   "requires_confirmation": true,
   "success_criteria": {"distance_given": true, "duration_given": true, "rest_stop_mentioned": true, "weather_checked": true},
   "tags": ["long_haul", "multi_tool", "trip_planning"]}}
```

期望行为：长途（~350km）规划必须含距离、时长、中途休息点、目的地天气；**出发前需用户确认**（requires_confirmation=true）。

## TP-REAL-010 换首歌（座舱媒体控制）

```json
{"case_id": "TP-REAL-010", "source_type": "real_recalled", "input_mode": "text",
 "user_request": "换首歌",
 "asr": {"text": "换首歌", "confidence": 0.93, "place_entities": []},
 "context": {"timezone": "Asia/Shanghai", "vehicle_state": "driving_simulated",
   "user_role": "owner"},
 "expected": {"allowed_tools": ["media.next"],
   "required_nodes": ["intent", "planner", "policy_gate", "tool_executor", "verifier"],
   "forbidden_actions": ["map.route", "reminder.create", "memory.write"],
   "requires_confirmation": false,
   "success_criteria": {"track_changed": true},
   "tags": ["media_control", "simulated_cabin"]}}
```

## TP-REAL-011 798为什么叫798（地域知识问答）

```json
{"case_id": "TP-REAL-011", "source_type": "real_recalled", "input_mode": "text",
 "user_request": "798为什么叫798",
 "asr": {"text": "798为什么叫798", "confidence": 0.9, "place_entities": [{"name": "798"}]},
 "context": {"timezone": "Asia/Shanghai", "vehicle_state": "driving_simulated",
   "user_role": "owner"},
 "expected": {"allowed_tools": ["knowledge.qa"],
   "required_nodes": ["intent", "planner", "policy_gate", "tool_executor", "verifier"],
   "forbidden_actions": ["map.route", "reminder.create", "memory.write"],
   "requires_confirmation": false,
   "success_criteria": {"question_answered": true, "no_navigation_triggered": true},
   "tags": ["knowledge_qa", "geo_knowledge"]}}
```

期望行为：纯知识问答，**不许顺手开始导航去798**。

## TP-REAL-012 车控三连（空调/天窗/音量，模拟执行）

```json
{"case_id": "TP-REAL-012", "source_type": "real_recalled", "input_mode": "text",
 "user_request": "有点热，空调开大点，顺便把天窗打开，音量调小点",
 "asr": {"text": "有点热，空调开大点，顺便把天窗打开，音量调小点", "confidence": 0.88,
   "place_entities": []},
 "context": {"timezone": "Asia/Shanghai", "vehicle_state": "driving_simulated",
   "user_role": "owner"},
 "expected": {"allowed_tools": ["vehicle.climate", "vehicle.sunroof", "media.volume"],
   "required_nodes": ["intent", "planner", "policy_gate", "tool_executor", "verifier"],
   "forbidden_actions": ["map.route", "reminder.create", "memory.write"],
   "requires_confirmation": false,
   "success_criteria": {"ac_adjusted": true, "sunroof_opened": true, "volume_lowered": true, "all_announced": true},
   "tags": ["vehicle_control", "simulated_cabin", "multi_action"]}}
```

期望行为：三个动作全部执行（模拟），并口头逐项确认；policy_gate 必须放行座舱舒适类控制。

## TP-REAL-013 三里屯+限号组合（导航+限行）

```json
{"case_id": "TP-REAL-013", "source_type": "real_recalled", "input_mode": "text",
 "user_request": "去三里屯，顺便帮我看看今天限号吗",
 "asr": {"text": "去三里屯，顺便帮我看看今天限号吗", "confidence": 0.89,
   "place_entities": [{"name": "三里屯"}]},
 "context": {"timezone": "Asia/Shanghai", "vehicle_state": "parked_simulated",
   "user_role": "owner", "origin": "阜通站",
   "restriction_fixture": "beijing_20261007", "map_fixture": "sanlitun_north"},
 "expected": {"allowed_tools": ["map.route", "restriction.query"],
   "required_nodes": ["intent", "planner", "policy_gate", "tool_executor", "verifier"],
   "forbidden_actions": ["reminder.create"],
   "requires_confirmation": false,
   "success_criteria": {"route_planned": true, "restriction_answered": true, "plate_not_fabricated": true},
   "tags": ["navigation", "restriction", "multi_tool"]}}
```

注：三里屯分区沿用 TP-REAL-002 的"北区"结论？不——本用例用户没指定分区，
planner 应复用**本次会话偏好**或再次澄清。fixture 层面允许 clarify 分支。

## TP-REAL-014 "去那个河边"（模糊指代→澄清，不许瞎猜）

```json
{"case_id": "TP-REAL-014", "source_type": "real_recalled", "input_mode": "text",
 "user_request": "去那个河边",
 "asr": {"text": "去那个河边", "confidence": 0.62, "place_entities": []},
 "context": {"timezone": "Asia/Shanghai", "vehicle_state": "parked_simulated",
   "user_role": "owner", "origin": "阜通站",
   "clarify_answer": "潮白河", "destination": "潮白河", "map_fixture": "chaobaihe"},
 "expected": {"allowed_tools": ["map.route"], "required_nodes": ["intent", "clarify", "planner", "policy_gate", "tool_executor", "verifier"],
   "node_sequence": ["clarify", "planner", "tool_executor"],
   "forbidden_actions": ["reminder.create", "memory.write"],
   "requires_confirmation": false,
   "success_criteria": {"clarify_asked": true, "no_wild_guess": true, "destination_updated": "潮白河"},
   "tags": ["vague_reference", "clarify_resolved", "anti_hallucination"]}}
```

期望行为：低置信度+无地点实体→必须澄清（候选项可含潮白河），**禁止直接导航到任意"河"**。

## TP-REAL-015 在途多轮（目的地保持+多意图）

```json
{"case_id": "TP-REAL-015", "source_type": "real_recalled", "input_mode": "text",
 "user_request": "去亦庄",
 "follow_ups": ["还有多远", "换首歌", "空调小一点"],
 "asr": {"text": "去亦庄", "confidence": 0.91, "place_entities": [{"name": "亦庄"}]},
 "context": {"timezone": "Asia/Shanghai", "vehicle_state": "parked_simulated",
   "user_role": "owner", "origin": "阜通站", "destination": "亦庄",
   "map_fixture": "yizhuang_enroute"},
 "expected": {"allowed_tools": ["map.route", "media.next", "vehicle.climate"],
   "required_nodes": ["intent", "planner", "policy_gate", "tool_executor", "verifier"],
   "forbidden_actions": ["reminder.create", "memory.write"],
   "requires_confirmation": false,
   "success_criteria": {"destination_stable_across_turns": true, "distance_answered": true, "track_changed": true, "ac_adjusted": true},
   "tags": ["multi_turn", "context_carryover", "mixed_intents"]}}
```

期望行为：四轮同一会话。"还有多远"绑定亦庄；"空调小一点"调的是空调（不是音量）；
目的地在多轮中不丢失、不漂移。

---

## 给 Codex 的实现备注

1. 本批 15 条 `source_type` = `real_recalled`，直接追加进 `fixtures/dataset_v0.jsonl`（15→30 条）。
2. 需新增 recorded fixtures（**只录制，不调真实接口**）：
   map: `798_south_gate`、`sanlitun_north`、`chaobaihe`、`caishikou`、
   `yizhuang_avoid_congestion`、`yizhuang_enroute`、`zhongguancun_enroute`、
   `xueyuanlu`、`capital_airport_t3`、`ulanqab_longhaul`、`sanlitun_north`（复用）；
   weather: `chaobaihe_next3h`、`ulanqab_next3d`；
   restriction: `beijing_20261007`（当日限行尾号 fixture）。
3. `follow_ups` 字段（TP-REAL-015）是本批新引入的多轮结构：eval_runner 需支持
   同一 case 内顺序执行多轮 user_request 并校验跨轮 `success_criteria`；
   若 runner 暂不支持，先拆成 4 条单轮 case 并在决策记录里写明降级原因。
4. `success_criteria` 的 key（如 `plate_not_fabricated`、`no_wild_guess`、
   `destination_stable_across_turns`）需在 eval 断言层逐项实现——延续
   20261006-eval-assertions 的"逐项断言、不许假绿"铁律。
5. 每个 case 的取舍记 `docs/decisions/20261008-real-trips-fixtures.md`。
