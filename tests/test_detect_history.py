"""detect_history（模式 B）单测 —— 全部用合成 JUnit XML，不碰 Docker。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from flaky_hunter.detect_history import (
    classify_history,
    detect_history,
    parse_junit_file,
    report_markdown,
)
from flaky_hunter.schema import HistoryReport


def _write_junit(
    dir_path: Path,
    filename: str,
    cases: list[tuple[str, str, str]],
    *,
    layout: str = "xunit2",
) -> None:
    """合成一份 JUnit XML。

    cases = [(classname, name, outcome)]，outcome ∈ {"P", "F", "E", "S"}。
    """
    inner = []
    for classname, name, outcome in cases:
        if outcome in ("F", "E"):
            tag = "failure" if outcome == "F" else "error"
            inner.append(
                f'<testcase classname="{classname}" name="{name}">'
                f'<{tag} message="boom" type="AssertionError">tb</{tag}>'
                f"</testcase>"
            )
        elif outcome == "S":
            inner.append(
                f'<testcase classname="{classname}" name="{name}">'
                f'<skipped message="skip it" /></testcase>'
            )
        else:
            inner.append(
                f'<testcase classname="{classname}" name="{name}" time="0.01"/>'
            )
    body = "".join(inner)
    if layout == "xunit2":
        xml = (
            '<?xml version="1.0" encoding="utf-8"?>'
            f'<testsuites><testsuite name="pytest" tests="{len(cases)}" '
            f'time="0.1">{body}</testsuite></testsuites>'
        )
    else:
        xml = (
            '<?xml version="1.0" encoding="utf-8"?>'
            f'<testsuite name="pytest" tests="{len(cases)}" '
            f'time="0.1">{body}</testsuite>'
        )
    (dir_path / filename).write_text(xml, encoding="utf-8")


def _one_test_history(
    tmp_path: Path,
    outcomes: list[str],
    *,
    name: str = "tests.test_x::test_a",
) -> Path:
    """生成一个只含单个测试指定结果序列的历史目录，文件名 0001..NNNN。"""
    xml_dir = tmp_path / "history"
    xml_dir.mkdir()
    for i, outcome in enumerate(outcomes, start=1):
        _write_junit(xml_dir, f"{i:04d}.xml", [("tests.test_x", "test_a", outcome)])
    return xml_dir


def _get(report: HistoryReport, test_id: str):
    return next(r for r in report.results if r.test_id == test_id)


# ---------- classify_history：判定规则 ----------


def test_flip_detected_flaky():
    verdict, conf = classify_history("PFPF")
    assert verdict == "flaky"
    assert "相邻翻转 3 次" in conf.basis
    assert "少数派口径 2" in conf.basis


def test_flaky_low_confidence_when_short():
    verdict, conf = classify_history("PF")
    assert verdict == "flaky"
    assert conf.level == "low"


def test_flaky_medium_confidence_when_20_plus():
    verdict, conf = classify_history("PF" * 15)  # 30 次有效观察
    assert verdict == "flaky"
    assert conf.level == "medium"


def test_all_pass_60_stable_medium_cap():
    """历史口径 stable 封顶 medium（跨 commit/环境前提）。"""
    verdict, conf = classify_history("P" * 60)
    assert verdict == "stable"
    assert conf.level == "medium"
    assert "二项检验" in conf.basis
    assert "同 SHA 校验后方可升 high" in conf.basis


def test_all_pass_170_still_medium_but_cites_external_line():
    verdict, conf = classify_history("P" * 170)
    assert verdict == "stable"
    assert conf.level == "medium"
    assert "170" in conf.note


def test_all_pass_short_inconclusive():
    verdict, conf = classify_history("P" * 30)
    assert verdict == "inconclusive"
    assert "0 次翻转 ≠ 稳定" in conf.basis


def test_all_fail_inconclusive():
    """全挂 ≠ 翻转 —— 三分类口径的核心边界。"""
    verdict, conf = classify_history("F" * 10)
    assert verdict == "inconclusive"
    assert "全挂 ≠ flaky" in conf.basis


def test_skipped_excluded_from_valid_runs():
    verdict, conf = classify_history("PPPSS")
    assert verdict == "inconclusive"  # 有效只有 3 < 60
    assert "2 次跳过" in conf.note


def test_no_valid_observation():
    verdict, _ = classify_history("SSS")
    assert verdict == "inconclusive"


# ---------- parse_junit_file ----------


def test_parse_old_layout_top_level_testsuite(tmp_path):
    _write_junit(
        tmp_path,
        "run.xml",
        [("tests.test_x", "test_a", "P"), ("tests.test_x", "test_b", "F")],
        layout="legacy",
    )
    outcomes = parse_junit_file(tmp_path / "run.xml")
    assert outcomes == {"tests.test_x::test_a": "P", "tests.test_x::test_b": "F"}


def test_error_counts_as_fail(tmp_path):
    _write_junit(tmp_path, "run.xml", [("tests.test_x", "test_a", "E")])
    assert parse_junit_file(tmp_path / "run.xml")["tests.test_x::test_a"] == "F"


def test_corrupt_xml_raises(tmp_path):
    (tmp_path / "bad.xml").write_text("<testsuite><testcase>", encoding="utf-8")
    with pytest.raises(ValueError, match="JUnit XML 解析失败"):
        parse_junit_file(tmp_path / "bad.xml")


# ---------- detect_history：目录级 ----------


def test_empty_dir_raises(tmp_path):
    with pytest.raises(ValueError, match="没有 JUnit XML"):
        detect_history(project="x/y", xml_dir=tmp_path)


def test_missing_files_counted(tmp_path):
    """测试只在 3 个历史 run 中的 2 个出现 → missing_files=1。"""
    xml_dir = _one_test_history(tmp_path, ["P", "F", "P"])
    (xml_dir / "0003.xml").unlink()
    _write_junit(xml_dir, "0003.xml", [("tests.test_x", "test_other", "P")])
    report = detect_history(project="x/y", xml_dir=xml_dir)
    r = _get(report, "tests.test_x::test_a")
    assert r.missing_files == 1
    assert r.runs_total == 2


def test_parametrized_names_distinct(tmp_path):
    xml_dir = tmp_path / "h"
    xml_dir.mkdir()
    _write_junit(
        xml_dir,
        "0001.xml",
        [
            ("tests.test_x", "test_a[case1]", "P"),
            ("tests.test_x", "test_a[case2]", "F"),
        ],
    )
    report = detect_history(project="x/y", xml_dir=xml_dir)
    assert report.summary.tests_total == 2


def test_filename_order_determines_time_order(tmp_path):
    """0001=P 0002=F 0003=P → transitions=2；乱序解读会算出 1。"""
    xml_dir = _one_test_history(tmp_path, ["P", "F", "P"])
    report = detect_history(project="x/y", xml_dir=xml_dir)
    r = _get(report, "tests.test_x::test_a")
    assert r.sequence == "PFP"
    assert r.flip_transitions == 2


def test_summary_and_markdown(tmp_path):
    xml_dir = tmp_path / "h"
    xml_dir.mkdir()
    _write_junit(xml_dir, "0001.xml", [("tests.test_x", "test_flaky", "P")])
    _write_junit(xml_dir, "0002.xml", [("tests.test_x", "test_flaky", "F")])
    _write_junit(xml_dir, "0003.xml", [("tests.test_x", "test_flaky", "P")])
    report = detect_history(project="x/y", xml_dir=xml_dir)
    assert report.summary.flaky == 1
    assert report.summary.stable == 0
    assert report.summary.inconclusive == 0
    md = report_markdown(report)
    assert "test_flaky" in md
    assert "PFP" in md
    assert report.files_parsed == 3
    assert "filename-lexicographic" in report.ordering


def test_schema_roundtrip(tmp_path):
    xml_dir = _one_test_history(tmp_path, ["P", "F"])
    report = detect_history(project="x/y", xml_dir=xml_dir)
    restored = HistoryReport.model_validate_json(report.model_dump_json())
    assert restored.results[0].sequence == "PF"
    assert restored.timestamp_utc.tzinfo is not None
    payload = json.loads(report.model_dump_json())
    assert payload["schema_version"] == "1.0"
