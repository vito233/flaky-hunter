## ✅ Flaky 门禁判定：pass

- **理由**：三探头全绿 —— 快速门禁未观察到翻转；0 次翻转 ≠ 证明稳定
- **扫描覆盖**：118 条测试 / 12 个文件，总耗时 18.6s（镜像 docker.io/library/python:3.10-slim）

| 探头 | 状态 | 耗时s | 说明 |
|---|---|---|---|
| setup | ✅ | 0.0 | 工具链就绪 |
| collect | ✅ | 1.4 | 118 条 |
| seq_default | ✅ | 1.3 | 0 挂 |
| seq_reverse | ✅ | 1.3 | 0 挂 |
| count:tests/test_fileutils/test_fileutils.py | ✅ | 1.3 | rc=0，0 挂 |
| count:tests/test_mdutils.py | ✅ | 1.2 | rc=0，0 挂 |
| count:tests/test_tools/test_header.py | ✅ | 1.2 | rc=0，0 挂 |
| count:tests/test_tools/test_html.py | ✅ | 1.2 | rc=0，0 挂 |
| count:tests/test_tools/test_image.py | ✅ | 1.2 | rc=0，0 挂 |
| count:tests/test_tools/test_inline.py | ✅ | 1.2 | rc=0，0 挂 |
| count:tests/test_tools/test_mdcheckbox.py | ✅ | 1.2 | rc=0，0 挂 |
| count:tests/test_tools/test_mdlist.py | ✅ | 1.2 | rc=0，0 挂 |
| count:tests/test_tools/test_reference.py | ✅ | 1.3 | rc=0，0 挂 |
| count:tests/test_tools/test_table.py | ✅ | 1.2 | rc=0，0 挂 |
| count:tests/test_tools/test_table_of_contents.py | ✅ | 1.2 | rc=0，0 挂 |
| count:tests/test_tools/test_textutils.py | ✅ | 1.2 | rc=0，0 挂 |

---
_快速门禁（报告模式）：三探头全绿 ≠ 证明没有 flaky（0 次翻转 ≠ 稳定）。全量复核走 flaky-hunter 的 100 遍重跑 + 顺序扫描。_
