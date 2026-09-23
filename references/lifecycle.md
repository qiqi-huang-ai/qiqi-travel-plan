# Schema、重查与隐私生命周期

在恢复旧档案、修改已交付行程、出发前复核或导出分享版时读本文件。

## 旧数据迁移

先生成新文件，不覆盖旧档案：

```bash
python3 scripts/migrate_trip.py old-trip.json --out trip.v1.1.json
```

当前迁移把 1.0 补成 1.1：加入 Provider 结果、重查队列、隐私分级与方案比较字段。未知版本拒绝迁移。

## 修改与依赖失效

使用 `state_io.py save --from`。地点变化会使关联事实、进出路段和使用该地点的行程项失效，并生成 `recheck_queue`。日期变化或恢复旧版触发全量日期相关失效。不要直接改完文案就交付。

## 出发前重查

```bash
python3 scripts/recheck_trip.py travel-plan/<trip_id>/trip.json \
  --out travel-plan/<trip_id>/recheck.json \
  --trip-out travel-plan/<trip_id>/rechecked-candidate.json
```

队列至少包括过期事实、冲突事实、必去项目、锁定项目和未知路段。`rechecked-candidate.json` 会把同一队列写入 `recheck_queue`，所以后续 MD/H5 能显示；命令拒绝覆盖私有源文件。重查完成后在候选文件更新事实状态、来源、`retrieved_at`、`stale_after`，再通过 `state_io.py save --from` 升版，随后校验和渲染。

## 隐私投影

私有 `trip.json` 是真源。分享时生成新文件：

```bash
python3 scripts/privacy_export.py trip.json --scope delivery --out delivery_snapshot.json
python3 scripts/privacy_export.py trip.json --scope public --redact "旅客姓名" --out public_summary.json
```

`public` 会隐藏用户资料、住宿名称与地址、私人来源 URL、Provider 原始结果、决策日志和锁定安排描述；文本扫描手机号、邮箱、中国大陆身份证号与带明确标签的订单/预订编号。人名无法仅靠正则可靠识别，用 `--redact` 逐个指定旅客姓名、联系人或其他精确文本（可重复参数），并在公开前人工复核自由文本。导出文件不得反向覆盖私有真源。

## 十二项验收

```bash
python3 scripts/verify_acceptance.py trip.json
```

该报告不替代 `validate_trip.py --check-outputs` 或人工证据复核。跨平台一项必须以 CI 三平台真实结果为准。
