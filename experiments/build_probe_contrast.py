"""单探头对照实验的输入组装（A/B/C 三组对比的 B、C 两组）。

背景：合并双探头输入的首轮归因（68.4%）中 4 条 OD 被判 NIO —— 假设是
"count 表型证据压过顺序成因"。本脚本生成两组单探头输入做对照：
  B 组（--probe order）：只喂顺序探头日志的条目
  C 组（--probe count）：只喂同进程 --count 探头日志的条目
每条输入 = 失败现场 + **中性翻转统计**（白名单提取，见下）。

白名单原则：翻转统计是"化验单"（运行观察），应该给模型；
判定结论与 polluter 归因是"诊断书"，不能给（否则答案印在考卷上）。

用法：uv run python experiments/build_probe_contrast.py
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from build_attribution_inputs import OUT as FULL_INPUTS
from build_attribution_inputs import RAW, strip_comments

ROOT = Path(__file__).resolve().parents[1]

# 中性观察行白名单（2026-10-13 对 34 份日志的注释行全量盘点后定）：
# 这些行只含运行统计/分隔标记，不含类别判定与 polluter 名字
_NEUTRAL_PATTERNS = (
    re.compile(r"^# 验证 ② victim 单独跑"),
    re.compile(r"^# 失败顺序（"),
    re.compile(r"^# \d+ 过 / \d+ 挂"),
    re.compile(r"^# 同进程 --count 探头翻转证据"),
    re.compile(r"^# 首个 FAILED 行："),
    re.compile(r"^# 日志尾部："),
    re.compile(r"^# ---- "),
)


def neutral_stats(text: str) -> str:
    """从证据日志的注释头里抽取中性翻转统计（化验单），剥掉判定与归因。"""
    kept = [
        ln
        for ln in text.splitlines()
        if ln.startswith("#") and any(p.match(ln) for p in _NEUTRAL_PATTERNS)
    ]
    return "\n".join(kept).strip()


def build_group(items: list[dict], probe: str) -> tuple[list[dict], list[str]]:
    group: list[dict] = []
    skipped: list[str] = []
    for it in items:
        owner, repo = it["project"].split("/")
        stem = it["test_name"].replace("::", "_").replace("/", "_")
        matches = sorted(RAW.glob(f"{owner}__{repo}__*{stem}__{probe}.log"))
        if not matches:
            skipped.append(it["test_name"])
            continue
        path = matches[0]
        text = path.read_text(encoding="utf-8")
        body = strip_comments(text)
        stats = neutral_stats(text)
        failure_log = body
        if stats:
            failure_log = f"[翻转统计（实验观察）]\n{stats}\n\n[失败现场]\n{body}"
        group.append({**it, "failure_log": failure_log, "log_files": [path.name]})
    return group, skipped


def main() -> None:
    items = json.loads(FULL_INPUTS.read_text(encoding="utf-8"))
    for probe in ("order", "count"):
        group, skipped = build_group(items, probe)
        out = ROOT / "data" / "processed" / f"attribution_inputs_{probe}.json"
        out.write_text(
            json.dumps(group, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        logging.info(
            "%s 组：%d 条（跳过 %d 条无该探头日志）-> %s",
            probe,
            len(group),
            len(skipped),
            out,
        )
        if skipped:
            logging.info("  跳过：%s", "; ".join(skipped))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    main()
