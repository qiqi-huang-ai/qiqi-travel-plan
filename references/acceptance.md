# 十二项成熟度验收与证据边界

这份表用于发布前对账，不替代 `validate_trip.py`、人工来源复核或 CI。验收必须区分“当前行程数据通过”“Skill 行为有自动测试”“外部 Provider 已真实调用”。

| ID | 验收合同 | 实现证据 | 自动验证 | 仍需人工/外部验证 |
| --- | --- | --- | --- | --- |
| A01 | 主行程项有地点、时间、证据状态 | Schema + 语义校验 | `verify_acceptance.py`、验证器测试 | 事实本身是否正确 |
| A02 | 跨地点移动有路段或明确未知 | `legs[]`、`incoming_leg_id` | 引用完整性与路线测试 | 实时路况、临时停运 |
| A03 | 未知预算不当作 0 | `status=unknown` 时 min/max 为 null | 预算边界测试 | 实时价格、汇率 |
| A04 | 预约失败、闭馆、超预算、赶不上返程会阻断 | `semantic_checks` | 四类反例测试 | 官方规则是否已更新 |
| A05 | Markdown 与 H5 同源 | 内容指纹、manifest、可见内容覆盖率 | `--check-outputs`；验收脚本会读取真实文件 | 页面视觉主观质量 |
| A06 | 修改后事实失效并进入重查队列 | `invalidate_changed_dependencies`、`recheck_trip.py` | 验收 CLI 实际运行依赖失效与候选新版行为测试；未运行时为 manual | 重查来源的实际可访问性 |
| A07 | 旧版可恢复且不覆盖历史 | 不可变快照、递增 restore | 验收 CLI 实际运行恢复与不可覆盖行为测试；未运行时为 manual | 外部备份策略 |
| A08 | Provider 失败可降级且不伪装成功 | 统一 ProviderResult、fallback | Amap 无 Key、天气结构错误、TikHub 错误测试 | 有 Key 的 Amap 真实请求 |
| A09 | 小红书只作体验证据 | social 不能单独成为 verified | 来源类型验收、TikHub 隐私测试 | 样本代表性 |
| A10 | macOS / Windows / Linux 完整测试 | GitHub Actions 三系统、Python 3.10/3.13 矩阵 | 仅 CI 真实运行后通过 | 本机只能确认 macOS |
| A11 | 数据与产物无凭据、隐私可分级 | preflight、secret scan、public projection | 凭据注入与公开投影测试 | 用户自填自由文本的语义复核 |
| A12 | 无地图/天气仍能交付基础方案 | capability fallback、未知状态 | Provider 不可用与示例 E2E | 降级方案的体验质量 |

## 发布门槛

1. `python3 -m unittest discover -s scripts/tests -v` 全绿。
2. `python3 scripts/validate_trip.py <trip.json>` 无 blocking。
3. 渲染后 `python3 scripts/validate_trip.py <trip.json> --check-outputs` 通过。
4. `python3 scripts/verify_acceptance.py <trip.json>` 中 A01–A09、A11–A12 通过；A10 只能由三平台 CI 结果判定。
5. 用户要公开分享时，从私有真源生成 `public` 投影，再对投影单独校验和渲染。
6. Amap、TikHub 等需要 Key 或可能计费的 Provider，只有真实成功调用才记为可用；dry-run、mock、README 声明都不算。

## 不伪造的能力边界

- 单文件 H5 是某个版本的不可变快照，不伪装成云端协作系统。
- Skill 不替用户订票、付款或预约；它提供截止时间、渠道和失败分支。
- 地图/天气/社媒都是可插拔数据源，不是核心排程的唯一依赖。
- Windows/Linux 未运行 CI 前，A10 必须保持 `manual`，不得因工作流文件存在就声称通过。
