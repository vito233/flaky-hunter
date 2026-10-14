# 2026-10-14 · 全链路复现验证：陌生人照 README 30 分钟跑通

## 四行底线

- **做了什么**：全新 `--depth 1` 克隆两份（Windows 侧 `/mnt/c` + WSL 家目录），
  严格只按 README「快速开始」逐步执行，不参考本地任何已有环境；逐步计时，
  记录全部卡点，修 README 后复验。
- **得到什么数字**：不碰 Docker 的六步（uv sync / pytest 254 全绿 / ruff /
  build_labels 19 条 / 历史翻转演示 118 条 = 0 flaky / 看板探活 200）全部
  一次通过，合计 **< 20s**（本机有依赖缓存；真陌生人首次 +3~5 分钟下载）。
  Docker 评测试点在 **WSL 内全链路通**：yamicache ×10 遍 **TP=2 FP=0 FN=0
  TN=2，精确率 100%（基线 50%）· 召回 100% · F1 1.000（基线 0.667）**，
  与 2026-10-12 C1 试点口径一致；耗时 **494s**（含逐容器装工具链 + 卷种子化）。
- **坑位 4 个，全部修进 README**（见下）。
- **卡在哪**：无。order 评测未单独重跑（与 replay 同一 runner 机制、同一
  Docker 路径，C1 已验证）；负样本复核定位为「进阶」而非快速开始（需外部备料）。

## 逐步计时（陌生人视角）

| 步骤 | 平台 | 耗时 | 结果 |
|---|---|---|---|
| git clone --depth 1 | Windows | 3.8s | ✅ |
| uv sync | Windows | 2.2s | ✅（首次另需下载依赖与 Python 3.13） |
| uv run pytest -q | Windows | 11.5s | ✅ 254 全绿 |
| ruff check + format | Windows | ~2s | ✅ 0 违规 / 69 文件无需改 |
| build_labels.py | Windows | 1.4s | ✅ labels.csv 19 条 |
| 历史翻转演示（自带 30 份 XML） | Windows | 1.4s | ✅ 118 条 = 0 flaky / 118 待定（30<60 不冒充 stable，与存档一致） |
| **run_eval 试点 yamicache ×10** | **Windows** | 3.6s | ❌ **找不到 docker**（Docker 在 WSL）→ 全数 DEPS_FAIL、指标归零 |
| run_eval 试点 yamicache ×10 | **WSL** | **494s** | ✅ TP=2 FP=0 FN=0 TN=2（P 100% / R 100% / F1 1.000） |
| uv sync --extra dashboard | Windows | 3.2s | ✅ |
| streamlit 启动探活 | Windows | ~12s | ✅ /_stcore/health = 200 |
| WSL：装 uv + 克隆 + uv sync | WSL | ~21s | ✅（uv 0.13.1 / 克隆+sync 5.7s） |

**陌生人首次全链路预算：约 10–12 分钟**（其中 8 分钟是评测本身），30 分钟
红线富余。

## 坑位与修复（README 已改）

1. **无前置条件节**：README 直接从 `uv sync` 开始，没说 uv / Docker 怎么来、
   更没说评测脚本要求「能直接调 docker」。→ 新增「前置条件」节，含 Windows
   最短路径三行命令（WSL 内装 uv → 克隆到 WSL 家目录 → 跑试点）。
2. **Windows 侧跑评测全军覆没**：Docker 已迁 WSL，Windows 侧 `docker` 不存在，
   脚本不崩但全数 DEPS_FAIL、指标 0.0%（好在如实报告、排除项写清楚）。且
   runner 的挂载参数是 Windows 风格路径，即使打通 docker 调用也挂载不进
   WSL 容器——**正解是评测命令整个进 WSL 跑，不是给 Windows 加兼容层**。
   → 前置条件节写明该边界与实测证据。
3. **历史翻转没给可跑的示例**：`--xml-dir <历史目录>` 是占位符，陌生人不知
   道仓库自带 `experiments/raw/history-mdutils/`（30 份真实 Actions 产物）。
   → 快速开始直接给具体命令（含 `--project didix21/mdutils`），并把
   `python -m` 统一成 `uv run python -m`。
4. **负样本复核命令照抄必报错**：`--src-root/--deps-root` 是必填参数，
   README 示例没带。→ 移出「快速开始」、单列「进阶」，写明必填与 WSL 视角
   路径要求，指向模块注释。

## 边界（如实报）

- 试点评测的容器内工具链安装走逐容器 pip（每 run 都装），8 分钟里大头是它；
  卷种子化后源码 I/O 不再是瓶颈（2026-10-12 实测 27s vs 9p 62s）。给陌生人
  的预期就该写「约 8 分钟」，README 已写。
- `run_order_eval.py` 未在本轮单独重跑：与 replay 共用 DockerRunner 与挂载
  机制，C1（10-13）已出过正式数字；WSL 迁移后它同样只能在 WSL 跑，README
  已随「需要 Docker」组说明。
- 两份克隆均为 `--depth 1`（省时间）；未验证完整历史克隆（对复现无影响）。

## 复验（本记录按项目纪律二次核对）

- 试点数字与 `experiments/results.csv`（本次生成于 WSL 克隆内）逐行对上：
  正样本 2 条全检出 —— `tests/test_noclass.py::test_main`（OD-Vic）、
  `tests/test_serialization.py::test_serialization`（NIO），双探头证据齐
  （换进程 10 遍全过 + 同进程 count ×100 失败率 99%，后者是唯一现形探头）；
  负样本 `test_class.py::test_counters` / `test_gc.py::test_gc2` 10 遍全过
  → inconclusive（不足 60 遍不冒充 stable），无一误报。
- 历史翻转输出与存档 `experiments/history_mdutils_master30.md` 数字一致
  （118 / 0 / 0 / 118）。
