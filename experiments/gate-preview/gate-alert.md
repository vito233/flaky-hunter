## ⚠️ Flaky 门禁判定：alert

- **理由**：检出 10 条 flaky 嫌疑（报告模式，不阻断合并）
- **扫描覆盖**：120 条测试 / 13 个文件，总耗时 20.2s（镜像 docker.io/library/python:3.10-slim）

| 探头 | 状态 | 耗时s | 说明 |
|---|---|---|---|
| setup | ✅ | 0.0 | 工具链就绪 |
| collect | ✅ | 1.5 | 120 条 |
| seq_default | ✅ | 1.4 | 10 挂 |
| seq_reverse | ✅ | 1.3 | 0 挂 |
| count:tests/test_fileutils/test_fileutils.py | ✅ | 1.3 | rc=0，0 挂 |
| count:tests/test_gate_demo.py | ✅ | 1.2 | rc=1，1 挂 |
| count:tests/test_mdutils.py | ✅ | 1.3 | rc=0，0 挂 |
| count:tests/test_tools/test_header.py | ✅ | 1.2 | rc=0，0 挂 |
| count:tests/test_tools/test_html.py | ✅ | 1.2 | rc=0，0 挂 |
| count:tests/test_tools/test_image.py | ✅ | 1.2 | rc=0，0 挂 |
| count:tests/test_tools/test_inline.py | ✅ | 1.2 | rc=0，0 挂 |
| count:tests/test_tools/test_mdcheckbox.py | ✅ | 1.3 | rc=0，0 挂 |
| count:tests/test_tools/test_mdlist.py | ✅ | 1.3 | rc=0，0 挂 |
| count:tests/test_tools/test_reference.py | ✅ | 1.2 | rc=0，0 挂 |
| count:tests/test_tools/test_table.py | ✅ | 1.2 | rc=0，0 挂 |
| count:tests/test_tools/test_table_of_contents.py | ✅ | 1.2 | rc=0，0 挂 |
| count:tests/test_tools/test_textutils.py | ✅ | 1.2 | rc=0，0 挂 |

### ⚠️ flaky 嫌疑（单独跑能过、套餐/重复跑挂）

| 测试 | 类型 | 证据 |
|---|---|---|
| `tests/test_gate_demo.py::test_victim_needs_cwd` | order_victim | 默认序挂（30s 级快速扫描） |
| `tests/test_mdutils.py::TestMdUtils::test_create_md_file` | order_victim | 默认序挂（30s 级快速扫描） |
| `tests/test_mdutils.py::TestMdUtils::test_new_checkbox_checked_list` | order_victim | 默认序挂（30s 级快速扫描） |
| `tests/test_mdutils.py::TestMdUtils::test_new_checkbox_list` | order_victim | 默认序挂（30s 级快速扫描） |
| `tests/test_mdutils.py::TestMdUtils::test_new_header` | order_victim | 默认序挂（30s 级快速扫描） |
| `tests/test_mdutils.py::TestMdUtils::test_new_list` | order_victim | 默认序挂（30s 级快速扫描） |
| `tests/test_mdutils.py::TestMdUtils::test_new_list_bold_items` | order_victim | 默认序挂（30s 级快速扫描） |
| `tests/test_mdutils.py::TestMdUtils::test_new_reference_image_markdown_data` | order_victim | 默认序挂（30s 级快速扫描） |
| `tests/test_mdutils.py::TestMdUtils::test_new_table_of_contents` | order_victim | 默认序挂（30s 级快速扫描） |
| `tests/test_mdutils.py::TestMdUtils::test_references_placed_in_markdown_file` | order_victim | 默认序挂（30s 级快速扫描） |

---
_快速门禁（报告模式）：三探头全绿 ≠ 证明没有 flaky（0 次翻转 ≠ 稳定）。全量复核走 flaky-hunter 的 100 遍重跑 + 顺序扫描。_
