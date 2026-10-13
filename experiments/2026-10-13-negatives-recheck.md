# 2026-10-13 · N3 负样本复核实验（原第 2 周 DoD 补齐）

## 四行底线

- **做了什么**：7 个评测项目 × 每项目抽 8 条 N1 负样本（seed=42 可复现）× 换进程重跑 30 遍（DoD 底线 K≥30），三分类定性后写进 limitations 第 14 条。途中修复 2 个脚本缺陷并重跑。
- **得到什么数字**：**56 条有效样本（≥50 达标）· 全过 55 · 环境全挂 1（排除并单独报数）· 噪声翻转 0 → N1 纯度下界 100%（55/55）、噪声率上界 0%**。口径声明：对失败率 ≥5% 的测试 30 遍抓到概率 ≈78.5%，弱翻转抓不全 —— 0% 是上界估计不是绝对证明。
- **下一步**：可从 freezegun（候选 111 条）扩抽到 100+ 条压低上界方差；detect_history.py（模式 B）补 N2 口径（CI 历史复核）。
- **卡在哪**：无。

## 途中修的 2 个缺陷（都有诊断证据）

1. **python-fs 整项目 COLLECT_ERROR**：根因 = python-fs 的测试在 `fs/tests/` 不在 `tests/`（真值表测试名 `fs/tests/test_mkdir.py::...` 可证），统一 testdir 对它不适用。修复 = 项目级 testdir 表（`PROJECT_TESTDIRS = {"chaosmail/python-fs": "fs"}`）—— 显式配置而非自动退到 `.`，避免把 `build/` 里的旧测试副本收进负样本池。诊断过程：手动复现容器挂载发现 src 卷完整、报 `file or directory not found: tests` → 锁定 testdir。
2. **统计口径 v1 的误读风险**：v1 把"0/30 全挂"记为翻转（噪声率 2.08%）—— 但 0 过 30 挂不是 flaky（flaky = 有过有挂），复跑拿到失败日志实锤 = `socket.gaierror` DNS 解析失败（断网容器下起 NameServer 的预期失败）。修复 = 三分类（stable_ok / env_fail / noisy），env_fail 排除并单独报数（与 C1 指标口径同规则）。顺带把失败日志落盘 `experiments/raw/negatives-recheck/`。

另：第一次全量 15 分钟跑完 48 条（python-fs 8 条缺失 + Pyro5 1 条被误计噪声），修完重跑 14 分钟出 56 条干净数据 —— 机器时间成本可接受，没搞手工合并（保证 CSV 血统干净）。

## 结果分项目

| 项目 | 样本 | 全过 | 环境挂 | 噪声 |
|---|---|---|---|---|
| chaosmail/python-fs | 8 | 8 | 0 | 0 |
| didix21/mdutils | 8 | 8 | 0 | 0 |
| irmen/Pyro5 | 8 | 7 | 1 | 0 |
| juntossomosmais/regex4ocr | 8 | 8 | 0 | 0 |
| mtik00/yamicache | 8 | 8 | 0 | 0 |
| spulec/freezegun | 8 | 8 | 0 | 0 |
| teamhide/pythondi | 8 | 8 | 0 | 0 |

## 对主指标的意义

C1 合并口径精确率 100.0%（下界）的"下界"二字现在有了定量支撑：N1 负样本池 56 条抽样复核 0 噪声 —— FP=0 的可信度增强（虽非证明，见口径声明）。这个数字已写进 `docs/limitations.md` 第 14 条。

## 面试可讲的点

- "负样本是我们自己造的，所以第一件事是测量它有多脏" —— N3 复核的设计与三分类口径
- "0/30 全挂和'有过有挂'是两回事：前者是环境失败，后者才是 flaky 信号" —— 用一条 Pyro5 的 gaierror 实例讲清判定规则的边界
