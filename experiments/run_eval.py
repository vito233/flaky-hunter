"""C1 第一版评测驱动：replay 模式检测器 × 阶段 A 真值 + 自建 N1 负样本。

用法：
    uv run python experiments/run_eval.py --runs 10 --negatives 1   # 试点（快）
    uv run python experiments/run_eval.py --runs 100 --negatives 2  # 正式
    uv run python experiments/run_eval.py --projects mtik00/yamicache,didix21/mdutils

产出（AGENTS.md：每个数字都要能在 experiments/results.csv 里查到）：
    experiments/results.csv          逐行结果（TP/FP/FN/TN 的原始事实）
    experiments/metrics.json         聚合指标 + always-flaky 基线
    experiments/raw/*.log            首个失败日志（证据留存）

口径说明（写死在这里，防以后自己问"这数怎么算的"）：
    - 正样本 = 阶段 A 复核可复现的 19 条（penman 因 SHA 已含修复剔除，
      理由记在 ground_truth_phase_a.csv 的 exclude_reason 列）
    - 负样本 = N1 口径：同项目同 SHA 下 IDoFT 未记录的测试，定种子抽样
    - y_pred：verdict == "flaky" 才算报警；stable / inconclusive 都算未报警
    - 环境类失败（DEPS_FAIL / TEST_NOT_FOUND / TIMEOUT / FETCH_FAIL）不进指标，
      单独报数 —— 不许悄悄消失，也不许冒充"没翻转"
    - 镜像默认 python:3.9-slim：方法论按年份选镜像是通则，但阶段 A 实测
      这 7 个项目在 3.9 上全部可跑（且本机只拉得到 3.9），优先用证据
"""

from __future__ import annotations

import argparse
import csv
import logging
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))  # 包未安装时也能直接跑

from flaky_hunter.detect_replay import DEFAULT_RERUN_COUNT, detect_replay  # noqa: E402
from flaky_hunter.metrics import (  # noqa: E402
    Prediction,
    format_report,
    summarize,
    write_metrics_json,
    write_results_csv,
)
from flaky_hunter.negatives import filter_recorded, select_negatives  # noqa: E402
from flaky_hunter.runner import DEFAULT_IMAGE, DockerRunner  # noqa: E402
from flaky_hunter.sources import (  # noqa: E402
    SourceError,
    ensure_source,
    resolve_test_path,
)

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("run_eval")

DEFAULT_SEED = 1729
# 镜像默认 python:3.9-slim：方法论按年份选镜像是通则，但阶段 A 实测
# 这 7 个项目在 3.9 上全部可跑（且本机只拉得到 3.9），优先用证据。
# DEFAULT_IMAGE 已带国内 mirror 前缀 —— 直连官方源会被代理破坏
# （runner.mirror_image 的实测注释）。


@dataclass
class GroundTruth:
    project: str
    sha: str
    test_name: str
    category: str
    eval_eligible: bool
    exclude_reason: str


@dataclass
class ProjectTask:
    project: str
    sha: str
    positives: list[GroundTruth]


def load_ground_truth(path: Path) -> list[GroundTruth]:
    with open(path, encoding="utf-8") as f:
        return [
            GroundTruth(
                project=r["project"],
                sha=r["sha"],
                test_name=r["test_name"],
                category=r["category"],
                eval_eligible=r["eval_eligible"] == "true",
                exclude_reason=r["exclude_reason"],
            )
            for r in csv.DictReader(f)
        ]


def _safe_name(text: str) -> str:
    return re.sub(r"[^\w.-]+", "_", text)


def _detect_one(
    *,
    gt: GroundTruth,
    runner: DockerRunner,
    src: Path,
    deps: Path,
    runs: int,
    per_run_timeout: int,
    raw_dir: Path,
) -> Prediction:
    """跑一条检测，任何异常都变成"带原因的排除行"，不让单个测试炸掉整轮。"""
    try:
        result = detect_replay(
            project=gt.project,
            sha=gt.sha,
            test_name=gt.test_name,
            runner=runner,
            src_dir=src,
            deps_dir=deps,
            runs=runs,
            per_run_timeout=per_run_timeout,
            raw_log_dir=raw_dir,
        )
    except RuntimeError as ex:
        return Prediction(
            project=gt.project,
            sha=gt.sha,
            test_name=gt.test_name,
            category=gt.category,
            y_true=1,
            y_pred=0,
            verdict="",
            status="",
            runs_total=0,
            passed=0,
            failed=0,
            fail_rate=None,
            excluded=True,
            exclude_reason="RUNNER_ERROR",
            note=str(ex),
        )

    # 首个失败日志已由 detect_replay 落盘到 raw_dir，路径在 result.raw_log_path
    status = result.status
    excluded = status != "OK"
    count_runs = result.count_runs
    return Prediction(
        project=gt.project,
        sha=gt.sha,
        test_name=gt.test_name,
        category=gt.category,
        y_true=1,
        y_pred=1 if result.verdict == "flaky" else 0,
        verdict=result.verdict,
        status=status,
        runs_total=result.runs.total if result.runs else 0,
        passed=result.runs.passed if result.runs else 0,
        failed=result.runs.failed if result.runs else 0,
        fail_rate=(result.runs.failed / result.runs.total)
        if result.runs and result.runs.total
        else None,
        count_passed=count_runs.passed if count_runs else 0,
        count_failed=count_runs.failed if count_runs else 0,
        count_fail_rate=(count_runs.failed / count_runs.total)
        if count_runs and count_runs.total
        else None,
        excluded=excluded,
        exclude_reason=status if excluded else "",
        note=(
            result.confidence.basis
            + (f" | {result.confidence.note}" if result.confidence.note else "")
        ),
    )


def run_project(
    task: ProjectTask,
    *,
    runner: DockerRunner,
    cache: Path,
    runs: int,
    per_run_timeout: int,
    negatives_per_project: int,
    seed: int,
    raw_dir: Path,
) -> list[Prediction]:
    """一个项目的完整流水线：源码 → 依赖 → 正样本检测 → 负样本构造 + 检测。"""
    name = task.project.split("/")[-1]
    preds: list[Prediction] = []

    try:
        src = ensure_source(task.project, task.sha, cache)
    except SourceError as ex:
        log.warning("  ✗ 源码获取失败: %s", ex)
        return [
            Prediction(
                project=gt.project,
                sha=gt.sha,
                test_name=gt.test_name,
                category=gt.category,
                y_true=1,
                y_pred=0,
                verdict="",
                status="",
                runs_total=0,
                passed=0,
                failed=0,
                fail_rate=None,
                excluded=True,
                exclude_reason="FETCH_FAIL",
                note=str(ex),
            )
            for gt in task.positives
        ]

    deps = cache / "deps" / f"{name}-{task.sha[:12]}"
    setup = runner.setup(src, deps)
    if not setup.tools_ok:
        log.warning("  ✗ 依赖安装失败（工具链都没装上）: %s", setup.log_tail[-200:])
        return [
            Prediction(
                project=gt.project,
                sha=gt.sha,
                test_name=gt.test_name,
                category=gt.category,
                y_true=1,
                y_pred=0,
                verdict="",
                status="",
                runs_total=0,
                passed=0,
                failed=0,
                fail_rate=None,
                excluded=True,
                exclude_reason="DEPS_FAIL",
                note=setup.log_tail[-200:],
            )
            for gt in task.positives
        ]
    if not setup.project_installed:
        log.info("  ℹ 项目自身依赖没装上（零依赖项目裸跑，不影响）")

    # ---- 正样本 ----
    resolved: dict[str, str] = {}
    for gt in task.positives:
        target = resolve_test_path(src, gt.test_name)
        resolved[gt.test_name] = target
        if target != gt.test_name:
            log.info("  ↻ 路径修正: %s → %s", gt.test_name, target)
        gt_r = GroundTruth(**{**gt.__dict__, "test_name": target})
        pred = _detect_one(
            gt=gt_r,
            runner=runner,
            src=src,
            deps=deps,
            runs=runs,
            per_run_timeout=per_run_timeout,
            raw_dir=raw_dir,
        )
        # 行里记 IDoFT 原始测试名（可追溯），检测用的是修正后的
        pred.test_name = gt.test_name
        preds.append(pred)
        tag = "flaky" if pred.y_pred else pred.verdict or pred.exclude_reason
        log.info(
            "  %s %s [%s] %s过/%s挂",
            "✅" if pred.y_pred else "⬚",
            gt.test_name.rsplit("::", 1)[-1],
            tag,
            pred.passed,
            pred.failed,
        )

    # ---- 负样本（N1：同项目同 SHA，IDoFT 未记录）----
    testdir = "tests" if (src / "tests").is_dir() else "."
    collected = runner.collect(src, deps, testdir=testdir)
    if not collected.ok:
        log.warning("  ⚠ 负样本收集失败（%s）—— 本项目无负样本", collected.reason)
        return preds

    candidates = filter_recorded(collected.tests, set(resolved.values()))
    negatives = select_negatives(candidates, negatives_per_project, seed)
    log.info("  ➖ 负样本候选 %d 条，抽样 %d 条", len(candidates), len(negatives))
    for node in negatives:
        pred = _detect_one(
            gt=GroundTruth(
                project=task.project,
                sha=task.sha,
                test_name=node,
                category="N1",
                eval_eligible=True,
                exclude_reason="",
            ),
            runner=runner,
            src=src,
            deps=deps,
            runs=runs,
            per_run_timeout=per_run_timeout,
            raw_dir=raw_dir,
        )
        # 负样本行的 y_true=0（上面借了正样本的构造，这里改正）
        pred.y_true = 0
        preds.append(pred)
        tag = "flaky" if pred.y_pred else pred.verdict or pred.exclude_reason
        log.info(
            "  %s %s [%s] %s过/%s挂",
            "🚨" if pred.y_pred else "⬚",
            node.rsplit("::", 1)[-1],
            tag,
            pred.passed,
            pred.failed,
        )
    return preds


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--runs", type=int, default=DEFAULT_RERUN_COUNT)
    ap.add_argument(
        "--negatives", type=int, default=2, help="每项目负样本条数（N1 口径）"
    )
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--per-run-timeout", type=int, default=120)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--image", default=DEFAULT_IMAGE)
    ap.add_argument("--projects", default="", help="逗号分隔，只跑这些项目（试点用）")
    ap.add_argument(
        "--ground-truth",
        default=str(REPO_ROOT / "experiments" / "ground_truth_phase_a.csv"),
    )
    ap.add_argument("--out-csv", default=str(REPO_ROOT / "experiments" / "results.csv"))
    ap.add_argument(
        "--out-json", default=str(REPO_ROOT / "experiments" / "metrics.json")
    )
    ap.add_argument("--cache", default=str(REPO_ROOT / ".cache"))
    args = ap.parse_args()

    gt_all = load_ground_truth(Path(args.ground_truth))
    only = {s.strip() for s in args.projects.split(",") if s.strip()}
    tasks: dict[tuple[str, str], ProjectTask] = {}
    for gt in gt_all:
        if only and gt.project not in only:
            continue
        if not gt.eval_eligible:
            # 阶段 A 就不可复现的（penman）—— 记一行排除，不进指标
            log.info("排除真值 %s（%s）", gt.test_name, gt.exclude_reason)
            continue
        key = (gt.project, gt.sha)
        tasks.setdefault(key, ProjectTask(project=gt.project, sha=gt.sha, positives=[]))
        tasks[key].positives.append(gt)

    log.info("=" * 70)
    log.info(
        "评测集：%d 条正样本 / %d 个项目；负样本 N1 ×%d/项目；runs=%d；镜像 %s",
        sum(len(t.positives) for t in tasks.values()),
        len(tasks),
        args.negatives,
        args.runs,
        args.image,
    )
    log.info("=" * 70)

    runner = DockerRunner(image=args.image)
    raw_dir = REPO_ROOT / "experiments" / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    preds: list[Prediction] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(
                run_project,
                task,
                runner=runner,
                cache=Path(args.cache),
                runs=args.runs,
                per_run_timeout=args.per_run_timeout,
                negatives_per_project=args.negatives,
                seed=args.seed,
                raw_dir=raw_dir,
            ): task
            for task in tasks.values()
        }
        for fut in as_completed(futures):
            task = futures[fut]
            try:
                preds.extend(fut.result())
            except Exception as ex:
                log.exception("项目 %s 整轮炸掉: %s", task.project, ex)

    # ---- 落盘 ----
    write_results_csv(preds, Path(args.out_csv))
    summary = summarize(preds, runs=args.runs, negatives_per_project=args.negatives)
    write_metrics_json(summary, Path(args.out_json))

    log.info("=" * 70)
    for line in format_report(
        preds, runs=args.runs, negatives_per_project=args.negatives
    ):
        log.info(line)
    log.info("总耗时 %.0fs → %s / %s", time.time() - t0, args.out_csv, args.out_json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
