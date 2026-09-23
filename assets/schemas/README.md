# 数据契约 · assets/schemas

这里是所有字段与枚举的**唯一定义**。`trip.json` 是唯一业务数据源；MD、HTML、图片卡都从它渲染。
机器可读版本：`trip.schema.json`（整包）、`audit.schema.json`、`manifest.schema.json`（JSON Schema 2020-12）。

通用规则：

- 时间一律带日期与时区的 ISO 8601：`2026-10-10T09:30:00+08:00`。日期用 `YYYY-MM-DD`。
- 金额保留币种与计价单位；分钟为整数。
- **未知用 `null` 或显式状态**，⛔ 不用空字符串、0、"正常开放"。
- 引用用稳定 ID；名称变化不改变对象身份。
- 叙述性内容（介绍、取舍解释、预约步骤、来源说明）也必须进结构化数据，否则"同一数据源"只能保证数字一致。
- ⛔ 任何字段都不得包含令牌、密钥、证件号。

## trip.json 顶层

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `schema_version` | string | 当前 `"1.1"`；读取兼容 `"1.0"`，交付前用 `migrate_trip.py` 迁移 |
| `trip_id` | string | `^[a-z0-9][a-z0-9-]{2,63}$`，建议 `2026-10-10-suzhou-3d` |
| `plan_version` | integer ≥ 1 | 修改或恢复由 `state_io.py` 递增，重渲染相同内容不递增 |
| `status` | enum | `draft` / `conditional` / `executable_as_of_check` |
| `user_confirmed` | boolean | 用户是否确认满意（与 `status` 独立） |
| `research_mode` | enum | `standard` / `deep` |
| `generated_at` / `checked_at` | datetime / datetime\|null | 生成时间、最近一次核查时间 |
| `request` | TripRequest | |
| `capabilities` | CapabilityReport[] | |
| `provider_results` | ProviderResult[] | 规范化的地图、天气、社媒等适配器结果；可选，凭据不得进入此字段 |
| `recheck_queue` | RecheckItem[] | 变更、过期、冲突或关键事项产生的待重查队列 |
| `privacy` | PrivacyState | `{classification: private\|delivery\|public, redacted_fields[]}` |
| `sources` | SourceRecord[] | |
| `facts` | FactRecord[] | |
| `places` | PlaceRecord[] | |
| `legs` | TravelLeg[] | |
| `itinerary` | Itinerary | |
| `decisions` | DecisionLog[] | |
| `changelog` | ChangeEntry[] | `{version, at, summary, affected_ids[]}` |

## TripRequest

| 字段 | 说明 |
| --- | --- |
| `origin` | `{name, region}`；region 可 null |
| `dates` | `{start, end, flexible, nights, return_by}`；`return_by` 最晚返回时刻（datetime\|null） |
| `timezone` | 主时区，如 `Asia/Shanghai` |
| `party` | `{adults, children_ages[], seniors, mobility_notes}` |
| `budget` | `{mode: total\|per_person, currency, amount_min, amount_max, budget_persons?, includes[], paid_amount, flexibility}` |
| `pace` | `{early_start, daily_hours, walking, rest_needs}`（自由文本或 null） |
| `interests` | `{like[], must[], optional[], avoid[]}`；`must[]` 每项是 `{label, place_id}`，place_id 可 null |
| `fixed_commitments` | `{id, kind, description, start, end, status: confirmed\|pending_payment\|intent, place_id}`；kind 为 `transport\|lodging\|booking\|meeting\|other` |
| `transport` | `{mode: public\|self_drive\|mixed\|unknown, luggage, notes}` |
| `special_needs` | string[] |
| `user_materials` | `{id, type, note, readable}` |
| `field_status` | `{<字段名>: user_stated\|extracted_pending\|default_suggested\|unknown}` |

`budget_persons` 是可选的明确预算人数（正整数或 null）。人均预算只有在币种相同且该人数明确时才能换算为全队上限，不从 seniors 与 adults 猜人数；已明确的 `itinerary.budget.hard_limit` 为该币种的全队上限。分类预算的 min/max 是全队预计未付范围，paid 是全队已付金额；同币种时分类 paid 合计须与 request.budget.paid_amount 一致。金额和时长不得为负数、NaN 或 Infinity，未知费用不当零，不自动折算外币。

## CapabilityReport

`{capability, availability: available|unavailable|unknown, permission: granted|denied|unknown|not_required, tool_id, verified_at, test_result, fallback}`

capability 取值：`web_search, web_fetch, file_read, file_write, script_exec, map_route, weather, image_fetch, image_gen, secret_store, tikhub`。

## ProviderResult、RecheckItem 与隐私

- `ProviderResult`：`{provider, capability, availability: available|unavailable|error, retrieved_at, coverage, source_url, items, warnings[], usage}`。它只保存适配器的规范化结果和用量摘要，不保存 API Key、Cookie 或 Authorization。
- `RecheckItem`：`{target_id, target_type: fact|leg|weather|transport, reasons[], action}`。队列进入 MD/H5；完成重查后更新对应事实或路段并重新生成队列。
- `PrivacyState` 只描述当前文件的分享级别和已删字段。私有 `trip.json` 始终是真源，`delivery/public` 是由 `privacy_export.py` 生成的新投影，不能反向覆盖真源。`public` 仍需人工复核自由文本中无法可靠自动识别的人名或地点隐私。

## SourceRecord

`{source_id, url, title, publisher, published_at, retrieved_at, source_type: official|map_tool|web|social|user_material, note}`

`url` 只允许 `http(s)://`，用户资料可为 null。

## FactRecord

`{fact_id, subject_id, subject_field?, claim, valid_for: {from, to}|null, status: verified|experience|estimate|unknown|conflict, source_ids[], excerpt, published_at, retrieved_at, stale_after?, conflicts[], method: tool|web|user|model}`

`subject_field` 可精确指向被该证据支持的业务字段；`stale_after` 到期后必须重新核查，不能继续标作当前有效。

`verified` 要求 `source_ids` 非空。

## PlaceRecord

| 字段 | 说明 |
| --- | --- |
| `place_id`, `name`, `city` | 必填 |
| `branch`, `entrance`, `address`, `region` | 分馆 / 推荐入口 / 地址 / 片区；无则 null |
| `duration_min`, `duration_max` | 建议游览分钟数范围（int\|null） |
| `opening_windows[]` | `{days[1-7]\|null, dates[]\|null, open "HH:MM", close "HH:MM", last_entry "HH:MM"\|null, note}`；**空数组表示未知**，不表示全天开放 |
| `booking` | `{required: true\|false\|null, channel, note}`；null = 未知 |
| `coords` | `{lat, lng}`\|null；只用于辅助分区 |
| `nav_link` | 已验证的导航链接\|null |
| `fact_ids[]` | 关联事实 |

## TravelLeg

`{leg_id, from_id, to_id, mode, route_group_id?, provider?, query_context?, depart_window, time_min, time_max, transfers, walking_min, evidence_level: tool_route|public_route|estimate|unknown, source_ids[], retrieved_at, note}`

`evidence_level = unknown` 时 `time_min/time_max` 必须为 null；否则必须是整数且 `time_min ≤ time_max`。

同一出发点/终点的多种交通方案用相同 `route_group_id` 分组；`provider` 记录实际使用的适配器，`query_context` 记录出发时段等查询语境。没有真实 Provider 结果时不得伪填 `tool_route`。

## Itinerary

| 字段 | 说明 |
| --- | --- |
| `summary` | `{title, destination, tagline}` |
| `plan_variants[]` | 对同一需求的可比较方案；最多一个 `selected=true` |
| `assumptions[]`, `tradeoffs[]`, `unmet[]` | 采用的假设 / 重要取舍 / 未满足项目（字符串数组） |
| `days[]` | Day；允许空数组作为未知日期骨架，但仅能处于 draft |
| `lodging` | `{strategy, regions[{name, pros, cons, budget_ref, suits_days}], change_lodging: bool, booked: {name, region, address, place_id}\|null}` |
| `transport_major[]` | `{id, kind, from, to, depart, arrive, status: booked\|to_book\|suggested, note, price_ref}` |
| `budget` | `{currency, hard_limit, categories[{name, min, max, paid, status: known\|estimate\|unknown, note}], note}` |
| `alternatives[]` | Alternative |
| `checklist[]` | ChecklistItem |
| `weather` | `{kind: forecast\|seasonal\|unavailable, entries[{date, summary, source_id}], note, prep[]}` |
| `risks[]` | 字符串数组 |
| `sources_note` | 资料说明 |

`PlanVariant` 为 `{variant_id, title, objective, selected, status, metrics, tradeoffs[]}`；`metrics` 至少包含 `travel_minutes, cost_max, lodging_changes, unknown_count`，未知值用 null。变体用于解释取舍，不允许各自另藏一份不受校验的行程正文。

### Day

`{day_id, date, weekday_check: bool, theme, region, lodging_base, intensity: light|moderate|heavy, items[], alternative_ids[], notes[]}`

### Item（行程项）

| 字段 | 说明 |
| --- | --- |
| `item_id` | 如 `day-01-visit-01` |
| `kind` | `visit / meal / transit / rest / checkin / checkout / transport_major / free / buffer` |
| `place_id` | visit / meal / checkin 必填；其他可 null |
| `title` | 展示名 |
| `planned_start`, `planned_end`, `timezone` | 带时区 |
| `locked` | 来自 fixed_commitments 的锁定项 |
| `booking_status` | `not_required / not_open / pending_user / user_claimed / confirmed / failed / unknown` |
| `admission_item_id` | 可选 string/null。馆内活动沿用同日同地点、已结束且中间未离场的直接 `visit` 项；引用项不得再沿用另一项。此时 `booking_status` 仅描述活动自身是否另需预约，入场条件从引用项保留；未确认的入场不能因活动无需预约而消失。跨日、离场后重入和交通项不得沿用。省略时沿用原契约：本项状态包括所需入场预约。 |
| `fact_ids[]` | visit 至少一条 |
| `incoming_leg_id` | 到达本项所用路段；同场所内步行可为 null 并在 notes 说明 |
| `alternative_id` | 对应替代方案 |
| `verification_status` | `verified / conditional / estimate / unknown / blocked` |
| `description` | 为什么安排、看什么、怎么游、为什么顺路 |
| `cost` | `{currency, min, max, status: known\|estimate\|unknown, unit}` |
| `tips[]`, `notes` | 邻近提示 |

`place_id` 表示活动实际位置与场所规则范围，不能借附近地标满足字段要求。院外餐饮应建立独立商户或明确的待选就餐片区（未知地址为 null、营业窗口为空、预约要求为 null）；真正馆内餐饮可引用入场项，且另行记录餐饮营业依据。程序验证结构关系，模型仍须核对标题、描述和地点是否相符，不能仅凭 ID 一致宣布语义正确。

### Alternative

`{alt_id, trigger, replaces_item_ids[], rejoin_at_item_id, description, place_id, requires_booking: bool, instant: bool, cost_delta, time_delta, verification_status}`

`instant = true` 时 `requires_booking` 必须为 false。

### ChecklistItem

`{check_id, priority: must|should|nice, deadline, action, status: todo|done|na, channel, related_item_ids[], if_unresolved}`

## DecisionLog

`{decision_id, topic, options[], user_choice, time, affected_ids[], note}`

## AuditReport（audit.v<N>.json）

`{trip_id, plan_version, checked_at, checks[{check_id, dimension, method: script|model|user, result: pass|fail|warn|skip, detail}], issues[{issue_id, severity: blocking|conditional|info, affected_ids[], evidence, fix_action, resolved}], summary{blocking, conditional, info}, recommended_status, script_version}`

## ArtifactManifest（manifest.v<N>.json）

`{trip_id, plan_version, content_hash, generated_at, files[{path, kind: md|html|card, sha256, bytes}], coverage{md{required, present, missing[]}, html{...}}, attribution{text, md, html}, renderer_version}`

## 枚举速查

- 方案状态：`draft` 探索草案 / `conditional` 条件式 / `executable_as_of_check` 截至核查时可执行
- 事实状态：`verified / experience / estimate / unknown / conflict`
- 预约状态：`not_required / not_open / pending_user / user_claimed / confirmed / failed / unknown`
- 路段证据：`tool_route / public_route / estimate / unknown`
- 行程项核查：`verified / conditional / estimate / unknown / blocked`
- 问题等级：`blocking / conditional / info`
- 字段来源：`user_stated / extracted_pending / default_suggested / unknown`
