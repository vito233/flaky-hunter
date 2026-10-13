# 实验记录 · 模式 B 历史翻转（detect_history）+ N2 口径落档

- 日期：2026-10-13（深夜）
- 产出：`src/flaky_hunter/detect_history.py` · `schema.HistoryResult/HistoryReport` ·
  `experiments/build_n2_from_recheck.py` · `experiments/negatives_n2.{json,csv}` ·
  单测 220 → 238 全绿
- 补账：TODOLIST 第 3 周"模式 B"+ N2 负样本口径

---

## 一、设计

**输入**：一个目录的 JUnit XML（每文件 = 一次 CI run），文件名典序 = 时间序
（调用方负责可排序命名，如 `0001.xml`）。不读 XML 内 timestamp 排序——
时区/精度坑多，文件名可控。

**判定规则**（纯函数 `classify_history`，与 replay 的 `classify_counts` 同构）：

| 观察 | 判定 | 置信 |
|---|---|---|
| 相邻翻转 ≥1（P/F 相邻不同） | flaky | 有效 ≥20 → medium，否则 low |
| 全过 ≥60 | stable | **medium 封顶**（历史 run 可能跨 commit/环境；同 SHA 校验后方可升 high） |
| 全过 <60 | inconclusive | 0 次翻转 ≠ 稳定（规范 §2.2） |
| 全挂 | inconclusive | 全挂 ≠ 翻转（三分类口径：env_fail 排除并单独报数） |
| 无有效观察（全 skip/缺失） | inconclusive | 没有证据就没有结论 |

**翻转的两个口径都给**：`flip_transitions`（相邻切换，历史主口径；
FFFPPP=1）与 `flip_min_count`（少数派计数，与 replay 的 flip_count 同口径，
跨模式可比；FFFPPP=2）。

**关键设计取舍**：
1. `HistoryResult` 独立于 `DetectionResult` —— 历史模式没有 Docker env、
   没有检出 SHA（铁律 2 不适用：不 checkout，只分析既有记录），硬套统一
   schema 会撒谎。`Mode` 的 `"history"` 字面量在 schema 1.0 就预留了。
2. `classname` 为空时 `test_id` 直接用 `name` —— 自攒历史可沿用 replay 的
   路径形式（`tests/test_x.py::test_y`），两种 test_id 口径不混用。
3. 坏 XML 直接抛错 —— 静默跳过会伪造"无翻转"证据。
4. `<error>` 按失败计 —— 对"这次没过"的历史语义，failure/error 是一回事。

---

## 二、N2 口径落档（真实运行数据走完整管道）

**命令**：`uv run python experiments/build_n2_from_recheck.py`

**数据**：negatives_recheck.csv（2026-10-13 实跑的 30 遍换进程记录，56 条）

**结果**：56 条 → env_fail 排除 1（Pyro5 testStartNSfunc，断网预期失败）→
**N2 = 55 条 / 7 项目**（口径：历史 ≥10 遍无翻转且全过）。

**对账**（管道内置，不一致即抛错）：① 管道测试数 55 = 候选数 55；
② 每条 P/F 计数与 CSV 一致；③ 55 = recheck 汇总的 stable_ok 55 ✅

**诚实声明（随数字走）**：这里的"历史"= 本项目自己跑出的 30 遍记录
（同 SHA 同容器），**不是目标项目的公开 CI 历史**——TODOLIST 第 3 周
DoD 明示"或从 fork 里自己跑出的历史攒"属合规路线。公开 CI 历史路线
（fork 攒 Actions 产物）见"下一步"。

---

## 三、对照开源实现（DoD 3：翻转计数逻辑核对）

对照两个主流实现，翻转计数核心逻辑一致，判定策略有意不同：

**1. flaky-detector（golikovichev/flaky-detector-agent，Tessl 注册）**
- 同：翻转 = pass/fail 的**相邻切换**；忽略 always-failing。
- 异：它要求 14 天滑动窗口内 ≥3 次翻转才报（`--min-flips 3`）——
  用提高门槛压误报；我们 min-flips=1 + 置信分档（次数 ≥20 → medium）+
  下游三分类与人工复核兜底 —— **更保守不漏报**，符合本项目"检测是
  下界、评测看精确率"的定位。
- 异：它带 quarantine PR + LLM 修复建议；我们**不做修复**（项目定位 =
  检测 + 归因评测，AGENTS.md"不做什么"）。
- 它的文档明确说："F F F P P P 失败率 50% 但不是 flaky（坏了后修好），
  P F P F P 才是 flaky 签名" —— 与我们"全挂 ≠ 翻转"的三分类口径独立一致，
  交叉印证。

**2. WithSecure flaky-tests-detection（基于 Apple 论文 Kowalczyk et al.）**
- 它做 **fliprate 排名**（分窗计算 + EWMA + top-N + 热图），面向"哪个
  测试最 flaky"的运营视角；输入还支持 timestamp/test_identifier/status 的 CSV。
- 我们做**三值判定**（flaky/stable/inconclusive）+ 保守 inconclusive 语义
  （0 翻转 ≠ 稳定、全挂 ≠ 翻转都要说清楚），为 N2 负样本口径与门禁决策服务
  —— 需求不同：排名给 SRE 看，判定给质量门禁和评测集看。

**结论**：翻转计数逻辑（相邻切换）与两个实现一致，没有写错；判定策略
差异是定位差异（运营排名 vs 质量门禁判定），不是逻辑分歧。

---

## 四、A/B 成本收益对比（DoD 4）

| 模式 | 单项目耗时（实测） | 资源占用 | 需要真值 | 检出条数（C1 合并口径 19 条的分解） | 检出类别覆盖 |
|---|---|---|---|---|---|
| A · replay（换进程 + 同进程 count） | 100 遍/测试；56 条×30 遍 ≈ 1 小时 Docker 机器时间（recheck 实测 12.5s/条·30 遍） | Docker + 多进程，重 | 否 | 独抓 7 + 与 order 都抓 4 = 11 | 跨进程非确定性（NOD）+ 进程内 NIO；**抓不了 OD**（固定顺序下不翻） |
| 顺序变换（detect_order） | 每 victim 多顺序 + 二分，分钟级 Docker | Docker | 否 | 独抓 8 + 都抓 4 = 12 | OD 家族（victim/polluter/最小化） |
| **B · history（本模块）** | **解析 30 文件 × 55 测试 0.04 秒（实测）** | **零 Docker、零机器占用** | 否（但**前提是历史已存在**） | 无真值评测（正样本无现成历史）；N2 演示 55 条全过无翻转 | 跨 run 非确定性（NOD 面）；OD 与短历史低频翻转抓不到 |

**结论（DoD 5，适用场景，可口头复述）**：
模式 B 和模式 A 不竞争、是接力——**有历史就先扫历史**（秒级、零资源，
翻面即 flaky，适合 CI 产物存量的仓库和门禁外的巡检）；**没历史才花机器
时间重跑**（模式 A/顺序变换制造观察）。模式 B 的三个边界要背下来：
① 抓不了顺序依赖（固定顺序不翻）；② 历史短就判不了（0 翻转 ≠ 稳定，
我们要求 ≥60 才给 stable）；③ 全挂不是翻转（可能是测试坏了或"坏了后
修好"）。工程组合拳：history 当零成本初筛层，replay/order 当深检层，
门禁三探头是快速初筛的实时版。

---

## 五、真实项目公开 CI 历史报告（DoD 2 完全体）

fork `vito233/mdutils` master 加 `history-collect.yml`（workflow_dispatch，
matrix 30 个并行 job，各跑一次 `pytest --junitxml -o junit_family=legacy
-p no:randomly`，环境配方照抄上游 main.yml：Python 3.10 + pytest）。

- Run 38398146304：30/30 job 全绿（1m03s），30 个 artifact 下载拍平
  （`experiments/raw/history-mdutils/run-0000..0029.xml`，1.2M 入库留证）
- **detect_history 结果：118 测试 = flaky 0 / stable 0 / inconclusive 118**
- 逐条对账通过：118 条 × 30 run 全 P、0 翻转、0 缺失、0 跳过
- **数字怎么读**：30 遍全过但 30 < 60（二项检验线）→ 按规则报
  inconclusive，不冒充 stable（0 次翻转 ≠ 稳定，规范 §2.2）；
  0 条 flaky 与"上游已修复已知 flaky 候选"的事实一致（预演淘汰记录）
- 意义：第一份**真实项目公开 CI 产物**走完模式 B 全链路
  （Actions 产出 → artifact → 检测器 → 报告）；报告
  `experiments/history_mdutils_master30.{json,md}`
- 意外收获（面试素材）：给 fork master 加的 history-collect 本身演示了
  "JUnit XML 是 CI 通用产物"——上游 main.yml 本来就产 junit.xml
  （传 Codecov），模式 B 只是把这类存量产物利用起来，无需改项目测试代码

## 六、下一步

1. 报告产物接看板第四视图（如有必要）。
2. IDoFT 提 PR（P1）—— 检测/归因/门禁/历史四条线的素材已齐。

