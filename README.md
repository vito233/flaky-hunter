# FlakyTest-Hunter

**CI 质量门禁：Flaky 测试检测与失败归因评测平台** —— 基于 IDoFT 公开数据集
（Python 子集 1618 条 / 332 个去重仓库）。

> 状态：**阶段 C1 · replay 模式检测流水线 + 第一版指标**（2026-10-12）。
> 双探头检测器（换进程重跑 + 同进程 `--count`）、N1 负样本、评测驱动均已就位。
> 第一版指标（19 条正样本 / 7 项目 + 14 条负样本，runs=100）：
> **精确率 100.0%（下界；always-flaky 基线 57.6%）· Recall@IDoFT 57.9%（基线 100%）
> · F1 0.733（基线 0.731）** —— 8 条漏检全是 mdutils 的顺序依赖（OD-Vic），
> 固定顺序重跑的系统性盲区，即 `detect_order.py`（顺序变换）要解决的问题。
> 逐行数据 `experiments/results.csv`，解读 `experiments/2026-10-12-c1-replay-mvp.md`；
> 阶段 A 的复现成果（20 条 / 8 个项目，门槛 15 / 3）见 `experiments/ground_truth_phase_a.csv`。

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
```

## 局限（先读这个再下结论）

见 [`docs/limitations.md`](docs/limitations.md) —— 精确率是系统性低估的**下界**、
`Recall@IDoFT` 不是真实召回率、Category 真值本身有噪声。

## 给 AI agent

见 [`AGENTS.md`](AGENTS.md)（三条铁律、选型清单、"报数字"的硬格式）。
