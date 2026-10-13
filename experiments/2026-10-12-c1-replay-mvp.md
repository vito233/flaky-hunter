# 2026-10-12 · C1 replay 模式 MVP 检测流水线

> 四行底线格式（项目规范 §2.8）。**数字以 experiments/results.csv / metrics.json 为准**，
> 本文只做归因解读。

## 做了什么

- `runner.py`：两段式 Docker 封装（setup 带网装依赖 / run `--network none` 断网），
  源码与依赖走 Docker 卷（9p 挂载 I/O 实测慢 2.3 倍，卷内跑）
- **双探头检测器**（`detect_replay.py`）：换进程重跑 N 遍（NOD / stable 判定正典）
  \+ 同进程 `--count=N`（进程内状态污染型 NIO 的阳性证据，JUnit XML 精确计数）
- `negatives.py`：N1 负样本（同项目同 SHA、IDoFT 未记录），定种子抽样
- `metrics.py`：precision（下界）/ Recall@IDoFT / F1 + always-flaky 基线
- `experiments/run_eval.py`：评测驱动，19 条正样本（7 项目）+ N1 负样本 ×2/项目，runs=100
- 数据层两处纠正：IDoFT 真实表头第三列带括号说明；"343 项目"实为去重 URL 数，
  去重仓库数是 **332**

## 得到什么数字

**第一版指标（19 条正样本 / 7 项目 + N1 负样本 14 条，换进程 ×100 + 同进程 --count ×100）**：

```
精确率 100.0%（下界；always-flaky 基线 57.6%）
Recall@IDoFT 57.9%（always-flaky 基线 100.0%）
F1 0.733（always-flaky 基线 0.731）
混淆矩阵 TP=11 FP=0 FN=8 TN=14
按 IDoFT 类别拆正样本召回：
  NIO      6/6  100.0%
  OD       1/1  100.0%
  OD-Vic   4/12 33.3%
```

> **⚠ 2026-10-13 深夜审计更正**（experiments/2026-10-13-groundtruth-audit.md）：
> 负样本剔除当时只对照 20 条真值表而非 py-data.csv 全库（bug 已修）——
> yamicache `test_deco_timeout` 被误标负样本，实为 IDoFT 记录的 NOD 型
> flaky（同 SHA）。修正后（正样本 20 / 负样本 13）：
>
> ```
> 精确率 100.0%（下界；always-flaky 基线 60.6%）
> Recall@IDoFT 55.0%（11/20；always-flaky 基线 100.0%）
> F1 0.710（always-flaky 基线 0.755）
> 混淆矩阵 TP=11 FP=0 FN=9 TN=13（新增 FN：test_deco_timeout，NOD）
> ```

三个值得在面试里讲的结论：

1. **11 条 TP 全部靠同进程 --count 探头抓到**（换进程 100 遍全是 100过/0挂）。
   yamicache 实例坐实了"进程内状态污染"机制：只上换进程探头，这轮召回率是 0/19。
   双探头设计不是洁癖，是数据逼出来的。
2. **8 条 FN 全是 mdutils 的 OD-Vic**（顺序依赖）：目标测试单独跑 100 遍全过，
   翻转只在"整套跑"时出现 —— 固定顺序 replay 的系统性盲区，正是 detect_order.py
   （顺序变换 + 二分最小化）要解决的，C1 后半段的主菜。
3. **F1 0.733 首次追平 always-flaky 基线（0.731）**：正样本占比 57.6% 时，
   "无脑全报"的 F1 很高；检测器的价值在精确率（100.0% vs 57.6%）——
   CI 门禁要的是不误堵合并，不是拦得多的。（审计更正后正样本占比 60.6%、
   基线 F1 0.755，replay 独立口径 F1 0.710 略低于基线 —— replay 单模式的
   召回短板更明显，合并口径 0.974 才是完整形态。）

诚实记录（不藏）：修复 count 探头前的第一轮曾出现 1 条 FP —— yamicache 的
`test_deco_timeout`（负样本，cross 98过/2挂，2% 时序抖动）；修复后重跑未复现
（count 探头 100 遍也全过）。**100% 精确率是小样本上的测量值，不是承诺**；
这正是 limitations 第 1 条"精确率是下界"的现实版 —— 抖动测试是否算 flaky，
本身就是阈值问题。

> **⚠ 审计后的新解读**：test_deco_timeout 就是 IDoFT 记录的 NOD 型 flaky
> （同 SHA）——第一轮的 98过/2挂**很可能不是误报，而是真阳性**（NOD 靠
> NonDex 类工具检出，触发依赖特定非确定性条件，复现率天然低）。当时把它
> 当"抖动误报"处理，复盘看是复现率问题撞上了真值口径 bug。两个教训：
> ① 抖动 + IDoFT 已记录 = 应先查全库再定性；② 负样本池必须全库对照。

**数据完整性**（结论落纸前自检，2026-10-12）：
- 混淆矩阵从 results.csv 独立重算，与 metrics.json 逐项一致
- count 探头 shortfall 10 行（全是 mdutils 的 unittest 风格测试），全部正确打标；
  无产出行 0（修复前是 3 行静默失败）
- 每行 verdict / status / 两个探头的原始计数都在 results.csv 里可查

## 下一步

- `detect_order.py`：顺序变换 + 二分最小化（iDFlakies 流程）—— OD 家族是 replay
  的系统性盲区，这是 C1 后半段的主菜
- 负样本 N2/N3 口径（CI 历史复核 / 抽样 K≥30 遍）；跨项目口径报告

## 卡在哪 / 踩坑（全是面试素材）

1. `setuptools<81` 的 `<` 被 bash 当输入重定向（`bash: 81: No such file or directory`）
   —— shell 里包名必须引号。试点实测抓到，测试钉住
2. 镜像直连 Docker Hub 官方源被代理破坏（`commit failed: size validation 7975 != 7636`）
   —— 必须走 daocloud mirror；和 pip 必须走清华源同一个病
3. **yamicache 的 NIO 是进程内状态污染**：换进程 100 遍全过，同进程 1过/99挂
   （失败原因：`len(Cache object)` —— 同进程第二次调用时对象还活着）。
   只上换进程探头会系统性漏掉这类 → 双探头设计的事实依据
4. JUnit XML 前导换行导致 `XML declaration not at start of entity` —— strip 后解析
5. 测试卫生：`Path("/c/deps")` 在 Windows 上是 `C:\c\deps`，marker 落真实文件系统
   污染后续轮次 —— 测试一律 tmp_path
6. **`pytest --count` 对 unittest.TestCase 不重复**（parametrize 不适用于 unittest
   用例，mdutils 28 条全是这种风格，只跑 1 次）—— 修：shortfall 打标 + 备注写明；
   另发现慢测试（3s/轮 ×100）会被 120s 熔断无声杀掉（XML 没写出来表现成"无产出"）
   —— 修：先量体温再按实测给预算。两个漏洞都只造成漏报/失真，不造成误报，
   但"请求 100 次只跑 1 次却不报警"是数据诚信问题，发现后全量重跑（13 分钟）
