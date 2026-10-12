"""metrics.py 的单元测试：混淆矩阵、基线、类别拆分、CSV 落盘、报告格式。"""

import json

from flaky_hunter.metrics import (
    Prediction,
    always_flaky_baseline,
    by_category,
    confusion,
    format_report,
    prf,
    status_breakdown,
    summarize,
    write_metrics_json,
    write_results_csv,
)


def _pred(**kw) -> Prediction:
    base = {
        "project": "a/b",
        "sha": "a" * 40,
        "test_name": "t::x",
        "category": "NIO",
        "y_true": 1,
        "y_pred": 1,
        "verdict": "flaky",
        "status": "OK",
        "runs_total": 100,
        "passed": 10,
        "failed": 90,
        "fail_rate": 0.9,
    }
    base.update(kw)
    return Prediction(**base)


def test_confusion_counts_only_included() -> None:
    preds = [
        _pred(),  # tp
        _pred(y_true=0, y_pred=1, category="N1"),  # fp
        _pred(y_true=1, y_pred=0),  # fn
        _pred(y_true=0, y_pred=0, category="N1"),  # tn
        _pred(excluded=True, exclude_reason="DEPS_FAIL"),  # 不计
    ]
    assert confusion(preds) == {"tp": 1, "fp": 1, "fn": 1, "tn": 1}


def test_prf_basic_and_zero_division() -> None:
    assert prf({"tp": 2, "fp": 1, "fn": 1, "tn": 5}) == (2 / 3, 2 / 3, 2 / 3)
    assert prf({"tp": 0, "fp": 0, "fn": 3, "tn": 4}) == (0.0, 0.0, 0.0)
    assert prf({"tp": 0, "fp": 0, "fn": 0, "tn": 4}) == (0.0, 0.0, 0.0)


def test_always_flaky_baseline_is_prevalence() -> None:
    """平凡基线：全报警 → 召回 1.0，精确率 = 正样本占比。"""
    p, r, f1 = always_flaky_baseline({"tp": 4, "fp": 0, "fn": 0, "tn": 6})
    assert r == 1.0
    assert p == 4 / 10
    assert abs(f1 - 2 * 0.4 / 1.4) < 1e-9


def test_by_category_splits_positive_recall() -> None:
    preds = [
        _pred(category="NIO"),
        _pred(category="NIO", y_pred=0),
        _pred(category="OD", y_pred=0),
        _pred(category="OD", y_pred=0),
        _pred(y_true=0, category="N1", y_pred=0),  # 负样本不进类别拆分
    ]
    cats = by_category(preds)
    assert cats["NIO"] == {"n": 2, "detected": 1, "recall": 0.5}
    assert cats["OD"] == {"n": 2, "detected": 0, "recall": 0.0}


def test_status_breakdown_counts_exclusions() -> None:
    preds = [
        _pred(excluded=True, exclude_reason="DEPS_FAIL"),
        _pred(excluded=True, exclude_reason="DEPS_FAIL"),
        _pred(excluded=True, exclude_reason="SHA_POST_FIX"),
        _pred(),  # 未排除不进 breakdown
    ]
    assert status_breakdown(preds) == {"DEPS_FAIL": 2, "SHA_POST_FIX": 1}


def test_format_report_has_baseline_on_every_number() -> None:
    """硬格式：精确率/召回/F1 三个数必须都带基线（铁律 3）。"""
    preds = [
        _pred(),
        _pred(),
        _pred(y_pred=0),
        _pred(y_true=0, category="N1", y_pred=0),
    ]
    lines = format_report(preds, runs=100, negatives_per_project=1)
    text = "\n".join(lines)
    assert "精确率 100.0%（下界；always-flaky 基线 75.0%）" in text
    assert "Recall@IDoFT 66.7%（always-flaky 基线 100.0%）" in text
    assert "F1 0.800（always-flaky 基线 0.857）" in text
    assert "TP=2 FP=0 FN=1 TN=1" in text


def test_write_results_csv_roundtrip(tmp_path) -> None:
    preds = [_pred(), _pred(y_true=0, category="N1", y_pred=0, fail_rate=0.0)]
    out = tmp_path / "results.csv"
    write_results_csv(preds, out)
    text = out.read_text(encoding="utf-8")
    assert "y_true" in text and "fail_rate" in text
    assert text.count("\n") == 3  # 表头 + 2 行


def test_summarize_json_serializable(tmp_path) -> None:
    preds = [_pred(), _pred(y_true=0, category="N1", y_pred=0)]
    summary = summarize(preds, runs=100, negatives_per_project=1)
    write_metrics_json(summary, tmp_path / "metrics.json")
    loaded = json.loads((tmp_path / "metrics.json").read_text(encoding="utf-8"))
    assert loaded["mode"] == "replay"
    assert loaded["confusion_matrix"]["tp"] == 1
    assert "always_flaky" in loaded["baselines"]
