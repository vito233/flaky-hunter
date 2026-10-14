# 2026-10-14 · 门禁 --strict 第二版：fail-closed + mode 字段 + fork 双档演示

## 四行底线

- **做了什么**：gate.py strict v2（四项改动，见下）+ 退出码矩阵单测 → flaky-hunter
  推送（`5b45615` 代码 + `3bbe1d2` 同日 N3 v2 数据）→ fork `vito233/mdutils` 开
  PR#3（`demo-bad-pattern-strict` 分支：与 PR#2 完全相同的注入代码，唯一差别 =
  workflow 的门禁命令加 `--strict`）→ Actions 真跑 → 下载判定工件核验。
- **得到什么数字**：同一注入代码双档对照 —— **PR#2（报告档）检查绿：alert 10 条，
  不阻断；PR#3（strict 档）检查红：alert 10 条，exit 1 拦截合并**。PR#3 扫描
  120 条 / 13 文件 / 扫描 30.8s，findings 与 PR#2 逐条一致（8 条 IDoFT 真值
  OD-Vic + 注入 victim + 1 条连带）。JSON 关键字段核验：verdict=alert ·
  mode=strict · schema 1.1。单测 243 → 254 全绿。
- **下一步**：全链路复现验证（README 终稿后，投递前保险）；Kappa（需人工标 30 条）。
- **卡在哪**：无。插曲：推送曾卡在本机代理客户端未运行（7890 端口无监听），
  启动 Clash Verge 后恢复；临时克隆仓库需单独配隐私 noreply 提交身份。

## strict v2 改了什么（四项，全部向后兼容）

1. **fail-closed**：strict 下 `inconclusive` → exit 2 —— 扫描没完成就不放行，
   与门禁自己的原则（"不许瞎着给 pass"）对齐；报告模式行为不变（恒 exit 0）。
2. **GateReport +mode 字段**（report/strict），schema_version 1.0 → 1.1。
3. **文案随模式走**：gate_verdict 理由、Markdown 页眉页脚（报告模式 = 贴告示；
   硬拦截 = 阻断），"0 次翻转 ≠ 稳定"免责声明两种模式都在场。verdict 本身与模式无关。
4. **main() 退出码矩阵单测**：6 组合参数化（报告恒 0；strict: pass 0 / alert 1 /
   inconclusive 2），外加 mode 字段回环、strict 短路、文案分档共 11 条新测试。

## fork 双档演示（vito233/mdutils，parent didix21/mdutils）

| PR | 注入代码 | 门禁模式 | PR 检查 | verdict | findings | JSON mode |
|---|---|---|---|---|---|---|
| [#2](https://github.com/vito233/mdutils/pull/2) demo-bad-pattern | chdir 坏模式测试 | 报告（shadow） | 🟢 success | alert（10 条） | 10 | —（v1 schema） |
| [#3](https://github.com/vito233/mdutils/pull/3) demo-bad-pattern-strict | **同一段** | `--strict` | 🔴 failure | alert（10 条） | **逐条一致** | strict |

- PR#3 步骤核验：唯一失败步骤 = "Run flaky gate (strict mode)"；summary 与工件
  照常上传（`if: always()` 兜底起效）。
- 工件留档：`experiments/gate-preview/gate-strict-pr3.{md,json}`。
- fork 上并行还有两个 run：上游自己的 "Python application"（codecov token 缺失，
  红与本演示无关，PR#1 时代就红）与 "PR Labeler"。

## 面试可讲的点

1. "门禁上线是两档工程惯例：先 shadow 观察误报率，数字站住再硬拦 —— 我把两档
   都在真实 fork 上跑通了"
2. "同一份注入代码、同一套探头，唯一变量是 --strict：报告档绿（贴告示）、
   strict 档红（拦下）—— 控制变量法证明拦截行为正确"
3. "strict 不只拦『检出』，还拦『没看清』—— inconclusive 也 exit 2（fail-closed），
   门禁不许瞎着给 pass"
4. 踩坑日志：git 推送依赖本机代理（7890）→ 排查端口监听；临时克隆不带身份配置
   → 仓库级隐私 noreply 身份
