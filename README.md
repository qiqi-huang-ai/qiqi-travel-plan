# Travel Plan Pro

一个以证据、约束和可复查状态为核心的旅行规划 Skill。它将行程需求、来源、事实、路线、预算、预约和出发前清单保存在同一份 `trip.json`，再生成同版本的 Markdown 与离线 HTML。

## 适用范围

- 目的地选择、多日行程、已订安排后的重排，以及出发前复查。
- 亲子、长者、预算、体力、交通和必去点等有明确约束的旅行。
- 需要可离线打开、可按分区和天数阅读的 HTML 行程页。

它不订票、不付款、不预约，也不会把未知的开放、票价、路线或余量写成事实。

## 安装与使用

将 `travel-plan-pro` 文件夹放到项目的 `.codex/skills/` 目录后，在 Codex 中说明旅行需求，或显式输入：

```text
请使用 $travel-plan-pro，帮我规划一次旅行。
```

Skill 会先记录需求和 TikHub 是否接入的选择，再进行调研、排程、校验与同源渲染。详细工作流见 [SKILL.md](SKILL.md)。

## 接口与隐私

核心功能只依赖 Python 标准库。高德和 TikHub 是可选增强：

- 不接入时，仍可交付带未知项和降级说明的基础方案。
- TikHub 只补充公开社媒中的体验线索，不替代官网、票务或运营方规则。
- 真实 TikHub 请求需要用户已在本机安全配置 `TIKHUB_API_KEY`，并显式确认 `--budget` 和 `--usage-file`；不得把密钥发送到聊天或写进 `trip.json`、HTML、日志。

## 发布前验证

在 Skill 根目录执行：

```bash
python3 scripts/release_check.py
```

该检查会运行行为测试，并检查必要文件、Markdown 链接、源码语法及发布垃圾文件。跨平台结果由 GitHub Actions 的 macOS、Windows、Linux 矩阵给出。

## 许可证

MIT。发布前请确认 [LICENSE](LICENSE) 中的版权主体与实际发布者一致。
