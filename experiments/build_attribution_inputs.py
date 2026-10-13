"""组装归因评测输入：19 条真值样本 → 失败现场文本。

产出 data/processed/attribution_inputs.json。
来源：experiments/raw/ 的 C1 证据日志（__order.log = 顺序探头，
__count.log = 同进程探头）。
关键规则：**剥离 `#` 开头的注释行**（那是 C1 检测器自己的判定头，含 polluter
归因 —— 直接给模型等于把答案写在考卷上，评测失真）。只保留 pytest 原始失败输出。
两个探头日志都在时合并（对模型都是合法失败证据）。

用法：uv run python experiments/build_attribution_inputs.py
"""

from __future__ import annotations

import csv
import json
import logging
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LABELS = ROOT / "data" / "processed" / "labels.csv"
RAW = ROOT / "experiments" / "raw"
OUT = ROOT / "data" / "processed" / "attribution_inputs.json"


def log_prefix(project: str, test_name: str) -> str:
    """raw 日志命名：owner__repo__test_name（:: 与 / 都替换为 _）。"""
    owner, repo = project.split("/")
    return f"{owner}__{repo}__{test_name.replace('::', '_').replace('/', '_')}"


def strip_comments(text: str) -> str:
    """剥掉 C1 判定头（# 开头行）与空行，只留 pytest 原始失败输出。"""
    kept = [ln for ln in text.splitlines() if not ln.startswith("#")]
    return "\n".join(kept).strip()


def load_failure_log(project: str, test_name: str) -> tuple[str, list[str]]:
    """合并两个探头的失败现场。找不到日志抛 LookupError（缺材料硬失败）。

    宽松匹配：部分 IDoFT 记录的 test_name 缺 `tests/` 前缀（如 Pyro5），
    glob 锚定 `__mode.log` 结尾，不会误吸同名前缀的其他测试
    （test_mkdir ≠ test_mkdir_recursive，因为锚点是 `__`）。
    """
    owner, repo = project.split("/")
    stem = test_name.replace("::", "_").replace("/", "_")
    sections: list[str] = []
    files: list[str] = []
    for mode, title in (
        ("order", "顺序探头失败现场"),
        ("count", "同进程 --count 探头失败现场"),
    ):
        matches = sorted(RAW.glob(f"{owner}__{repo}__*{stem}__{mode}.log"))
        if not matches:
            continue
        path = matches[0]
        body = strip_comments(path.read_text(encoding="utf-8"))
        if body:
            sections.append(f"[{title}]\n{body}")
            files.append(path.name)
    if not sections:
        msg = f"找不到证据日志：{owner}__{repo}__*{stem}__order.log / __count.log"
        raise LookupError(msg)
    return "\n\n".join(sections), files


def main() -> None:
    with LABELS.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    items: list[dict[str, str]] = []
    for row in rows:
        failure_log, files = load_failure_log(row["project"], row["test_name"])
        items.append(
            {
                "project": row["project"],
                "sha": row["sha"],
                "test_name": row["test_name"],
                "label": row["label"],
                "failure_log": failure_log,
                "log_files": files,
            }
        )
    OUT.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    logging.info(
        "attribution_inputs.json: %d 条（每条含合并失败现场）-> %s", len(items), OUT
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    main()
