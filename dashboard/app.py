"""FlakyTest-Hunter 看板（TODOLIST 第 5 周 DoD：本地能启动，≥3 个视图）。

大白话：把仓库里已经算出来的数字（C1 检测三件套、C2 归因三组、真值表）
摆到一个网页上，面试时可以现场打开讲 —— 每个数字都带着自己的基线，
和仓库里的 JSON/CSV 一字不差（数据源就是 experiments/ 下的产物文件）。

启动（可选依赖组 dashboard，不污染核心依赖）：
    uv sync --extra dashboard
    uv run streamlit run dashboard/app.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
EXP = ROOT / "experiments"


def _load_json(name: str) -> dict:
    return json.loads((EXP / name).read_text(encoding="utf-8"))


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:.1f}%"


@st.cache_data
def load_all() -> tuple[dict, dict, dict, dict, pd.DataFrame]:
    combined = _load_json("metrics_combined.json")
    attribution = _load_json("metrics_attribution.json")
    attribution_count = _load_json("metrics_attribution_count.json")
    attribution_order = _load_json("metrics_attribution_order.json")
    gt = pd.read_csv(ROOT / "experiments" / "ground_truth_phase_a.csv")
    return combined, attribution, attribution_count, attribution_order, gt


st.set_page_config(page_title="FlakyTest-Hunter 看板", page_icon="🐉", layout="wide")
st.title("🐉 FlakyTest-Hunter · 质量门禁看板")
st.caption(
    "数据源 = experiments/ 产物文件（metrics_*.json / ground_truth_phase_a.csv），"
    "每个数字都带基线 —— 与 README 口径一致"
)

combined, attr, attr_count, attr_order, gt = load_all()

# ------------------------------------------------------------- 视图 1：检测
st.header("1️⃣ Flaky 检测（C1 · 双模式合并口径）")
c1, c2, c3, c4 = st.columns(4)
c1.metric("TP（真 flaky 抓到）", combined["confusion_matrix"]["tp"])
c2.metric("FP（误报）", combined["confusion_matrix"]["fp"])
c3.metric("FN（漏报）", combined["confusion_matrix"]["fn"])
c4.metric("TN（负样本放行）", combined["confusion_matrix"]["tn"])

st.subheader("指标 vs always-flaky 基线（全报警的平凡策略）")
base = combined["baselines"]["always_flaky"]
metrics_df = pd.DataFrame(
    {
        "指标": ["精确率（下界）", "Recall@IDoFT", "F1"],
        "本工具": [combined["precision"], combined["recall_at_idoft"], combined["f1"]],
        "基线": [base["precision"], base["recall"], base["f1"]],
    }
).set_index("指标")
st.bar_chart(metrics_df)
cm = combined["confusion_matrix"]
st.caption(
    f"{cm['tp'] + cm['fn']} 条正样本 / 7 项目 + {cm['fp'] + cm['tn']} 条负样本"
    f"（2026-10-13 真值口径审计修正后，见 groundtruth-audit）；"
    f"重跑 {combined['runs']} 遍换进程 + "
    f"{combined['alone_runs']} 遍单独跑 × {combined['orders']} 个顺序。"
    "精确率是**下界**：IDoFT 漏记的真 flaky 只能落进 FP 列（负样本 N1 口径的噪声）"
)

st.subheader("按类别的召回")
by_cat = pd.DataFrame(
    {
        "类别": list(combined["by_category"].keys()),
        "样本数": [v["n"] for v in combined["by_category"].values()],
        "检出": [v["detected"] for v in combined["by_category"].values()],
        "召回": [v["recall"] for v in combined["by_category"].values()],
    }
).set_index("类别")
st.dataframe(by_cat)

# ------------------------------------------------------------- 视图 2：归因
st.header("2️⃣ LLM 失败归因（C2 · glm-5.3-flash 温度 0 · 19 条）")
st.caption(
    f"prompt {attr['prompt_version']} · 解析失败率 {_pct(attr['parse_failure_rate'])}"
    " · 单探头对照实验确认：输入协议是归因评测的第一变量"
)

rows = []
for label, m in (
    ("合并输入（主口径）", attr),
    ("仅 count 探头输入", attr_count),
    ("仅 order 探头输入", attr_order),
):
    rows.append(
        {
            "输入组": label,
            "六类准确率": m["accuracy"],
            "多数类基线": m["baselines"]["eval_majority"],
            "OD 二分类": m.get("od_binary_accuracy"),
            "OD 二分类基线": m["baselines"]["eval_od_binary"],
        }
    )
attr_df = pd.DataFrame(rows).set_index("输入组")
st.bar_chart(attr_df)
st.dataframe(attr_df.map(_pct))
st.warning(
    "**负结果如实报告**：六类细判 68.4% 仅比多数类基线 63.2% 高 1 条；"
    "**粗粒度有信号** —— OD 二分类 78.9% vs 基线 68.4%（+10.5pp）。"
    "单组输入的数字不可脱离子集基线引用："
    "count 组 11/11 全答 NIO、order 组 12/12 全答 OD-Vic（模型跟随表型主导信号）。"
    "详见 docs/negative-result.md"
)

per_class = pd.DataFrame(attr["per_class"]).T
per_class["support"] = per_class["support"].astype(int)
st.subheader("六类逐类指标（support=1 的 OD 类不具统计意义）")
st.dataframe(per_class.map(lambda x: _pct(x) if isinstance(x, float) else x))

# ------------------------------------------------------------- 视图 3：真值表
st.header("3️⃣ 真值表（阶段 A · IDoFT 复现结果）")
c1, c2 = st.columns(2)
proj_filter = c1.multiselect(
    "按项目筛选", sorted(gt["project"].unique()), default=sorted(gt["project"].unique())
)
cat_filter = c2.multiselect(
    "按类别筛选",
    sorted(gt["category"].dropna().unique()),
    default=sorted(gt["category"].dropna().unique()),
)
view = gt[gt["project"].isin(proj_filter) & gt["category"].isin(cat_filter)]
st.dataframe(view, use_container_width=True)
eligible = gt[gt["eval_eligible"]]
st.caption(
    f"复现 {len(eligible)} 条 / {eligible['project'].nunique()} 个项目"
    "（门槛 ≥15 条 / ≥3 项目，大幅达标）；剔除行带原因（如 penman SHA 已含修复）"
)

st.divider()
st.caption(
    "🐉 FlakyTest-Hunter · 检测与归因的完整方法论见 README 与 docs/；"
    "负样本复核（N3）与负结果文档见 experiments/ 与 docs/negative-result.md"
)
