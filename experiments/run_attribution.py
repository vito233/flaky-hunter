"""C2 全量归因评测：19 条真值样本 → WorkBuddyLLM → 三件套（带两套基线口径）。

用法：
  FLAKY_WB_ENDPOINT=... FLAKY_WB_PUBLISHABLE_KEY=... \
    uv run python experiments/run_attribution.py [--model glm-5.3-flash]

产出：
  experiments/results_attribution.csv   逐条：真值/预测/解析/依据
  experiments/metrics_attribution.json  三件套 + 两套基线 + 口径声明 + 运行元数据
  experiments/raw/attribution/*.json    逐条原始输出留痕（badcase 素材）

口径：解析失败计入分母按判错算（最严格，主口径）；混淆矩阵行=真值列=预测。
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from flaky_hunter.attribution.llm import parse_failure_rate, run_attribution
from flaky_hunter.attribution.metrics import (
    accuracy,
    confusion_matrix,
    macro_f1,
    per_class_f1,
)
from flaky_hunter.attribution.models import AttributionInput
from flaky_hunter.attribution.prompt import PROMPT_VERSION
from flaky_hunter.attribution.workbuddy_client import WorkBuddyLLM
from flaky_hunter.baselines import (
    EVAL_MAJORITY_CLASS_BASELINE,
    EVAL_OD_BINARY_BASELINE,
    MAJORITY_CLASS_BASELINE,
    OD_BINARY_BASELINE,
)

ROOT = Path(__file__).resolve().parents[1]
INPUTS = ROOT / "data" / "processed" / "attribution_inputs.json"
OUT_CSV = ROOT / "experiments" / "results_attribution.csv"
OUT_METRICS = ROOT / "experiments" / "metrics_attribution.json"
RAW_DIR = ROOT / "experiments" / "raw" / "attribution"

FIELDS = ["project", "test_name", "label_true", "label_pred", "parse_ok", "rationale"]


def main() -> None:
    parser = argparse.ArgumentParser(description="C2 全量归因评测")
    parser.add_argument("--model", default="glm-5.3-flash")
    parser.add_argument("--temperature", type=float, default=0.0)
    args = parser.parse_args()

    items = json.loads(INPUTS.read_text(encoding="utf-8"))
    samples = [
        AttributionInput(
            test_name=it["test_name"],
            failure_log=it["failure_log"],
            code_diff="",
            project=it["project"],
        )
        for it in items
    ]
    client = WorkBuddyLLM(model_id=args.model, temperature=args.temperature)
    logging.info(
        "归因评测：model=%s temperature=%s n=%d",
        args.model,
        args.temperature,
        len(samples),
    )
    records = run_attribution(samples, client)
    if parse_failure_rate(records) == 1.0 and records and all(r.error for r in records):
        raise SystemExit("全部解析失败 —— 先检查网络/凭据，不做无意义指标")

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    y_true: list[str] = []
    y_pred: list[str | None] = []
    csv_rows: list[dict[str, str]] = []
    for it, rec in zip(items, records, strict=True):
        pred = rec.output.label.value if rec.output else None
        y_true.append(it["label"])
        y_pred.append(pred)
        raw_name = (
            f"{it['project'].replace('/', '__')}__"
            f"{it['test_name'].replace('::', '__').replace('/', '_')}.json"
        )
        csv_rows.append(
            {
                "project": it["project"],
                "test_name": it["test_name"],
                "label_true": it["label"],
                "label_pred": pred or "PARSE_FAIL",
                "parse_ok": str(rec.output is not None),
                "rationale": rec.output.rationale
                if rec.output
                else (rec.error or "")[:120],
            }
        )
        raw_name = (
            f"{it['project'].replace('/', '__')}__"
            f"{it['test_name'].replace('::', '__').replace('/', '_')}.json"
        )
        raw_path = RAW_DIR / raw_name
        raw_path.write_text(
            json.dumps(
                {
                    "test_name": it["test_name"],
                    "label_true": it["label"],
                    "raw": rec.raw,
                    "output": rec.output.model_dump() if rec.output else None,
                    "error": rec.error,
                },
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )

    with OUT_CSV.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(csv_rows)

    od_family_true = [
        "OD" if t in {"OD-Vic", "OD", "OD-Brit"} else "NON_OD" for t in y_true
    ]
    od_family_pred = [
        ("OD" if p in {"OD-Vic", "OD", "OD-Brit"} else "NON_OD") if p else None
        for p in y_pred
    ]
    metrics = {
        "schema_note": (
            "解析失败计入分母按判错算（最严格主口径）；混淆矩阵行=真值列=预测"
        ),
        "model_id": client.model_id,
        "temperature": args.temperature,
        "prompt_version": PROMPT_VERSION,
        "n_samples": len(samples),
        "parse_failure_rate": parse_failure_rate(records),
        "accuracy": accuracy(y_true, y_pred),
        "macro_f1": macro_f1(y_true, y_pred),
        "per_class": per_class_f1(y_true, y_pred),
        "confusion_matrix": confusion_matrix(y_true, y_pred),
        "od_binary_accuracy": accuracy(od_family_true, od_family_pred),
        "baselines": {
            "note": "两套口径不得混用（规范 §3.2）",
            "full_corpus_majority": MAJORITY_CLASS_BASELINE,
            "full_corpus_od_binary": OD_BINARY_BASELINE,
            "eval_majority": EVAL_MAJORITY_CLASS_BASELINE,
            "eval_od_binary": EVAL_OD_BINARY_BASELINE,
        },
        "timestamp_utc": datetime.now(UTC).isoformat(),
    }
    OUT_METRICS.write_text(
        json.dumps(metrics, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    logging.info(
        "归因准确率 %.1f%%（本评测集口径：多数类基线 %.1f%%，OD 二分类基线 %.1f%%）",
        metrics["accuracy"] * 100,
        EVAL_MAJORITY_CLASS_BASELINE * 100,
        EVAL_OD_BINARY_BASELINE * 100,
    )
    logging.info(
        "macro-F1 %.3f · 解析失败率 %.1f%%",
        metrics["macro_f1"],
        metrics["parse_failure_rate"] * 100,
    )
    logging.info("逐条 -> %s；三件套 -> %s", OUT_CSV, OUT_METRICS)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    main()
