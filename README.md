# FlakyTest-Hunter

**CI 质量门禁：Flaky 测试检测与失败归因评测平台** —— 基于 IDoFT 公开数据集
（Python 子集 1618 条 / 343 项目）。

> 状态：**阶段 C1 · 仓库骨架**（2026-10-12）。检测器与指标尚未就位；
> 阶段 A 的复现成果（20 条 / 8 个项目，门槛 15 / 3）见规划文件夹 `phase-a/`。

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
```

## 局限（先读这个再下结论）

见 [`docs/limitations.md`](docs/limitations.md) —— 精确率是系统性低估的**下界**、
`Recall@IDoFT` 不是真实召回率、Category 真值本身有噪声。

## 给 AI agent

见 [`AGENTS.md`](AGENTS.md)（三条铁律、选型清单、"报数字"的硬格式）。
