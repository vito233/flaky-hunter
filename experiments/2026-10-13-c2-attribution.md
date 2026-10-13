# C2 · 全量归因评测（2026-10-13）

## 做了什么

- 输入材料组装：`experiments/build_attribution_inputs.py` → 19 条真值样本的
  失败现场（合并 order/count 两探头日志，**剥离 C1 判定头**——注释行含
  polluter 归因，直接给模型等于把答案写在考卷上；剥离后经泄漏检查干净）
  → `data/processed/attribution_inputs.json`
- 跑法：`experiments/run_attribution.py`，`glm-5.3-flash`、温度 0.0、
  PROMPT_VERSION=attribution-prompt-v0.1.0、19 条、解析失败率 0.0%
- 指标手写（`attribution/metrics.py` + 手算单测），未引 sklearn：19 条 ×6 类
  的一次性统计不值得几十 MB 重依赖（选型说明已入模块 docstring）

## 得到什么数字（两遍核对：CSV 独立重算 = metrics.json）

```
归因准确率 68.4%（13/19；本评测集口径：多数类基线 63.2%（12/19），
                  全量口径多数类基线 49.5%）
macro-F1 0.496
OD 二分类 78.9%（15/19；本评测集二分类基线 68.4%（13/19）—— +10.5pp）
混淆矩阵（行=真值 列=预测）：
  NIO   → NIO 6
  OD    → NIO 1
  OD-Vic → NIO 3 · OD 2 · OD-Vic 7
逐类：NIO P0.60/R1.00/F0.75 · OD P0/R0/F0（support=1）· OD-Vic P1.00/R0.58/F0.74
```

## 分析（面试素材）

**正面**：
- NIO 召回 100%（6/6）；模型答 OD-Vic 时精确率 100%（7/7）——不是瞎猜，
  是**保守**：证据不足时滑向 NIO
- OD 二分类 78.9% vs 基线 68.4%：+10.5pp，有真实信号

**负面（部分负结果，见 docs/negative-result.md）**：
- 六类细判 68.4% vs 多数类 63.2%：**只多对 1 条**——六类层面的收益微弱
- 6 条判错中 4 条（yamicache test_main / pythondi test_configure /
  python-fs test_mkdir / Pyro5 test_compat_config）是**输入材料的结构性
  混淆**：这些条目同时有 count 日志（"第 1 次过第 2 次起挂"是 NIO 的显性
  特征），合并输入后 NIO 证据压过了底层顺序依赖成因 → 模型滑向 NIO
- 2 条 OD-Vic → OD：OD 家族内部细分（Vic vs 未细分）错误，官方 IDoFT
  本身对这两类边界也模糊
- OD 类 support=1：单样本类让 macro-F1 对单条极敏感（0.496 被它拉低），
  六类 macro-F1 在本评测集上**不具统计意义**，只报不评

## 改进方向（下一步候选）

1. 单探头输入对照实验：同一批样本分别只喂 order / 只喂 count 日志，
   量化"证据混源"造成的误判占比（预计 4/6 条会翻正）
2. 扩样本：19 条里 OD 家族细分本就不足；评测集扩到全量 IDoFT 可复现子集
   才能支撑六类结论
3. 温度/模型对照：glm-5.3-flash 温度 0 vs deepseek-v4-flash，看保守倾向
   是否模型特有

## 卡在哪

无卡点。负结果已按规范落 `docs/negative-result.md`，不藏。
