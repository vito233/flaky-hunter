# 数据字典（IDoFT + 结果 schema）

## IDoFT 7 列（已核验）

⚠️ **真实表头第三列带括号说明**（2026-10-12 用 `data/raw/py-data.csv` 实测，
本文档此前的简化写法是错的，已纠正）：

```
Project URL,SHA Detected,Pytest Test Name (PathToFile::TestClass::TestMethod or PathToFile::TestMethod),Category,Status,PR Link,Notes
```

解析层把表头括号部分砍掉再比对（`flaky_hunter.data.parse_idoft`），
另容忍文件头 BOM。

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

**裁剪后标签集（归因评测用，规范 §3.2，2026-10-13 定稿）**：
`OD / OD-Brit / NIO / NOD / OD-Vic / OTHER`（6 类）。Python 子集样本 <20 的
类别（ID 3 / UD 1；NDOD / NDOI / OSD / TZD 为 0 条）并入 `OTHER`，空标签（39）
排除。本评测集（`experiments/ground_truth_phase_a.csv` eval_eligible=true，
19 条 / 7 项目）分布：OD-Vic 12 / NIO 6 / OD 1；裁剪结果
`data/processed/labels.csv`（程序生成，`experiments/build_labels.py`）。
本评测集基线口径：多数类 63.2%（12/19）· OD vs 非 OD 68.4%（13/19），
与全量口径 49.5% / 72.7% 分开声明。

## Status 官方枚举

`空白 / Opened / Accepted / InspiredAFix / DeveloperWontFix / DeveloperFixed / Deleted /
Rejected / Skipped / MovedOrRenamed / RepoArchived / Deprecated / RepoDeleted /
MovedToGradle / FixedOrder / Unmaintained / RepoRenamed`

出现枚举外的值：解析层原样保留并记 warning，人工核查。

## 结果 JSON schema（规范 §3.1，代码即契约）

定义在 `flaky_hunter/schema.py`（pydantic），关键约束：

- `mode`: `replay | order | history`；`verdict`: `flaky | stable | inconclusive`
- `status`（目标项目环境）只许 `OK / DEPS_FAIL / TEST_NOT_FOUND / SHA_NOT_FOUND / TIMEOUT`
- **双探头**（2026-10-12 定）：`runs` = 换进程重跑（stable 判定的正典）；
  `count_runs` = 同进程 `--count` 探头（进程内状态污染型 NIO 的**阳性证据**，
  翻了才算，没翻不给 stable 背书 —— 修正 C）。yamicache 实例证明：
  只上换进程探头会系统性漏掉进程内 NIO。
- **顺序模式**（2026-10-13 定，`detect_order.py`）：`order_analysis` =
  顺序变换结果 —— `permutations_tried`（**有效**顺序数，没跑完的不算）、
  `failing_orders`、`victim`、`polluters`（最小污染集合，ddmin 收敛结果）、
  `minimized`（False = 撞预算上限，是上界不是最小）。顺序模式**不判 stable**：
  几个顺序没翻只给阴性观察。判定必须三条件全过：某顺序挂 ∧ 单独跑全过 ∧
  polluter 集合能复现。
- **历史模式**（2026-10-13 定，`detect_history.py`）：独立模型
  `HistoryResult` / `HistoryReport`（**不走** `DetectionResult` —— 历史模式
  没有 Docker env、没有检出 SHA，硬套会撒谎）。`test_id` = JUnit 的
  `classname::name`（classname 缺省时直接用 `name`，便于自攒历史沿用
  replay 路径形式）；`sequence` = 按时间序 P/F/S 串；`flip_transitions`
  = 相邻切换（历史主口径，FFFPPP=1）与 `flip_min_count` = 少数派计数
  （与 replay 可比，FFFPPP=2）。判定：翻转 → flaky；全过 ≥60 → stable
  （**历史口径封顶 medium**：历史 run 可能跨 commit/环境）；全挂 / 次数
  不足 → inconclusive（全挂 ≠ 翻转、0 次翻转 ≠ 稳定）。时间序 = 文件名
  典序，坏 XML 抛错不静默跳过。
- **validator 兜底**：宣称 `stable` 需要 `runs.total >= 170` 或 `confidence.level=high`
  （0 次翻转 ≠ 稳定，规范 §2.2；count_runs 不计入）
- `timestamp_utc` 必须带时区
- **results.csv 增列**（2026-10-13）：`mode`（replay / order / replay+order）、
  `polluters`（分号分隔）、`orders_tried`、`minimized`。合并口径
  （`metrics.combine_modes`）：任一模式报警即报警，两个模式抓不同机制，是互补。

## 负样本三档数据文件（N1 / N2 / N3）

| 文件 | 口径 | 规模 |
|---|---|---|
| `experiments/negatives_recheck.{csv,json}` | N3 v1：7×8 条 × 30 遍（2026-10-13；6 条混入已审计修正，真 N1 = 50） | 56 条 |
| `experiments/negatives_recheck_v2.{csv,json}` | N3 v2：7×20 条 × 30 遍（2026-10-14，抽样前全库对照） | 129 条 |
| `experiments/negatives_n2.{csv,json}` | N2 v1 = N1 + ≥10 遍无翻转全过 | 55 条 |
| `experiments/negatives_n2_v2.{csv,json}` | N2 v2（同口径，v2 数据重建） | 128 条 |

三分类口径：`stable_ok`（30 遍全过）/ `env_fail`（全挂 = 环境类失败，
排除并单独报数）/ `noisy`（有过有挂 = 真噪声）。N2 由
`build_n2_from_recheck.py` 走 JUnit XML → `detect_history` 管道重建
（内置两道对账；输入/输出可参数化，默认吃 v1 文件）。

## 硬数字（可直接引用，全部核验过）

| 数字 | 含义 |
|---|---|
| 1618 / 332 | IDoFT Python 记录数 / **去重仓库数**（2026-10-12 实测纠正） |
| 343 | 去重 **URL** 数 —— 不是项目数！20 个 URL 带 `/issues/N`、`/tree/...` 子路径，其中 11 个与裸 URL 同仓库 |
| 52.4% | 仓库已失效比例 |
| 77 / 43 | 干净真值条数 / 项目数 |
| **49.5%** | 多数类基线（全答 OD-Vic）—— 常量 `MAJORITY_CLASS_BASELINE` |
| **72.7%** | OD vs 非 OD 二分类基线 —— 常量 `OD_BINARY_BASELINE` |
| 170 遍 | 95% 置信确认不 flaky 所需重跑 —— 常量 `RERUN_FOR_95_CONFIDENCE` |
| 84% | Google 提交后失败中假警报占比 |
| 187.4 CPU 年 | OpenStack 重跑 flaky 测试的消耗 |
| 约 20% | 干净真值里能成功复现的比例 |
| 20 条 / 8 项目 | 阶段 A 实际复现数（`phase-a/experiments/results.csv`） |
