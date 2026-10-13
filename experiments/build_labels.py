"""从 ground_truth_phase_a.csv 生成裁剪后标签表 data/processed/labels.csv（规范 §3.2）。

用法：uv run python experiments/build_labels.py
规则：eval_eligible=true 的样本 → map_category 裁剪 → 缺标签排除并计数。
数字必须程序生成，不许手抄（阶段 A 教训：手工誊抄 40 行错了 15 行）。
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path

from flaky_hunter.attribution.labels import map_category

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "experiments" / "ground_truth_phase_a.csv"
DST = ROOT / "data" / "processed" / "labels.csv"

FIELDS = ["project", "sha", "test_name", "labels_raw", "label"]


def main() -> None:
    with SRC.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    out_rows: list[dict[str, str]] = []
    dropped = 0
    for row in rows:
        if row["eval_eligible"].strip().lower() != "true":
            continue
        label = map_category(row["category"])
        if label is None:
            dropped += 1
            continue
        out_rows.append(
            {
                "project": row["project"],
                "sha": row["sha"],
                "test_name": row["test_name"],
                "labels_raw": row["category"],
                "label": label.value,
            }
        )
    DST.parent.mkdir(parents=True, exist_ok=True)
    with DST.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(out_rows)
    logging.info(
        "labels.csv: %d 条（排除缺标签 %d 条）-> %s", len(out_rows), dropped, DST
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    main()
