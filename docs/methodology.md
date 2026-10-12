# 方法论（人读版）

> 萃取自规划文件夹 `寒假实习-项目规范.md` §1 / §2。这里是**仓库内**的常读版；
> 两边不一致时以规范为准并回改这里。

## 铁律（违反 = 全部数字作废）

1. **原始数据只读**：`data/raw/` 永不修改，加工产物落 `data/processed/`
2. **复现按 `SHA Detected` 检出**，不按 HEAD；检完 `git rev-parse HEAD` 校验
3. **准确率旁边必须有基线**（多数类 49.5% / OD 二分类 72.7%，常量在 `flaky_hunter/baselines.py`）

## 检测路径必须分流（v1 的最大技术错误）

IDoFT Python 子集里 **OD 家族（OD / OD-Vic / OD-Brit）占 72.7%**。顺序依赖的含义是：
**在固定顺序下结果是确定的** —— 拿 `pytest --count=20` 去跑它，20 遍结果一样，
会得出"很稳定"的错误结论。

| Category | 占比 | 走哪条路 | 为什么 |
|---|---|---|---|
| OD / OD-Vic / OD-Brit | 72.7% | **顺序变换 + 二分最小化** | 固定顺序重跑无效 |
| NOD（非确定性） | 14.5% | 固定顺序高重跑 | 跑够次数就能暴露 |
| NIO（非幂等） | 9.9% | 固定顺序高重跑 | 定义就是"第一次过、第二次挂" |
| ID / UD | 0.3% | 两条都试 | 样本太少，无统计意义 |
| Category 为空 | 2.4% | 两条都试 | 取能复现的那条 |

- **顺序变换**：用 `pytest-random-order` / `pytest-randomly`，不自己实现排列逻辑
- **二分最小化**：抄 iDFlakies 的流程（随机序 → 失败序 → 二分收敛最小 polluter 集合），别自己发明

## 重跑次数与置信度

- 外部事实（22,352 项目 / 876,186 用例）：95% 把握确认"不 flaky"平均需重跑 **170 遍**
- 默认 `RERUN_COUNT = 100`；**0 次翻转 ≠ 稳定**，报"稳定"必须附实际运行次数
- **修正 C**：`--count=N` 是同进程循环，`PYTHONHASHSEED` / 全局 random 状态不重置 ——
  只能当快速初筛，**不能作为"不 flaky"的证据**；判定必须换进程重跑

## 负样本口径（项目的核心创新点之一）

正样本：IDoFT 全部 Python 记录（1618 条 / 332 个去重仓库；343 是去重 URL 数，非项目数）。**负样本一条都没有，必须自己造**：

| 档 | 规则 | 可信度 |
|---|---|---|
| N1（默认） | 同项目、同 SHA 下 IDoFT 未记录的测试 | 有噪声 |
| N2 | N1 + CI 历史无翻转 | 较高 |
| N3 | N1 + 抽样跑 K≥30 遍复核 | 高（只对抽样做） |

**偏差方向论证（写进 README 的原话）**：IDoFT 是"被发现过的 flaky"的并集。检测器抓到
真 flaky 而 IDoFT 漏记时只能落进假阳性列 → **精确率被系统性低估**；重跑不足导致漏检 →
召回率同样低估。**两个指标都偏保守，报出的是下界。**

## 样本划分与防泄漏

- **必须报跨项目（cross-project）口径**（参照 Flakify：within 98% vs cross 89%，差 16 个点）
- few-shot 示例按项目分组划分（GroupKFold 思路）；随机种子写死并记入实验记录
- 防泄漏检查自动化：`tests/test_no_leakage.py` 挂 CI

## 目标项目复现

- 按 `SHA Detected` 检出；git 不通时用 codeload 拉指定 SHA 的 tarball（解压目录名即校验）
- **修正 A**：`SHA Detected` 可能已含修复（penman 实例）—— 先判 SHA 是修复前还是修复后
- **修正 B**：复现用项目自己 CI 的命令行（从 `.github/workflows/*.yml` 读出），不能只用 `pytest <target>`
- **默认 Docker**，两条理由：① 老项目是 Python 3.7–3.9 时代的东西；② 不少 flaky 机制只在
  Linux 上成立（mdutils 实例：Windows 上 PermissionError 掩盖翻转机制）。**Windows 上
  "没有翻转"不能作为"不 flaky"的证据**
- 镜像按 commit 年份选（2018–2019 → `python:3.7-slim`；2020–2021 → `3.9-slim`；2022+ → `3.10-slim`）
- `--network none` 刻意断网：自动过滤"依赖外部服务"的脏数据
- 依赖攻坚 SOP：`-e .` → requirements* → 降 Python 版本 → 放弃。**单项目上限 2 小时**，
  可复现成功率约 20%，恋战是最贵的错误
- 项目状态标记只许五值：`OK / DEPS_FAIL / TEST_NOT_FOUND / SHA_NOT_FOUND / TIMEOUT`
