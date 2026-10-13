# FlakyTest-Hunter

**CI 质量门禁：Flaky 测试检测与失败归因评测平台** —— 基于 IDoFT 公开数据集
（Python 子集 1618 条 / 332 个去重仓库）。

> 状态：**C2 · LLM 归因评测出炉 —— 部分负结果，如实报告**（2026-10-13）。
> 三件套（`glm-5.3-flash` 温度 0 · 19 条 · 解析失败率 0%）：
> **归因准确率 68.4%**（13/19；本评测集多数类基线 63.2% = 12/19，仅 +1 条）·
> OD 二分类 **78.9%**（15/19；二分类基线 68.4%，+10.5pp）·
> macro-F1 0.496（OD 类 support=1，不具统计意义）。
> NIO 召回 100% / 答 OD-Vic 精确率 100%。单探头对照实验确认 4 条误判主因
> 为 count 表型证据混源（只喂 order 输入后 4 条全部翻正；单组数字不可
> 脱离子集基线引用）。
> 详见 [`experiments/2026-10-13-c2-attribution.md`](experiments/2026-10-13-c2-attribution.md)
> 与 [`docs/negative-result.md`](docs/negative-result.md)。
>
> 状态 2：**CI 门禁已挂真实项目 fork**（2026-10-13）。`gate.py` 三探头门禁
> （默认序 + 反向序 + 文件级同进程 count，单独跑验证定性）以报告模式挂上
> [`vito233/mdutils`](https://github.com/vito233/mdutils)（parent
> didix21/mdutils），`pull_request` 触发。正反双用例验收：
> 干净 PR 判定 **pass**（118 条 / 12 文件 / 18.7s）；注入该项目 IDoFT 真值
> 8 条 OD-Vic 的历史元凶模式（chdir 进临时目录不恢复）后判定 **alert**，
> 检出 10 条 order_victim —— **对已知真值召回 100%**（真值 8 条全部重新抓出
> + 注入 victim + 1 条连带）。单次 run 总耗时 32s / 49s。
> 详见 [`experiments/2026-10-13-ci-gate-fork.md`](experiments/2026-10-13-ci-gate-fork.md)。
>
> 换进程重跑 + 同进程 `--count` 双探头抓 NIO/NOD；顺序变换（多顺序扫描 + 前缀
> 二分 + ddmin 最小 polluter 归因）抓 OD 家族。
> **合并口径（19 条正样本 / 7 项目 + 14 条负样本）：精确率 100.0%（下界；
> always-flaky 基线 57.6%）· Recall@IDoFT 100.0%（基线 100%）· F1 1.000
> （基线 0.731）** —— 顺序模式独立 OD-Vic 召回 12/12，另发现 11 条 IDoFT
> 未记录的 victim（无真值，只报数不进指标）。
> 逐行数据 `experiments/results.csv`（replay）/ `results_order.csv`（顺序），
> 解读 `experiments/2026-10-12-c1-replay-mvp.md` 与
> `experiments/2026-10-13-c1-order.md`；归因证据 `experiments/raw/*__order.log`。

## 问题（为什么值得做）

- Google 内部 **84%** 的"测试挂掉"报告是假警报
- OpenStack 为重跑时好时坏的测试烧掉 **187.4 个 CPU 年**
- 要有 95% 把握确认一个测试不 flaky，平均要重跑 **170 遍**

## 快速开始

```bash
uv sync                # 装依赖（含 dev 组）
uv run pytest          # 单元测试（含基线断言、"stable 需要证据"防线）
uv run ruff check .    # 静态检查
uv run ruff format .   # 格式化

# 跑评测（需要 Docker；19 条正样本 + N1 负样本，默认 runs=100）
uv run python experiments/run_eval.py --runs 100 --negatives 2
uv run python experiments/run_eval.py --projects mtik00/yamicache --runs 10  # 试点

# 顺序变换评测（OD 家族；产出 results_order.csv + metrics_combined.json）
uv run python experiments/run_order_eval.py --orders 4 --alone-runs 10
uv run python experiments/run_order_eval.py --projects didix21/mdutils  # 试点

# C2 地板：生成裁剪后标签表（data/processed/labels.csv，19 条 / 6 类）
uv run python experiments/build_labels.py

# 看板（可选依赖组，不污染核心依赖；3 个视图：检测 / 归因 / 真值表）
uv sync --extra dashboard
uv run streamlit run dashboard/app.py

# N3 负样本复核（7 项目 × 8 条 N1 负样本 × 换进程 K 遍，报告确认稳定比例）
uv run python experiments/run_negatives_recheck.py --runs 30
```

## 局限（先读这个再下结论）

见 [`docs/limitations.md`](docs/limitations.md) —— 精确率是系统性低估的**下界**、
`Recall@IDoFT` 不是真实召回率、Category 真值本身有噪声。

## 给 AI agent

见 [`AGENTS.md`](AGENTS.md)（三条铁律、选型清单、"报数字"的硬格式）。
