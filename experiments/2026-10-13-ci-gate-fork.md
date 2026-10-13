# 2026-10-13 · CI 门禁挂真实 fork（第 5 周提前完成）

## 四行底线

- **做了什么**：gate.py 门禁入口（三探头 + 单独跑验证 + 报告模式）→ 本机双用例预演 → fork didix21/mdutils → workflow 挂 fork → 两个演示 PR 触发 Actions 跑通
- **得到什么数字**：CI 用例 1 = pass（118 条 / 12 文件 / 扫描 18.7s / run 总 32s）；用例 2 = alert 检出 10 条 order_victim（120 条 / 13 文件 / 扫描 25.3s / run 总 49s），其中 IDoFT 真值 8 条 OD-Vic 全部重新抓出（**对已知真值召回 100%**）+ 注入 victim + 1 条连带受害者
- **下一步**：负样本 N2/N3 复核实验（可并行）；`--strict` 硬拦截转正式（第二版）；detect_history.py（模式 B）补原第 3 周欠账
- **卡在哪**：无卡点。有一个**方法论级发现**：候选池全部 4 个有真值的项目，最新 master 上 flaky 均已被上游修复（见下"预演淘汰记录"）—— 门禁演示改为"正反双用例"剧本

## 为什么要"正反双用例"（今天的核心方法论决定）

原计划假设"fork 上跑门禁会拦到存量 flaky"。**预演证伪了这个假设**：

| 候选 | 活跃度 | 最新 master 实测 | 结论 |
|---|---|---|---|
| didix21/mdutils | ✅ 2025-10 / 236★ | 118 条全过；chdir 已被上游改写修复（grep 全仓库无 chdir） | flaky 已修 |
| mtik00/yamicache | ⚠️ 擦线（2023-10） | CI 命令 + `--count=20` 全过；**历史 SHA 同探头 38 挂**（方法有效） | flaky 已修 |
| juntossomosmais/regex4ocr | ❌ archived=true（2021 止） | — | 出局 |
| irmen/Pyro5 | ✅ 2026-06 / 388★ | 466 条全过 + `--count=10` 全过；**历史 SHA 同探头 9 挂/1 过复现成功**（方法有效） | flaky 已修 |

结论：IDoFT 记录的 flaky 上游会修，"存量报警"这条路不存在。改用门禁上线的工程惯例剧本：**放行干净 PR + 拦截已知坏模式**。坏模式 = mdutils 自己历史上真实出过的 chdir 污染（IDoFT 真值 8 条 OD-Vic 的元凶，`os.chdir` 进 TemporaryDirectory 不恢复），**不是凭空造假**，叙事闭环："上游 2025 年修掉这个 bug，我们的门禁现在能拦住同类 bug 回潮"。

## gate.py 设计（三探头 + 定性）

```
探头 ①  默认顺序整跑一遍          抓"单独过、整套挂"（OD-Vic）
探头 ②  反向顺序整跑一遍          抓顺序依赖（OD）
探头 ③  每个测试文件同进程 --count=20  抓进程内 NIO（yamicache 型：
        换进程 100 遍全过、同进程 1 过 99 挂 —— 只有这个探头看得见）
谁挂 → 单独跑一遍定性：单独过 = 嫌疑报警；也挂 = 坏测试（不冤枉）
```

- 报告模式（检出不让 CI 红）—— 门禁上线先 shadow 观察误报率再转硬拦截；`--strict` 留给第二版
- 覆盖范围声明写进每份报告：**0 次翻转 ≠ 证明稳定**（快速门禁，非 100 遍全量复核）
- 单测 204 → 220 全绿；判定/解析/渲染全是纯函数

## 实施中的坑（3 个，全部实测钉住）

1. **cmd.exe 批处理 shim 丢换行**：Windows 跑 gate、Docker 在 WSL，最初用 docker.cmd 转发 → 多行容器脚本被批处理层截断（`bash: unexpected EOF`）。单行脚本存活、多行必炸（T1/T2 实验）。
   **正解**：`docker_bin` 支持多段前缀，走 `wsl -d Ubuntu-22.04 -e docker` —— **`-e` 必须带**：wsl.exe 不带 `-e` 会把参数拼成命令行交默认 shell 二次解释，引号被改写。T3 验证多行 + 混引号原样穿透。
2. **MSYS 路径转换**：git bash 启动命令时把 `--deps /home/...` 转成 `C:/Program Files/Git/home/...`，挂载串变三段 → `invalid mode: /deps`。**正解**：`MSYS_NO_PATHCONV=1`。
3. **POSIX 风格路径在 Windows 宿主侧**：`Path("/home/...")` 的 mkdir/iterdir/write 会炸。**正解**：setup / gate 的目录操作全部 `contextlib.suppress(OSError)` 防御（只损失缓存，不损功能）。

## CI 验证（GitHub Actions，fork 上）

- workflow：`.github/workflows/flaky-gate.yml`（pull_request + workflow_dispatch），从 GitHub 直接 `pip install git+https://github.com/vito233/flaky-hunter.git`
- PR #1（clean-demo，README 改行）：**success**，判定 pass，118 条 / 12 文件 / 18.7s（run 总 32s）
- PR #2（demo-bad-pattern，注入 chdir 模式测试）：**success**（报告模式），判定 alert，10 条嫌疑（120 条 / 13 文件 / 25.3s，run 总 49s）
- findings 与本机预演**逐条一致**（含真值 8 条全中）
- 注：上游自己的 CI（"Python application"）在 fork 上红（codecov token 缺失）—— 与门禁无关，是上游 workflow 需要 secrets

## 与项目 CI 原命令的关系（TODOLIST A3 前置缺口）

- mdutils 的 CI 原命令已抄：Python 3.10 + `pytest --cov --junitxml=junit.xml -o junit_family=legacy`（只装 pytest + pytest-cov）
- 门禁扫描未带 `--cov`（gate 脚本统一 `--no-cov`）：预演已验证两种口径下套件表型一致（均 118 全过）；`--cov` 的追踪开销对测试行为的扰动未测，计入 limitations（原第 11 条的变体）

## 改动清单

- flaky-hunter：`src/flaky_hunter/gate.py`（新）、`runner.py`（pip_mirror 可配 / PIP_OFFICIAL / docker_prefix 多段前缀 / run_script 公开 / POSIX 防御）、`tests/test_gate.py`（16 条单测）、`experiments/gate-preview/`（预演产物）。commit `3d70b41`（gate 主体 + 预演）+ `7a7049b`（CLI resolve 修）
- fork vito233/mdutils：master 加 workflow（`c2f203e`）+ clean-demo + demo-bad-pattern 两分支 + PR #1 #2

## 面试可讲的点

1. "门禁上线的标准动作是正反用例验收"——干净 PR 放行 + 已知坏模式拦截，先 shadow 再 strict
2. "我证明了门禁对已知真值召回 100%"——注入历史元凶模式，IDoFT 真值 8 条全部重新抓出
3. "为什么不用存量 flaky 演示？我实测了 4 个候选，最新 master 全被上游修了"——预演淘汰记录就是证据
4. wsl -e / MSYS 转换 / cmd 批处理三个坑 = AI 编码踩坑日志素材
