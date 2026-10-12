"""C1 后半段评测驱动：顺序变换检测器 × 与 replay 同一套评测集。

用法：
    uv run python experiments/run_order_eval.py --projects didix21/mdutils  # 试点
    uv run python experiments/run_order_eval.py --orders 4 --alone-runs 10  # 正式

产出（AGENTS.md：每个数字都要能在 experiments/*.csv 里查到）：
    experiments/results_order.csv       顺序模式逐行结果（含 polluter 归因）
    experiments/metrics_order.json      顺序模式独立指标 + always-flaky 基线
    experiments/metrics_combined.json   合并口径（replay ∪ order）+ 基线
    experiments/raw/*__order.log        每个 victim 的归因证据（失败签名）

口径说明（写死在这里，防以后自己问"这数怎么算的"）：
    - 评测集与 run_eval.py 完全一致：19 条阶段 A 复核可复现的正样本
      + N1 负样本 ×2/项目（同种子 1729，抽样逻辑复用 run_eval）
    - y_pred（顺序模式）：只有"三条件验证全过"的 victim 算报警 ——
      某顺序挂 ∧ 单独跑 ×alone_runs 全过 ∧ 最小 polluter 集合能复现
    - 合并口径：y_pred = replay 报警 ∨ order 报警（两个模式抓不同机制，
      是互补不是竞争）；按键 (project, sha, test_name) 对齐
    - 顺序模式抓到的 victim 若不在评测集里（IDoFT 未记录、也没抽中），
      单独报数不进混淆矩阵 —— 无真值的行不进指标（可能是 IDoFT 漏记的
      真 flaky，恰是 limitations 第 1 条"精确率下界"的现实版）
    - 项目级失败（收集失败 / 容器没跑起来）→ 该项目所有行排除，
      exclude_reason=ORDER_SCAN_FAIL，不许拿不完整的扫描当"没 victim"
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))  # 包未安装时也能直接跑

from flaky_hunter.detect_order import (  # noqa: E402
    DEFAULT_ALONE_RUNS,
    DEFAULT_MINIMIZE_BUDGET,
    DEFAULT_ORDERS,
    DEFAULT_SEED,
    OrderScan,
    scan_project_order,
)
from flaky_hunter.metrics import (  # noqa: E402
    Prediction,
    combine_modes,
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

sys.path.insert(0, str(REPO_ROOT / "experiments"))  # 复用 run_eval 的真值加载
from run_eval import GroundTruth, load_ground_truth  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("run_order_eval")


def load_replay_predictions(path: Path) -> list[Prediction]:
    """读 replay 模式的 results.csv（合并口径的左侧输入）。"""
    with open(path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    preds: list[Prediction] = []
    for r in rows:
        preds.append(
            Prediction(
                project=r["project"],
                sha=r["sha"],
                test_name=r["test_name"],
                category=r["category"],
                y_true=int(r["y_true"]),
                y_pred=int(r["y_pred"]),
                verdict=r["verdict"],
                status=r["status"],
                runs_total=int(r["runs_total"]),
                passed=int(r["passed"]),
                failed=int(r["failed"]),
                fail_rate=float(r["fail_rate"]) if r["fail_rate"] else None,
                count_passed=int(r["count_passed"]),
                count_failed=int(r["count_failed"]),
                count_fail_rate=float(r["count_fail_rate"])
                if r["count_fail_rate"]
                else None,
                excluded=r["excluded"] == "True",
                exclude_reason=r["exclude_reason"],
                note=r["note"],
                mode="replay",
            )
        )
    return preds


def run_project(
    project: str,
    sha: str,
    positives: list[GroundTruth],
    *,
    runner: DockerRunner,
    cache: Path,
    orders: int,
    alone_runs: int,
    negatives_per_project: int,
    seed: int,
    minimize_budget: int,
    per_run_timeout: int,
    raw_dir: Path,
) -> tuple[list[Prediction], OrderScan | None, list[str]]:
    """一个项目的完整顺序流水线。返回 (评测行, 扫描结果, 集外 victim)。"""
    name = project.split("/")[-1]
    try:
        src = ensure_source(project, sha, cache)
    except SourceError as ex:
        log.warning("  ✗ 源码获取失败: %s", ex)
        preds = [
            Prediction(
                project=project,
                sha=sha,
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
                mode="order",
            )
            for gt in positives
        ]
        return preds, None, []

    deps = cache / "deps" / f"{name}-{sha[:12]}"
    setup = runner.setup(src, deps)
    if not setup.tools_ok:
        log.warning("  ✗ 依赖安装失败: %s", setup.log_tail[-200:])
        preds = [
            Prediction(
                project=project,
                sha=sha,
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
                mode="order",
            )
            for gt in positives
        ]
        return preds, None, []

    testdir = "tests" if (src / "tests").is_dir() else "."
    scan = scan_project_order(
        project=project,
        sha=sha,
        runner=runner,
        src_dir=src,
        deps_dir=deps,
        testdir=testdir,
        orders=orders,
        seed=seed,
        alone_runs=alone_runs,
        minimize_budget=minimize_budget,
        per_run_timeout=per_run_timeout,
        raw_log_dir=raw_dir,
    )
    if scan.error:
        log.warning("  ✗ 顺序扫描失败: %s", scan.error)
        preds = [
            Prediction(
                project=project,
                sha=sha,
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
                exclude_reason="ORDER_SCAN_FAIL",
                note=scan.error,
                mode="order",
            )
            for gt in positives
        ]
        return preds, scan, []

    victim_by_node = {v.test_name: v for v in scan.victims}

    # ---- 正样本行（与 replay 同一批；路径修正逻辑同 run_eval）----
    resolved: dict[str, str] = {}
    for gt in positives:
        resolved[gt.test_name] = resolve_test_path(src, gt.test_name)
    preds: list[Prediction] = []
    for gt in positives:
        target = resolved[gt.test_name]
        victim = victim_by_node.get(target)
        y_pred = 1 if victim else 0
        note = (
            victim.confidence.basis
            + (f" | {victim.confidence.note}" if victim.confidence.note else "")
            if victim
            else scan.non_victim_reasons.get(
                target, "NOT_IN_COLLECTED_SET:收集不到该测试"
            )
        )
        preds.append(
            Prediction(
                project=project,
                sha=sha,
                test_name=gt.test_name,
                category=gt.category,
                y_true=1,
                y_pred=y_pred,
                verdict="flaky" if victim else "inconclusive",
                status="OK",
                runs_total=alone_runs if victim else 0,
                passed=alone_runs if victim else 0,
                failed=0,
                fail_rate=0.0 if victim else None,
                note=note,
                mode="order",
                polluters=";".join(victim.order_analysis.polluters)
                if victim and victim.order_analysis
                else "",
                orders_tried=scan.orders_tried,
                minimized=bool(victim.order_analysis.minimized)
                if victim and victim.order_analysis
                else False,
            )
        )
        log.info(
            "  %s %s [%s]%s",
            "✅" if y_pred else "⬚",
            gt.test_name.rsplit("::", 1)[-1],
            "flaky" if y_pred else "inconclusive",
            f" ← polluter: {preds[-1].polluters}" if y_pred else "",
        )

    # ---- 负样本行（N1：同项目同 SHA、IDoFT 未记录；同 run_eval 抽样）----
    collected = runner.collect(src, deps, testdir=testdir)
    if not collected.ok:
        log.warning("  ⚠ 负样本收集失败（%s）—— 本项目无负样本行", collected.reason)
        return preds, scan, []

    candidates = filter_recorded(collected.tests, set(resolved.values()))
    negatives = select_negatives(candidates, negatives_per_project, seed)
    for node in negatives:
        victim = victim_by_node.get(node)
        y_pred = 1 if victim else 0
        preds.append(
            Prediction(
                project=project,
                sha=sha,
                test_name=node,
                category="N1",
                y_true=0,
                y_pred=y_pred,
                verdict="flaky" if victim else "inconclusive",
                status="OK",
                runs_total=alone_runs if victim else 0,
                passed=alone_runs if victim else 0,
                failed=0,
                fail_rate=0.0 if victim else None,
                note=(
                    victim.confidence.basis
                    if victim
                    else scan.non_victim_reasons.get(node, "ORDERS_ALL_PASS")
                ),
                mode="order",
                polluters=";".join(victim.order_analysis.polluters)
                if victim and victim.order_analysis
                else "",
                orders_tried=scan.orders_tried,
                minimized=bool(victim.order_analysis.minimized)
                if victim and victim.order_analysis
                else False,
            )
        )
        if y_pred:
            log.info("  🚨 负样本 %s 被顺序模式判 flaky（疑 FP 或 IDoFT 漏记）", node)

    # ---- 集外 victim：抓到了但不在评测集里 → 只报数，不进矩阵 ----
    eval_nodes = set(resolved.values()) | set(negatives)
    extra = sorted(scan.victim_nodes - eval_nodes)
    if extra:
        log.info("  ℹ 集外 victim %d 条（无真值，不进指标）: %s", len(extra), extra)
    return preds, scan, extra


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--orders", type=int, default=DEFAULT_ORDERS)
    ap.add_argument("--alone-runs", type=int, default=DEFAULT_ALONE_RUNS)
    ap.add_argument(
        "--negatives",
        type=int,
        default=2,
        help="每项目负样本条数（N1 口径，同 run_eval）",
    )
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--minimize-budget", type=int, default=DEFAULT_MINIMIZE_BUDGET)
    ap.add_argument("--per-run-timeout", type=int, default=120)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--image", default=DEFAULT_IMAGE)
    ap.add_argument("--projects", default="", help="逗号分隔，只跑这些项目（试点用）")
    ap.add_argument(
        "--ground-truth",
        default=str(REPO_ROOT / "experiments" / "ground_truth_phase_a.csv"),
    )
    ap.add_argument(
        "--replay-csv", default=str(REPO_ROOT / "experiments" / "results.csv")
    )
    ap.add_argument(
        "--out-csv", default=str(REPO_ROOT / "experiments" / "results_order.csv")
    )
    ap.add_argument(
        "--out-json", default=str(REPO_ROOT / "experiments" / "metrics_order.json")
    )
    ap.add_argument(
        "--out-combined-json",
        default=str(REPO_ROOT / "experiments" / "metrics_combined.json"),
    )
    ap.add_argument("--cache", default=str(REPO_ROOT / ".cache"))
    args = ap.parse_args()

    gt_all = load_ground_truth(Path(args.ground_truth))
    only = {s.strip() for s in args.projects.split(",") if s.strip()}
    tasks: dict[tuple[str, str], list[GroundTruth]] = {}
    for gt in gt_all:
        if only and gt.project not in only:
            continue
        if not gt.eval_eligible:
            continue
        tasks.setdefault((gt.project, gt.sha), []).append(gt)

    log.info("=" * 70)
    log.info(
        "顺序变换评测：%d 条正样本 / %d 个项目；orders=%d；alone_runs=%d；"
        "负样本 N1 ×%d/项目；ddmin 预算 %d；镜像 %s",
        sum(len(v) for v in tasks.values()),
        len(tasks),
        args.orders,
        args.alone_runs,
        args.negatives,
        args.minimize_budget,
        args.image,
    )
    log.info("=" * 70)

    runner = DockerRunner(image=args.image)
    raw_dir = REPO_ROOT / "experiments" / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    preds: list[Prediction] = []
    extra_victims: list[str] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(
                run_project,
                project,
                sha,
                positives,
                runner=runner,
                cache=Path(args.cache),
                orders=args.orders,
                alone_runs=args.alone_runs,
                negatives_per_project=args.negatives,
                seed=args.seed,
                minimize_budget=args.minimize_budget,
                per_run_timeout=args.per_run_timeout,
                raw_dir=raw_dir,
            ): (project, sha)
            for (project, sha), positives in tasks.items()
        }
        for fut in as_completed(futures):
            project, _sha = futures[fut]
            try:
                p, _scan, extra = fut.result()
                preds.extend(p)
                extra_victims.extend(f"{project}::{e}" for e in extra)
            except Exception as ex:
                log.exception("项目 %s 整轮炸掉: %s", project, ex)

    # ---- 顺序模式独立指标 ----
    write_results_csv(preds, Path(args.out_csv))
    order_summary = summarize(
        preds,
        runs=args.orders,
        negatives_per_project=args.negatives,
        mode="order",
    )
    order_summary["alone_runs"] = args.alone_runs
    order_summary["extra_victims_outside_eval_set"] = extra_victims
    write_metrics_json(order_summary, Path(args.out_json))

    # ---- 合并口径（replay ∪ order）----
    merged = None
    if Path(args.replay_csv).exists():
        replay_preds = load_replay_predictions(Path(args.replay_csv))
        merged = combine_modes(replay_preds, preds)
        combined_summary = summarize(
            merged,
            runs=100,
            negatives_per_project=args.negatives,
            mode="replay+order",
        )
        combined_summary["alone_runs"] = args.alone_runs
        combined_summary["orders"] = args.orders
        write_metrics_json(combined_summary, Path(args.out_combined_json))
    else:
        log.warning("  ⚠ 找不到 replay 结果 %s，跳过合并口径", args.replay_csv)

    log.info("=" * 70)
    for line in format_report(
        preds,
        runs=args.orders,
        negatives_per_project=args.negatives,
        header=(
            f"顺序变换模式 · OD 家族检测（{args.orders} 个顺序 + 二分/ddmin；"
            f"victim 单独跑 ×{args.alone_runs}；负样本 N1 ×{args.negatives}/项目）"
        ),
    ):
        log.info(line)
    if merged is not None:
        for line in format_report(
            merged,
            runs=100,
            negatives_per_project=args.negatives,
            header=(
                f"合并口径 replay ∪ order（换进程 ×100 + {args.orders} 个顺序；"
                f"负样本 N1 ×{args.negatives}/项目）"
            ),
        ):
            log.info(line)
    if extra_victims:
        log.info(
            "集外 victim（无真值，未进指标）%d 条：%s",
            len(extra_victims),
            extra_victims,
        )
    log.info(
        "总耗时 %.0fs → %s / %s / %s",
        time.time() - t0,
        args.out_csv,
        args.out_json,
        args.out_combined_json,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
