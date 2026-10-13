# FlakyTest-Hunter

**CI 质量门禁：Flaky 测试检测与失败归因评测平台** —— 基于 IDoFT 公开数据集
（Python 子集 1618 条 / 332 个去重仓库）。

> 状态：**C1 ✅ 完成（双模式检测器 + 合并口径指标）· 下一站 C2 · LLM 归因评测**
> （2026-10-13）。
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
```

## 局限（先读这个再下结论）

见 [`docs/limitations.md`](docs/limitations.md) —— 精确率是系统性低估的**下界**、
`Recall@IDoFT` 不是真实召回率、Category 真值本身有噪声。

## 给 AI agent

见 [`AGENTS.md`](AGENTS.md)（三条铁律、选型清单、"报数字"的硬格式）。
