# 审计记录 · IDoFT 全库对照发现真值口径错误（2026-10-13 深夜三）

> 起因：起草 IDoFT PR 材料时，对"11 条集外 victim"做 IDoFT 全库（py-data.csv）
> 逐条对照 —— 结果牵出两处真值口径错误。本文档是证据链与修正方案，
> **修正动作等 vito 确认后执行**。

---

## 一、根因（一个 bug，两处表现）

`filter_recorded(candidates, recorded)` 的 `recorded` 参数，两个调用方传的
都是**阶段 A 真值表**（ground_truth_phase_a.csv，eval_eligible=true 子集，
约 20 条）：

- `run_eval.py:280` —— C1 评测的负样本剔除
- `run_negatives_recheck.py:251`（注释自称"与 C1 同一套 N1 逻辑"）—— N3 复核

而 N1 的定义是「同项目、同 SHA 下 **IDoFT 全库** 未记录的测试」
（negatives.py docstring / methodology.md §N1）。**实现对照了 20 条，
定义要求对照 py-data.csv 全库（Python 部分全部记录）** —— 实现与定义不符。

阶段 A 真值表是抽样复现的产物（约 20 条），IDoFT 全库是 1618 条记录 /
332 项目 —— 两者差两个数量级，凡"IDoFT 有、真值表无"的测试都会被误判为
"未记录"流进负样本池。

## 二、实测影响（程序对照，精确匹配 + SHA 核对）

### 1. C1 主评测：1 条 TN 实为 FN

`mtik00/yamicache tests/test_gc.py::test_deco_timeout`

- IDoFT 全库：**NOD** 类，SHA `1109d08b`（与我们扫描 SHA 相同）
- 阶段 A：不在真值表（未复现，NOD 需要 NonDex 类工具）
- 我们的判定：replay 100 遍全过（stable）+ order 4 顺序全过 → 未检出
- 按 Recall@IDoFT 口径：**该行是 FN，不是 TN**

修正后（合并口径 = replay ∪ order）：

| 指标 | 原报 | 修正后 | 基线 |
|---|---|---|---|
| 混淆矩阵 | TP=19 FP=0 FN=0 TN=14 | **TP=19 FP=0 FN=1 TN=13** | — |
| 精确率（下界） | 100.0% | **100.0%（不变，FP=0）** | 57.6% |
| Recall@IDoFT | 100.0% | **95.0%（19/20）** | 100% |
| F1 | 1.000 | **0.974** | 0.731 |

replay 独立：TP=11 FN=9 TN=13 → Recall 55.0%，F1 0.710（原 0.733）
order 独立：TP=12 FN=8 TN=13 → Recall 60.0%（原 63.2%），F1 0.750（原 0.774）
（精确重算以修正脚本输出为准，上表为手工推算，落地前程序复核）

**定性**：test_deco_timeout 是 NOD 型（NonDex 类工具检出的非确定性 API
行为）。我们没有 NonDex，replay（换进程）/order（顺序变换）对其机制性
盲区 —— 是**工具覆盖边界**，不是检测器 bug。这也解释了为什么 19 条正样本
裁剪后 NOD 为 0（阶段 A 复现不了 NOD）。但口径上它就是 IDoFT 已记录的
flaky，Recall 必须如实降。

### 2. N3 复核：56 条"负样本"中 6 条为 IDoFT 已记录（不传播）

| 测试 | IDoFT 类别 | N3 结果（换进程 30 遍） |
|---|---|---|
| yamicache test_cached | OD-Vic | 全过 |
| yamicache test_keyed_cached | OD-Vic | 全过 |
| yamicache test_avoid_collision | NIO | 全过 |
| freezegun test_can_ignore_email_module | OD-Vic | 全过 |
| freezegun test_ignore_errors_in_start[TypeError] | OD-Vic | 全过 |
| python-fs test_addpath | NIO | 全过 |

- 与评测集 14 条 TN 的交集 = 0 → **主指标不受 N3 污染影响**
- 6 条"30 遍全过"与其 IDoFT 记录**不矛盾**：换进程单跑对 NIO（进程内
  污染）与 OD-Vic（victim 单独跑能过）机制性盲区 —— 与它们被 IDoFT
  检出的机制同源。这 6 条反而成了"N3 口径盲区"的实证（limitations 16 条
  的同款逻辑适用到 N3）
- **修正后的 N3 叙事**：真 N1 = 50 条，全过 50/50 = 纯度下界 100%（数值
  不变，证据基础从 55 缩到 50）；6 条 IDoFT 已知 + 1 条 env_fail 单独报数
- **危险被躲过的一面**：若这 6 条在 30 遍里翻了面，会被记成"N1 噪声"
  污染噪声率 —— 实际全过，数值结论侥幸未坏，但方法漏洞必须修

### 3. "11 条集外 victim"表述错误（10 条 IDoFT 其实有记录）

| 组 | 条数 | IDoFT 全库对照 |
|---|---|---|
| yamicache test_cached / test_keyed_cached | 2 | **已有记录**（OD-Vic，同 SHA） |
| freezegun test_class_import/test_datetimes 等 | 8 | **已有记录**（OD-Vic，同 SHA） |
| Pyro5 test_daemon.py::TestDaemon::testHandshakeDenied | 1 | **真·未记录** |

- "集外"的真实含义 = 不在 19 条评测子集，不是"IDoFT 没记录"
- 10 条是**对 IDoFT 已知记录的独立复现**（同 SHA 同类别，附 polluter 归因
  证据）—— 价值是复现与归因增强，不是新发现
- 真新发现 = Pyro5 1 条（IDoFT 提交候选）

## 三、修正方案（等确认后执行）

1. **修 bug**：`run_eval.py` / `run_negatives_recheck.py` 的 `recorded`
   改为 py-data.csv 全库（键 = (owner/repo, test_name)）；negatives.py
   文档注明对照范围要求；补单测（全库对照 + 撞库用例）
2. **重算主指标**：y_true 语义改为 IDoFT 全库口径；test_deco_timeout
   行修正（0→1，标注 NOD-unreproducible）；重跑 metrics 汇总（数据不动，
   只改分类与聚合）；防漂移测试同步（19/20）
3. **文档更正**：c1-order.md / c1-replay-mvp.md 加更正注记；limitations
   第 13 条改写 + 新增"N1 对照范围 bug"条；README 19/19 → 19/20 全链
   表述；项目现状总结；白话日志追加更正段（旧段不改）
4. **N3 口径重构**：negatives_recheck.json 加 post-hoc 修正字段；
   limitations 第 14 条改写
5. **memory 更正**：MEMORY.md / 2026-10-13.md
6. **IDoFT PR 策略随之更新**（原计划起草的素材作废重排）：
   - 新增 1 条：Pyro5 testHandshakeDenied（detected，附三条件证据）
   - 可选增强：10 条已记录测试的 polluter 归因 → 按 IDoFT 惯例开
     notes issue（现有 Notes 全空，归因是增量价值）
   - test_deco_timeout 不提交任何修改（我们没复现，无权改 Status）

## 四、教训（写进踩坑日志）

1. **"全库对照"必须在实现层兜底**：真值表是裁剪产物，任何"未记录"
   判定都要对数据全集做，不能对子集做 —— 和"过滤后集合 ≠ 全集"是
   同一类错误。
2. **这次怎么抓到的**：起草对外提交材料时被迫做全库逐条对照 ——
   对外动作的严谨要求倒逼出对内错误的暴露。如果只对内用，这个 bug
   可能一直藏着。
3. ** 数字链条里每一步的"对照范围"都要显式声明**：20 条 vs 1618 条
   差两个数量级，靠 docstring 的一句话挡不住实现走样。
