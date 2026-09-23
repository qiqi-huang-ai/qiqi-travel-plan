# 本对话已确认范围对账

本文件把用户在本次开发中确认的范围，与当前可运行实现逐项对应。状态只按代码和测试判定，不按 README 声明判定。

| 已确认要点 | 当前实现 | 证据 | 状态 |
| --- | --- | --- | --- |
| 完整旅行规划内核 | 需求、地点、事实、来源、路段、行程、预约、预算、备选、清单、天气、风险均进入 `trip.json` | Schema + 语义校验 | 已完成 |
| 多数据源 Provider 层 | 高德、Open-Meteo、TikHub，加上宿主网页/用户资料能力契约；统一 ProviderResult；成功结果写入新候选行程 | `scripts/providers/`、`provider_pipeline.py`、`tikhub_client.py` | 已完成 |
| 高德地图 | 地理编码、地点搜索、驾车/步行/公交路线；规范化结果；显式 live/dry-run；持久请求账本；无 Key 明确降级 | `providers/amap.py` | 已实现；有 Key 实调待用户环境验证 |
| 天气接口 | Open-Meteo 逐日预报、覆盖范围、失败降级、季节参考边界 | `providers/weather.py` | 已完成并实调 |
| TikHub 小红书保持正式能力 | 搜索、图文/视频详情、一级/二级评论、分页、预算账本、脱敏、结构变化停止 | `tikhub_client.py` | 已完成；真实调用必须显式 `--live` |
| 小红书不替代官方规则 | social 单独支撑的事实不能标为 verified | Validator + A09 | 已完成 |
| 证据图谱与字段新鲜度 | `subject_field`、适用期、抓取时间、失效时间、冲突、来源、行程引用；可独立导出 | `evidence_graph.py` | 已完成 |
| 路线矩阵 | 同起终点多候选、Provider、查询语境、时间/步行/换乘比较、可解释推荐 | `route_matrix.py` + MD/H5 | 已完成 |
| 约束排程 | 开放时间、最晚入场、预约、路段上界、返程、预算、锁定项、必去项、跨午夜/时区 | `validate_trip.py` | 已完成 |
| 多方案比较 | 轻松版、预算版、少折返版的指标和取舍；最多选择一个 | PlanVariant + MD/H5 | 已完成 |
| 雨天与预约失败方案 | 触发、替换段、回接点、费用/时间影响、预约前置条件 | Alternative | 已完成 |
| 离线 H5 | 自包含单文件、移动端时间线、预算、清单、截止提示、重查、证据、深色偏好、出发模式、打印友好、无网络依赖 | 独立区块渲染器 `scripts/render_outputs.py` | 已完成 |
| 版本恢复 | 原子写入、不可变快照、diff、递增恢复、修改后失效 | `state_io.py` | 已完成 |
| Schema 迁移 | 1.0 → 1.1，新文件输出，不覆盖原文件 | `migrate_trip.py` | 已完成 |
| 隐私分层 | private 真源、delivery/public 投影、手机号/邮箱/证件号/带标签订单号/凭据扫描；public 删除决策日志；`--redact` 全局精确删除姓名等用户指定文本 | `privacy_export.py` | 已完成；自由文本公开前仍需人工复核 |
| 修改后自动重查 | 地点变化使事实、路段、行程项失效；产生候选新版与队列 | `state_io.py`、`recheck_trip.py` | 已完成 |
| macOS 路径修复 | `/var` 与 `/private/var` 统一 canonical path | 路径回归测试 | 已完成 |
| macOS/Windows/Linux | GitHub Actions 三系统、Python 3.10/3.13 | `.github/workflows/check.yml` | 已配置；本机只实跑 macOS |
| 无地图/天气仍能交付 | Provider 失败保留 unknown/estimate 与降级说明 | A08/A12 | 已完成 |
| 自动预订、付款、发消息 | 明确不在本 Skill 默认授权范围 | Operating contract | 有意不做 |
| 历史版本在单个 H5 内切换 | 单个 H5 保持一个不可变版本，避免伪切换；历史版本单独渲染 | delivery 边界 | 有意改为更可靠的实现 |
| 图片行程卡 | 保留可选交付规则和 manifest 类型；未在没有明确需求时消耗生图额度 | delivery 规则 | 按需能力，不是默认产物 |

## 直接运行验收

完整检查顺序：

```bash
python3 scripts/validate_trip.py <trip.json>
python3 scripts/provider_pipeline.py <trip.json> <provider-result.json> --target-id <place-or-leg-id> --out <candidate.json>
python3 scripts/route_matrix.py <trip.json> --out <route-matrix.json>
python3 scripts/evidence_graph.py <trip.json> --out <evidence-graph.json>
python3 scripts/render_outputs.py <trip.json>
python3 scripts/validate_trip.py <trip.json> --check-outputs
python3 scripts/verify_acceptance.py <trip.json>
```

验收报告中 A10 只有在三平台 CI 真实运行后才能从 `manual` 改为通过。
