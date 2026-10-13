"""N3 负样本复核实验（规范 §2.3 / TODOLIST 原第 2 周 DoD）。

N1 负样本 = 同项目、同 SHA 下 IDoFT 未记录的测试 —— **有噪声**（IDoFT
漏记的真 flaky 会混进负样本列，把精确率往下压）。本实验回答一个问题：
**N1 到底有多干净？**

做法：7 个评测项目，每项目抽 8 条 N1 负样本（固定 seed 可复现），每条
**换进程重跑 K=30 遍**（DoD 底线 K≥30）。30 遍全过 = "K 遍内无翻转"；
出现 fail = N1 混入了真 flaky（噪声）。产出"确认稳定比例"，写进 README
局限章节。

统计口径声明（必读，防误读）：
    30 遍全过 **≠ 证明稳定** —— 95% 置信确认不 flaky 平均要 170 遍
    （规范 §2.2）。这个数字的正确读法是**噪声率上界**：对真实失败率
    ≥5% 的测试，30 遍内至少观察到一次翻转的概率 ≈ 1 - 0.95^30 ≈ 78.5%
    —— 弱翻转（<5% 失败率）抓不全，所以"全过比例"是 N1 纯度的下界。

用法（本机：Docker 在 WSL，走多段前缀；CI 无需本脚本）：
    uv run python experiments/run_negatives_recheck.py \
        --src-root /home/code/fh-recheck \
        --deps-root /home/code/fh-recheck/deps \
        --docker-bin "wsl;-d;Ubuntu-22.04;-e;docker" \
        --per-project 8 --runs 30 --seed 42 \
        --out experiments/negatives_recheck.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from flaky_hunter.negatives import filter_recorded, select_negatives
from flaky_hunter.runner import DockerRunner

log = logging.getLogger("recheck")

GROUND_TRUTH = (
    Path(__file__).resolve().parents[1] / "experiments" / "ground_truth_phase_a.csv"
)

# 统计口径：30 遍内抓到 ≥5% 失败率翻转的概率（写进报告，防误读）
CATCH_PROB_5PCT = 1 - 0.95**30

# 每项目测试目录：python-fs 的测试在 fs/tests/（真值表测试名可证），
# 统一 "tests" 会 COLLECT_ERROR。封闭集合，显式配置比自动探测可靠
# （自动退到 "." 会把 build/ 里的旧测试副本也收进负样本池 —— 脏）。
PROJECT_TESTDIRS = {"chaosmail/python-fs": "fs"}


@dataclass
class RecheckRow:
    """一条负样本的复核结果。

    verdict 三分类（与 C1 指标口径一致）：
        stable_ok   K 遍全过 = "K 遍内无翻转"（≠ 证明稳定）
        env_fail    K 遍**全挂** = 环境类失败（如断网下 DNS 不可用）——
                    不是 flaky 也不是干净负样本，**排除并单独报数**
        noisy       有过有挂 = N1 混入真 flaky（噪声，全实验最关心的）
    """

    project: str
    sha: str
    test_name: str
    runs: int  # 请求遍数
    passed: int
    failed: int
    timeouts: int
    notfound: int
    flipped: bool  # 有过有挂 = N1 噪声
    all_pass: bool  # K 遍全过
    env_fail: bool  # K 遍全挂（0 有效通过）= 环境类失败，排除
    seconds: float
    note: str = ""


def load_project_shas(path: Path) -> dict[str, tuple[str, set[str]]]:
    """真值表 → {project: (sha, recorded_tests)}。只取 eval_eligible=true。"""
    out: dict[str, tuple[str, set[str]]] = {}
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            if r["eval_eligible"].strip().lower() != "true":
                continue
            proj = r["project"]
            sha, recorded = out.get(proj, (r["sha"], set()))
            if r["sha"] != sha:
                raise ValueError(f"{proj} 真值表里有多个 SHA：{sha} vs {r['sha']}")
            recorded.add(r["test_name"])
            out[proj] = (sha, recorded)
    return out


def recheck_one(
    runner: DockerRunner,
    src_dir: Path,
    deps_dir: Path,
    test_name: str,
    runs: int,
    per_run_timeout: int,
    raw_log_dir: Path | None = None,
) -> RecheckRow:
    """一条负样本：换进程重跑 runs 遍，按三分类定性。

    stable_ok（K 遍全过）/ env_fail（K 遍全挂 = 环境类失败，排除并
    单独报数，与 C1 指标口径同规则）/ noisy（有过有挂 = N1 噪声）。
    """
    t0 = time.monotonic()
    outcome = runner.replay(
        src_dir, deps_dir, test_name, runs=runs, per_run_timeout=per_run_timeout
    )
    seconds = round(time.monotonic() - t0, 1)
    if outcome.docker_error:
        return RecheckRow(
            project="",
            sha="",
            test_name=test_name,
            runs=0,
            passed=0,
            failed=0,
            timeouts=0,
            notfound=0,
            flipped=False,
            all_pass=False,
            env_fail=False,
            seconds=seconds,
            note=f"DOCKER: {outcome.docker_error[:120]}",
        )
    counts = {"pass": 0, "fail": 0, "timeout": 0, "notfound": 0}
    for verdict in outcome.sequence:
        counts[verdict] = counts.get(verdict, 0) + 1
    env_fail = counts["pass"] == 0
    flipped = counts["pass"] > 0 and counts["fail"] > 0
    if flipped and raw_log_dir is not None and outcome.first_failure_log:
        raw_log_dir.mkdir(parents=True, exist_ok=True)
        safe = test_name.replace("/", "_").replace("::", "__")
        (raw_log_dir / f"{safe}.log").write_text(
            outcome.first_failure_log, encoding="utf-8"
        )
    return RecheckRow(
        project="",
        sha="",
        test_name=test_name,
        runs=len(outcome.sequence),
        passed=counts["pass"],
        failed=counts["fail"],
        timeouts=counts["timeout"],
        notfound=counts["notfound"],
        flipped=flipped,
        all_pass=not flipped and not env_fail,
        env_fail=env_fail,
        seconds=seconds,
        note=("首个失败日志已落 raw" if flipped else ""),
    )


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description="N3 负样本复核（换进程 K 遍）")
    parser.add_argument("--src-root", required=True, help="WSL 视角的源码根目录")
    parser.add_argument("--deps-root", required=True, help="依赖缓存根目录")
    parser.add_argument("--docker-bin", default="docker")
    parser.add_argument("--per-project", type=int, default=8)
    parser.add_argument("--runs", type=int, default=30)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--per-run-timeout", type=int, default=120)
    parser.add_argument("--only", default=None, help="只跑这个项目（试点用）")
    parser.add_argument(
        "--out",
        default=str(Path(__file__).parent / "negatives_recheck.csv"),
    )
    args = parser.parse_args(argv)

    projects = load_project_shas(GROUND_TRUTH)
    if args.only:
        projects = {k: v for k, v in projects.items() if k == args.only}
        if not projects:
            log.error("--only=%s 不在真值表里", args.only)
            return 2

    runner = DockerRunner()
    _parts = tuple(s for s in args.docker_bin.split(";") if s)
    runner.docker_bin = _parts[0] if len(_parts) == 1 else _parts
    src_root = Path(args.src_root)
    deps_root = Path(args.deps_root)
    raw_dir = Path(__file__).resolve().parent / "raw" / "negatives-recheck"

    rows: list[RecheckRow] = []
    for proj, (sha, recorded) in sorted(projects.items()):
        src = src_root / f"{proj.split('/')[1]}-{sha}"
        if not src.exists():
            # Windows 视角路径（.exists() 对 POSIX 路径恒 False）——交给容器报错
            log.warning("源码目录缺失（按 WSL 视角核对）：%s", src)
        deps = deps_root / proj.split("/")[1]
        log.info("=== %s @ %s ===", proj, sha[:8])

        # setup（两段式第一段：装工具链 + 项目依赖，带网；缓存后秒过）
        setup = runner.setup(src, deps)
        if not setup.tools_ok:
            log.error(
                "  环境没建起来：%s —— 项目跳过（如实报告）", setup.log_tail[-100:]
            )
            rows.append(
                RecheckRow(
                    proj,
                    sha,
                    "(setup)",
                    0,
                    0,
                    0,
                    0,
                    0,
                    False,
                    False,
                    False,
                    0.0,
                    "SETUP_FAIL",
                )
            )
            continue

        # collect → filter_recorded → 抽样（与 C1 同一套 N1 逻辑，扩抽样量）
        coll = runner.collect(src, deps, testdir=PROJECT_TESTDIRS.get(proj, "tests"))
        if not coll.ok:
            log.error("  收集失败：%s —— 项目跳过（如实报告）", coll.reason)
            rows.append(
                RecheckRow(
                    proj,
                    sha,
                    "(collect)",
                    0,
                    0,
                    0,
                    0,
                    0,
                    False,
                    False,
                    False,
                    0.0,
                    f"COLLECT_{coll.reason}",
                )
            )
            continue
        candidates = filter_recorded(coll.tests, recorded)
        picked = select_negatives(candidates, args.per_project, args.seed)
        log.info(
            "  候选 %d 条（已剔 IDoFT 记录 %d 条），抽 %d 条",
            len(candidates),
            len(coll.tests) - len(candidates),
            len(picked),
        )

        for node in picked:
            row = recheck_one(
                runner,
                src,
                deps,
                node,
                args.runs,
                args.per_run_timeout,
                raw_log_dir=raw_dir,
            )
            row.project, row.sha = proj, sha
            rows.append(row)
            if row.flipped:
                mark = "⚠️ 翻转（N1 噪声）"
            elif row.env_fail:
                mark = "🚫 全挂（环境类失败，排除）"
            else:
                mark = "✅ 全过"
            log.info(
                "  %s %s（%d/%d 过，%.0fs）%s",
                mark,
                node,
                row.passed,
                row.runs,
                row.seconds,
                row.note,
            )

    # 汇总（口径：三分类，见模块 docstring 与 RecheckRow 注释）
    valid = [r for r in rows if r.runs > 0]
    flipped = [r for r in valid if r.flipped]
    env_fail = [r for r in valid if r.env_fail]
    all_pass = [r for r in valid if r.all_pass]
    clean = [r for r in valid if not r.env_fail]  # 排除环境失败后的分母
    collected_fail = [r for r in rows if r.note.startswith("COLLECT_")]
    summary = {
        "schema_version": "2.0",
        "generated_utc": datetime.now(UTC).isoformat(),
        "runs_per_sample": args.runs,
        "seed": args.seed,
        "projects": len(projects),
        "samples_total": len(rows),
        "samples_valid": len(valid),
        "collect_failures": len(collected_fail),
        "verdict_breakdown": {
            "stable_ok（K 遍全过）": len(all_pass),
            "env_fail（K 遍全挂，排除并单独报数）": len(env_fail),
            "noisy（有过有挂 = N1 噪声）": len(flipped),
        },
        "env_fail_tests": [
            {"project": r.project, "test": r.test_name} for r in env_fail
        ],
        "flipped_tests": [{"project": r.project, "test": r.test_name} for r in flipped],
        "noise_rate_upper_bound": (
            round(len(flipped) / len(clean), 4) if clean else None
        ),
        "confirm_stable_ratio_lower_bound": (
            round(len(all_pass) / len(clean), 4) if clean else None
        ),
        "catch_prob_5pct": round(CATCH_PROB_5PCT, 4),
        "disclaimer": (
            "30 遍全过 ≠ 证明稳定（95% 置信需 ~170 遍）；全过比例 = N1 纯度下界。"
            "env_fail（全挂）不是 flaky 也不是噪声 —— 是断网等环境约束下的"
            "预期失败（C1 指标口径：环境类失败排除并单独报数）"
        ),
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=list(asdict(rows[0]).keys()) if rows else ["project"],
        )
        writer.writeheader()
        writer.writerows(asdict(r) for r in rows)
    json_path = out_path.with_suffix(".json")
    json_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    log.info("")
    log.info("=== 复核汇总（N3，K=%d，seed=%d）===", args.runs, args.seed)
    log.info(
        "有效样本：%d 条（收集失败 %d 条项目级跳过）", len(valid), len(collected_fail)
    )
    log.info(
        "三分类：全过 %d · 环境全挂（排除）%d · 噪声翻转 %d",
        len(all_pass),
        len(env_fail),
        len(flipped),
    )
    log.info(
        "排除环境失败后（分母 %d）：噪声率上界 %s · 确认稳定比例（下界）%s",
        len(clean),
        summary["noise_rate_upper_bound"],
        summary["confirm_stable_ratio_lower_bound"],
    )
    if env_fail:
        log.info("环境失败明细（预期：断网下依赖 DNS/socket 的测试）：")
        for r in env_fail:
            log.info("  - %s %s", r.project, r.test_name)
    log.info(
        "口径：对失败率 ≥5%% 的测试，K=30 抓到概率 ≈ %.1f%%；弱翻转抓不全",
        CATCH_PROB_5PCT * 100,
    )
    log.info("明细：%s / %s", out_path, json_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
