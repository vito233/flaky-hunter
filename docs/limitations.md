# 必须主动交代的 5 条局限

> 改评测代码前必读。面试时**主动说**第 3 条，比等人问出来的效果完全不同。

1. **负样本是假定的。** IDoFT 未记录 ≠ 稳定。当检测器抓到真 flaky 而 IDoFT 漏记时，
   它只能落进假阳性列 → **精确率被系统性低估**；重跑不足导致漏检 → 召回率同样低估。
   **两个指标都偏保守，报出的是下界。**（这是常规做法：FlakyCat / FlakyXbert 也是自己补的 non-flaky 组）

2. **`Recall@IDoFT` 不是真实召回率** —— 分母是 IDoFT 记录数，不是"世界上所有 flaky"。

3. **`Category` 字段本身有标注噪声** —— 来自不同工具、不同年代、不同人标注，一致性未知。
   计划：报告分布 + 抽 30 条人工复核 + 估 Cohen's Kappa 上界。

4. **多标签被裁剪为单标签的损失** —— 评测取字母序第一个为主标签，完整标签集另存 `labels_raw`。

5. **约 20% 的可复现成功率带来选择偏差** —— 能跑起来的项目可能系统性地和跑不起来的不是一类东西。

## 阶段 A 补充（2026-10-12 实测）

6. **"清单记录 ≠ 到处可复现"** —— 同一记录换个环境可能是"稳定挂"或"怎么跑都过"。
   阶段 A 已积累 16+3 条证据（thundra / PyGraph / clashogram / isbnlib / wellpathpy）。
   这直接影响：以"复现成功"为前提构造的评测集，天然偏向"环境友好"的 flaky 机制。

## 阶段 C1 补充（2026-10-12 评测实测）

7. **同进程 `--count` 探头对 unittest.TestCase 家族无效** —— pytest-repeat 的重复
   靠 parametrize 实现，而 pytest 对 unittest 用例不走这条路（mdutils 28 条全是
   unittest 风格，实测 `--count=100` 只跑 1 次）。已在 runner 层打 shortfall 标
   （实际迭代数 < 请求数即可见），但这类家族的进程内 NIO 本轮覆盖不到。
   后续可用 `unittest` 自带的进程内 loop 补（`python -c` 里 N 次
   `TextTestRunner().run()`）。
8. **小样本 + 抖动测试 = 测量方差**：第一轮评测出现过 1 条 2% 时序抖动的负样本
   被判 flaky（98过/2挂），修复探头后重跑未复现。100% 精确率是测量值不是承诺，
   "抖动低于多少算稳定"本身是需要显式声明的阈值决策。

## 阶段 C1 后半段补充（2026-10-13 顺序模式实测）

9. **顺序模式的召回受"污染能否被某个顺序触发"限制**：只跑 4 个顺序
   （默认/反向/2 随机），polluter 恰好都在 victim 后面就漏。IDoFT 的
   OD-Vic 在本评测集里全部触发（12/12），但这是运气 + 集合小，
   不等于"4 个顺序够了"。iDFlakies 同样只给阳性证据。
10. **ddmin 是启发式，不保证全局最小 polluter 集合**：本轮全部收敛到
    1–2 条，但撞预算上限时只报"上界"（minimized=False，明说不假装）。
11. **套装顺序 = pytest 收集顺序，不完全是项目自己 CI 的顺序**（修正 B 的
    遗留）：复现用 pytest 收集序近似，项目若用自定义 runner（tox/nose），
    "默认顺序"与它的 CI 顺序可能有别。
12. **顺序扫描会被被测项目的 flaky 机制本身打到**：freezegun 的假时间泄漏进
    pytest 计时器，pytest INTERNALERROR 崩掉，整个顺序无结果（XML_EMPTY）。
    处理是跳过 + 留痕 + 不计入有效观测，但这类项目的顺序覆盖天然更低。
13. **评测集裁剪 = 选择偏差，且曾因对照范围 bug 混入已记录 flaky**：顺序扫描
    在评测集外抓到 11 条 victim —— 经 2026-10-13 全库对照审计，**10 条是
    IDoFT 已记录**（同 SHA 同类别 OD-Vic，阶段 A 抽样未进真值表），真·未
    记录只有 Pyro5 1 条。更早的 bug：N1 负样本剔除曾只对照真值表（20 条）
    而非 py-data.csv 全库（1618 条），导致 yamicache test_deco_timeout
    （IDoFT NOD，同 SHA）被当 TN 实为 FN（合并口径 R 100% → 95.0%）、
    6 条混进 N3 抽样 —— bug 已修（`flaky_hunter/idoft.py` 全库对照 +
    防漂移单测），详见 `experiments/2026-10-13-groundtruth-audit.md`。
    "精确率下界"论据保留：N1 口径下检测器抓到 IDoFT 漏记的真 flaky
    依然只能落假阳性列。
14. **负样本复核（N3）的置信度上限**：56 条抽样 × 换进程 30 遍
    （seed=42 可复现），全过 55 · 环境全挂 1（Pyro5 testStartNSfunc 需要
    DNS 解析，断网容器下预期失败，排除并单独报数）· 噪声翻转 0 ——
    **口径修正（2026-10-13 审计）**：56 条中 6 条为 IDoFT 已记录
    （NIO×2 / OD-Vic×4，同 SHA；负样本剔除 bug 混入），真 N1 = 50 条，
    **N1 纯度下界 100%（50/50）、噪声率上界 0%（对真 N1）**。
    两个必须读对的口径：① 30 遍全过 ≠ 证明稳定（95% 置信平均要 170 遍）；
    对失败率 ≥5% 的测试，30 遍至少观察到一次翻转的概率 ≈ 78.5% ——
    弱翻转抓不全，0% 是**上界的估计**；② 换进程口径对 NIO（进程内污染）
    与 OD 家族（victim 单独跑能过）**机制性盲区** —— 混入的 6 条 30 遍
    全过与其 IDoFT 记录不矛盾，恰是该盲区的实证。
    数据：`experiments/negatives_recheck.{csv,json}`（脚本
    `experiments/run_negatives_recheck.py`；分类修正在 json 的
    post_hoc_audit 块）。

## 阶段 C1 补账补充（2026-10-13 深夜 · 模式 B）

15. **N2 的"历史"是自攒的，不是目标项目公开 CI 历史**：N2 口径
    （N1 + 历史无翻转）用的历史 = negatives_recheck 的 30 遍换进程记录
    （同 SHA 同容器，本机 Docker 实跑）——TODOLIST 第 3 周 DoD 明示
    "或从 fork 里自己跑出的历史攒"属合规路线，但与"项目自己的 CI
    历史"有差别：自攒历史同 SHA 同环境，公开 CI 历史跨 commit 跨环境
    （我们的 stable 判定因此在历史口径下封顶 medium）。公开 CI 历史
    路线已补：fork 上 history-collect workflow 攒 mdutils master 30 个
    真实 CI 产物。    数据：`experiments/negatives_n2.{csv,json}`（脚本
    `experiments/build_n2_from_recheck.py`，N2 门槛 = 历史 ≥10 遍
    无翻转且全过）。
16. **模式 B 抓不到顺序依赖和"坏→修好"**：历史翻转检测的是跨 run
    非确定性；OD 家族在固定顺序下不翻（和 replay 同样的盲区），FFFPPP
    （坏了后被修好）失败率 50% 却不是 flaky。所以 N2 的"无翻转"证据
    不覆盖 OD 型 flaky —— 负样本里若藏着 OD 型，N2 口径抓不出来
    （replay 同理，这是双模式共同下界，见第 1 条）。

## 审计补充（2026-10-13 深夜 · 真值口径修正）

17. **NOD 家族是检测器的机制性盲区**：合并口径唯一 FN（yamicache
    test_deco_timeout）为 NOD 型 —— IDoFT 的 NOD 记录来自 NonDex 类
    工具（对非确定性 API 做语义变异后重跑），我们 replay（换进程原样
    重跑）与 order（顺序变换）都不做 API 语义变异，NOD 触发条件
    （特定哈希序/时序）在原样重跑下几乎不复现。第一轮评测它曾翻面
    一次（98过/2挂）当时被当"抖动误报"处理 —— 审计后重读为真阳性
    低复现。修复方向（C3 候选）：对 NIO/NOD 类候选加 NonDex 式变异
    探头，或在报告中声明 NOD 覆盖为 0 并说明工具边界。
