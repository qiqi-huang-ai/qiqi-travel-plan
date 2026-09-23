# delivery.md · 可复查、可离线使用的旅行交付

交付物不是手工改出来的两份文档，而是同一份 `trip.json` 的两个视图：便于复制和存档的 Markdown，以及可直接打开的单文件 HTML。二者与 audit、manifest、版本快照一起构成可追溯包。

## 交付门槛

先运行数据校验；有 blocker 时停止渲染为“可执行版”。然后执行渲染和产物校验。manifest 必须绑定 trip_id、plan_version、内容 hash、MD/HTML 的相对路径、sha256、文件大小和内容覆盖结果。只要数据变过，旧产物即过期，必须重新渲染。

```bash
python scripts/validate_trip.py travel-plan/<trip>/trip.json
python scripts/render_outputs.py travel-plan/<trip>/trip.json
python scripts/validate_trip.py travel-plan/<trip>/trip.json --check-outputs
```

## 内容结构

HTML 和 MD 均应包含：计划状态与最后核查时间、旅行概览、锁定安排、每天按时间排序的活动/移动/休息、路线和不确定性、住宿与行李、预算范围、预约与出发前清单、替代方案、复查队列、事实来源摘要及免责声明。未知、估算和待办必须可见，不能藏在脚注。

## HTML 规则

HTML 使用内嵌 CSS/JS，离线打开不请求远程字体、图片、地图、分析脚本或接口。交互只能帮助阅读（折叠、时间线、倒计时、深浅色），不改变业务数据。外链须是安全 http(s) 链接，所有用户文本都需转义。当前视觉为“岛屿手册 / 潮汐纸纹”：墨绿信息层级、暖珊瑚动作色、低对比海潮背景；不依赖其他 Skill 的品牌或组件。

HTML 的信息架构分为“固定导航 + 主内容”：桌面端左侧是可点击的分区索引，移动端降级为横向索引；每日行程提供“全部 / D1 / D2 …”筛选；滚动时当前分区会高亮。总览中用离线 CSS 旅程骨架表达天数，清单支持本地浏览器勾选并显示准备度，返回顶部和打印样式只改变阅读方式，不写回 `trip.json`。这些是阅读层能力，业务数据仍以 `trip.json` 为唯一来源。

## 隐私与配图

默认私有包可包含用户已授权的订单摘要；公开版先通过 `privacy_export.py` 去除联系人、订单号、精确住址、用户材料和 provider 原始结果。配图只用用户授权、明确可商用或生成素材；无法确认许可时使用 CSS 装饰，不下载或嵌入不明来源图片。

## 修改、版本与恢复

修改建议在同目录的独立副本上完成，再用 `state_io.py save --from` 保存。保存会递增版本、留存旧快照、失效依赖的核查信息并提示重新渲染。恢复旧版不是覆盖当前文件：它会先保留当前快照，再生成一个新的当前版本，且所有原有 verified 状态都需要复查。

```bash
cp travel-plan/<trip>/trip.json travel-plan/<trip>/edited.json
# 编辑 edited.json 后：
python scripts/state_io.py save travel-plan/<trip>/trip.json --from travel-plan/<trip>/edited.json --summary "调整住宿片区"
python scripts/state_io.py versions travel-plan/<trip>
python scripts/state_io.py restore travel-plan/<trip> 2
```

没有脚本能力时，仍按同一原则：保留原始数据、记录版本/时间/改动原因、重新列出受影响事实，并明确交付物不再是当前数据的验证渲染。
