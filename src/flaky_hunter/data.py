"""IDoFT 数据集解析（铁律 1：`data/raw/` 只读，本模块只读不写）。

外部事实（详见 docs/data-dictionary.md，均已核验）：
- 7 列固定表头：Project URL / SHA Detected / Pytest Test Name /
  Category / Status / PR Link / Notes
- 已观测脏数据（评审实算）：Status 尾随空格 1 条、Category 误落 Status 列 3 条
- Category 允许 `;` 分隔多标签；评测时取**字母序第一个**为主标签（规范 §3.2）

解析原则：**清洗必须留痕** —— 每处修正记进 `warnings`，不悄悄改数据。
"""

from __future__ import annotations

import csv
import io

from pydantic import BaseModel, Field

EXPECTED_COLUMNS = [
    "Project URL",
    "SHA Detected",
    "Pytest Test Name",
    "Category",
    "Status",
    "PR Link",
    "Notes",
]

# Category 官方枚举（照抄 IDoFT README，规范 §5.2，不许自创）
CATEGORY_VALUES = {
    "OD",
    "OD-Brit",
    "OD-Vic",
    "ID",
    "ID-HtF",
    "NIO",
    "NOD",
    "NDOD",
    "NDOI",
    "UD",
    "OSD",
    "TZD",
}

# Status 官方枚举（规范 §5.2；空值合法）
STATUS_VALUES = {
    "Opened",
    "Accepted",
    "InspiredAFix",
    "DeveloperWontFix",
    "DeveloperFixed",
    "Deleted",
    "Rejected",
    "Skipped",
    "MovedOrRenamed",
    "RepoArchived",
    "Deprecated",
    "RepoDeleted",
    "MovedToGradle",
    "FixedOrder",
    "Unmaintained",
    "RepoRenamed",
}


class IdoftParseError(ValueError):
    """表头不符合 IDoFT 7 列约定时抛出 —— 拒绝猜格式。"""


class IdoftRecord(BaseModel):
    """IDoFT 单条记录（清洗后，清洗动作全部留痕在 warnings）。"""

    project_url: str
    project: str  # owner/repo
    sha: str
    test_name: str
    category: list[str] = Field(default_factory=list)  # 多标签已拆分
    primary_category: str = ""  # 字母序第一个（规范 §3.2）
    status: str = ""
    pr_link: str = ""
    notes: str = ""
    warnings: list[str] = Field(default_factory=list)


def _parse_project(project_url: str) -> str:
    """`https://github.com/<owner>/<repo>[/...]` -> `owner/repo`。"""
    parts = [p for p in project_url.split("/") if p]
    # URL 带子路径（/tree/main 等）时取 github.com 后的两段
    if "github.com" in parts:
        rest = parts[parts.index("github.com") + 1 :]
        if len(rest) >= 2:
            return f"{rest[0]}/{rest[1]}"
    if len(parts) >= 2:
        return f"{parts[-2]}/{parts[-1]}"
    raise ValueError(f"无法从 URL 解析 owner/repo: {project_url!r}")


def parse_idoft(text: str) -> list[IdoftRecord]:
    """解析 IDoFT CSV 文本。

    表头不符直接抛 IdoftParseError（不猜格式 —— 猜错了下游所有数字作废）。
    """
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise IdoftParseError("空输入：没有表头")
    if [h.strip() for h in reader.fieldnames] != EXPECTED_COLUMNS:
        raise IdoftParseError(
            f"表头不符：期望 {EXPECTED_COLUMNS}，实际 {reader.fieldnames}"
        )

    records: list[IdoftRecord] = []
    for lineno, raw in enumerate(reader, start=2):  # 行号从 2 起（1 是表头）
        row = {k.strip(): (v or "").strip() for k, v in raw.items() if k is not None}
        extra = raw.get(None)
        warnings: list[str] = []
        if extra and any(extra):
            warnings.append(f"行 {lineno} 有超出 7 列的多余字段，已忽略")

        # 已观测脏数据：Category 误落 Status 列（评审实算 3 条）—— 归位并留痕
        status = row["Status"]
        category_raw = row["Category"]
        if status in CATEGORY_VALUES:
            warnings.append(
                f"行 {lineno}: Status 列出现 Category 值 {status!r}，已归位"
            )
            category_raw = f"{category_raw};{status}" if category_raw else status
            status = ""

        if status and status not in STATUS_VALUES:
            warnings.append(
                f"行 {lineno}: Status 枚举外 {status!r}，原样保留待人工核查"
            )

        cats = [c.strip() for c in category_raw.split(";") if c.strip()]
        records.append(
            IdoftRecord(
                project_url=row["Project URL"],
                project=_parse_project(row["Project URL"]),
                sha=row["SHA Detected"],
                test_name=row["Pytest Test Name"],
                category=cats,
                primary_category=sorted(cats)[0] if cats else "",
                status=status,
                pr_link=row["PR Link"],
                notes=row["Notes"],
                warnings=warnings,
            )
        )
    return records
