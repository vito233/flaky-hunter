# AGENTS.md

> 给在本仓库工作的 AI agent 的引导。**人读的详细版在 `docs/` 下，这里只做索引和硬约束。**

---

## 这是什么项目

**CI 质量门禁：Flaky 测试检测与失败归因评测平台**

在 IDoFT 公开数据集（Python 子集 1618 条 / 332 个去重仓库）上做 flaky 测试检测的可复现性筛选，实现双模式检测器（重跑翻转率 / CI 历史翻转计数），并评测"给定失败日志 + 代码 diff，LLM 能否还原 flakiness 的真实类别"。

**定位是评测，不是方法创新。** 不许在代码注释、文档、commit message 里出现"提出了新方法""首次"这类措辞。

---

## 三条铁律

违反任何一条，整个项目的数字就废了。这三条**没有工具兜底**，靠你自觉。

| # | 铁律 |
|---|---|
| 1 | `data/raw/` **只读**。加工产物一律写 `data/processed/` |
| 2 | 复现目标项目时 **`git checkout <SHA Detected>`**，不是 HEAD。检完必须 `git rev-parse HEAD` 校验 |
| 3 | **任何准确率旁边必须有基线**。裸百分比不许出现在代码、文档、结果文件、commit message 里 |

---

## 动手之前先读这三个

| 文件 | 里面有什么 |
|---|---|
| `docs/methodology.md` | 检测路径分流（OD 家族走顺序变换，不是重跑）、重跑次数与置信度、负样本口径与偏差论证 |
| `docs/limitations.md` | 必须主动交代的 5 条局限。**改评测代码前必读** |
| `docs/data-dictionary.md` | IDoFT 列定义、Category / Status 官方枚举、硬数字与基线 |

规划期的完整判断在仓库外的 `../寒假实习-项目规范.md`（§2 是技术判断，§4 是选型清单）。

---

## 选型：先找现成的，找不到再自己写

**加任何新模块之前，先在下面这张表里找一遍。找不到才自己写，并在 PR 描述里说明为什么找不到。**

| 要做的事 | 用这个，别自己写 |
|---|---|
| 依赖管理 / 锁定 / 运行 | `uv` |
| 代码风格 + 格式化 + import 排序 | `ruff`（`ruff check` + `ruff format`） |
| 提交前检查 | `pre-commit` |
| 结构化数据 / schema 校验 | `pydantic` |
| CLI | `typer` |
| 配置 + 环境变量 | `pydantic-settings` |
| HTTP 重试 / 退避 / 限流 | `tenacity` |
| 表格处理 | `pandas` |
| 进度条 | `tqdm` |
| 随机顺序跑测试 | `pytest-random-order` / `pytest-randomly` |
| 覆盖率 | `pytest-cov` |
| 分类指标 / 混淆矩阵 | `scikit-learn` |
| 分组切分（按项目） | `sklearn.model_selection.GroupKFold` 的思路 |
| 看板 / 图表 | `streamlit` + `plotly` |

**不引入**：MLflow、DVC、自研 LLM 调用框架、微调流水线。理由见规范 §4.3 —— 本项目的实验数量是个位数级别，上这些是过度工程。

---

## 常用命令

```bash
uv sync                            # 装依赖
uv run pytest                     # 跑测试（含基线断言）
uv run pytest -q tests/test_no_leakage.py
uv run ruff check --fix . && uv run ruff format .
uv run pre-commit run --all-files
```

---

## 报数字的格式（硬格式）

```
归因准确率 68.2%（多数类基线 49.5%，OD 二分类基线 72.7%）
Macro-F1 0.51（基线 0.17）
```

- 召回率一律写成 **`Recall@IDoFT`**（分母是 IDoFT 记录数，不是"世界上所有 flaky"）
- 精确率注明是**下界**（原因见 `docs/limitations.md` 第 1 条）
- 报"稳定"结论时**必须附带实际运行次数**（0 次翻转 ≠ 稳定）
- 每个数字都要能在 `experiments/results.csv` 里查到

---

## 代码约定（只列工具管不了的）

| 约定 | 说明 |
|---|---|
| 检测结果 JSON | 必须符合 `docs/data-dictionary.md` 里的 schema，用 `pydantic` model 定义（`flaky_hunter.schema`），**不许手写 dict** |
| 状态标记 | 只能用 `OK` / `DEPS_FAIL` / `TEST_NOT_FOUND` / `SHA_NOT_FOUND` / `TIMEOUT` 五个值，不许自创 |
| prompt 模板 | 带 `VERSION` 常量；实验结果必须记版本号 |
| 目标项目运行环境 | 一律 Docker，默认 `--network none`。**不要用本地 venv 去装老项目** |
| 目标项目 | 只 `clone`，**不改、不 push**。要改就在 fork 上改 |

---

## 不做什么

- 不做微调 / 训练分类器
- 不做多 Agent 编排
- 不写爬虫
- 不引入前端框架
- 不宣称方法创新
- 不用人肉 checklist 代替 CI —— **能写成一行配置的，就别写进文档**
