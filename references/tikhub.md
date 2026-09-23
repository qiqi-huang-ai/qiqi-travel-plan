# tikhub.md · 小红书体验线索的可选增强

TikHub 不是旅行规划的前置条件。它只在用户主动选择“接入”、提供自己的 key、同意本次网络调用和请求上限后，作为小红书公开内容的体验线索来源。未接入时继续产出基础方案，并在能力表中注明社媒增强未使用。

## 能力与边界

当前 adapter 面向 TikHub 的小红书 App V2 搜索、笔记详情、视频、评论与回复能力。其合适用途是发现近期排队、亲子体验、拍照点、用餐感受和备选地点；不适合单独证明开放、预约、票价、交通时刻或安全规则。规则性结论必须有官网、票务页、运营方公告或用户已确认凭证。

## 用户引导

1. 说明接入是可选项，以及会获得的体验线索和费用/隐私边界。
2. 请用户在本机环境变量中配置 `TIKHUB_API_KEY`，不要把 key 发送到聊天、写进 trip.json 或导出的 HTML。
3. 先用 dry-run 展示预计 URL、请求数量与预算；dry-run 不读取 key，也不联网。
4. 再由用户确认 live 调用和本次最大请求量；真实 CLI 必须显式传入 `--budget`。请求失败、认证失败或达到额度后立刻停止。
5. 适配器输出可直接作为统一 ProviderResult；用 `provider_pipeline.py` 导入候选行程时，结果只生成 `source_type=social` 和 `status=experience` 的事实，标注检索时间、样本局限和不适用的规则范围。

```bash
# 不联网：检查即将请求什么
python scripts/tikhub_client.py search "厦门 亲子" --dry-run --budget 10

# 用户已授权后才运行；预算是本次硬上限
TIKHUB_API_KEY='在终端本地设置' \
python scripts/tikhub_client.py search "厦门 亲子" --live --budget 10 --out /tmp/tikhub-search.json

# 运行不联网的适配器回归测试
python -m unittest discover -s scripts/tests -p 'test_*.py'
```

## 失败和异常响应

没有 key、非 2xx、返回不是 JSON、认证失败、用量耗尽或结构缺字段，都输出 `unavailable` 或 `error`，带原因但绝不回显 token。适配器不自动切换到其他平台、不绕过认证、不增加预算重试。调用账本只存请求计数和时间，不存 Authorization header、完整查询中的私人信息或原始凭据。

## 将结果写入计划

先保存原始响应的最小必要摘要，再创建可审计来源。社媒结论应使用“有若干公开笔记提及”“作为体验线索”这类表述，并说明不是统计结论。若来源过期、互相矛盾或样本过少，写 `unknown` / `conflict` 并加入 recheck queue；不得把它升级为 `verified`。
