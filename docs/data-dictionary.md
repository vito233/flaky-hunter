# 数据字典（IDoFT + 结果 schema）

## IDoFT 7 列（已核验）

```
Project URL, SHA Detected, Pytest Test Name, Category, Status, PR Link, Notes
```

| 列 | 说明 | 怎么用 |
|---|---|---|
| `Project URL` | GitHub 仓库地址 | 解析 `owner/repo` |
| `SHA Detected` | 检出时的 commit | **复现时 checkout 这个值**（铁律 2） |
| `Pytest Test Name` | `路径::类::方法` 或 `路径::方法` | 定位目标测试 |
| `Category` | 见下，可 `;` 分隔多标签 | 归因评测的标签（真值） |
| `Status` | 见下 | 筛干净真值用 `Accepted` / `DeveloperFixed` |
| `PR Link` | 修复 PR | 复现率低时的降级日志来源 |
| `Notes` | 补充信息 | 含 `latest commit`、`randomly`/`seed` 线索 |

> 已观测脏数据：`Opened␠`（尾随空格）1 条、`OD-Vic` 误落 Status 列 3 条 —— 解析层已统一
> strip 并留痕（`flaky_hunter/data.py` 的 `warnings` 字段）。
> `Notes` 里的 `latest commit` 年份**上限是 2022**：筛"2023 年后活跃"必须现查 GitHub API。

## Category 官方枚举（照抄 IDoFT README，不改）

`OD` / `OD-Brit` / `OD-Vic` / `ID` / `ID-HtF` / `NIO` / `NOD` / `NDOD` / `NDOI` / `UD` / `OSD` / `TZD`

| 值 | 白话 |
|---|---|
| `OD` | 顺序依赖 |
| `OD-Brit` | 顺序依赖 · brittle 型（被别人踩后自己挂） |
| `OD-Vic` | 顺序依赖 · victim 型（受害者） |
| `ID` / `ID-HtF` | 实现依赖（Nondex 检出）/ 且难修 |
| `NIO` | 第一次跑过、第二次跑挂 |
| `NOD` | 非确定性（⚠️ 不是"非顺序依赖"，那是 `NDOI`） |
| `NDOD` / `NDOI` | 既看顺序又非确定 / 非确定但与顺序无关 |
| `UD` / `OSD` / `TZD` | 未知依赖 / 依赖操作系统 / 依赖时区 |

分布（Python 子集实算）：OD-Vic 801 / OD-Brit 322 / NOD 235 / NIO 160 / OD 54 / 空 39 / ID 3 / UD 1

## Status 官方枚举

`空白 / Opened / Accepted / InspiredAFix / DeveloperWontFix / DeveloperFixed / Deleted /
Rejected / Skipped / MovedOrRenamed / RepoArchived / Deprecated / RepoDeleted /
MovedToGradle / FixedOrder / Unmaintained / RepoRenamed`

出现枚举外的值：解析层原样保留并记 warning，人工核查。

## 结果 JSON schema（规范 §3.1，代码即契约）

定义在 `flaky_hunter/schema.py`（pydantic），关键约束：

- `mode`: `replay | order | history`；`verdict`: `flaky | stable | inconclusive`
- `status`（目标项目环境）只许 `OK / DEPS_FAIL / TEST_NOT_FOUND / SHA_NOT_FOUND / TIMEOUT`
- **validator 兜底**：宣称 `stable` 需要 `runs.total >= 170` 或 `confidence.level=high`
  （0 次翻转 ≠ 稳定，规范 §2.2）
- `timestamp_utc` 必须带时区

## 硬数字（可直接引用，全部核验过）

| 数字 | 含义 |
|---|---|
| 1618 / 343 | IDoFT Python 记录数 / 项目数 |
| 52.4% | 仓库已失效比例 |
| 77 / 43 | 干净真值条数 / 项目数 |
| **49.5%** | 多数类基线（全答 OD-Vic）—— 常量 `MAJORITY_CLASS_BASELINE` |
| **72.7%** | OD vs 非 OD 二分类基线 —— 常量 `OD_BINARY_BASELINE` |
| 170 遍 | 95% 置信确认不 flaky 所需重跑 —— 常量 `RERUN_FOR_95_CONFIDENCE` |
| 84% | Google 提交后失败中假警报占比 |
| 187.4 CPU 年 | OpenStack 重跑 flaky 测试的消耗 |
| 约 20% | 干净真值里能成功复现的比例 |
| 20 条 / 8 项目 | 阶段 A 实际复现数（`phase-a/experiments/results.csv`） |
