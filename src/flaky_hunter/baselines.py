"""硬数字基线（规范 §5.3，全部核验过）。

铁律 3：**任何准确率旁边必须有基线**。这些常量由单元测试断言
（tests/test_baselines.py），任何重构把它们弄错会立刻报错。
改动数字必须注明来源与日期。
"""

# 多数类基线：全答 OD-Vic 能得到的准确率（Category 分布实算，评审附录 C）
MAJORITY_CLASS_BASELINE = 0.495

# OD vs 非 OD 二分类基线（Category 分布实算：OD 家族占 72.7%）
OD_BINARY_BASELINE = 0.727

# 要有 95% 把握确认一个通过了测试不 flaky，平均所需重跑次数
# （arXiv 2101.09077，Gruber et al.）—— 0 次翻转 ≠ 稳定（规范 §2.2）
RERUN_FOR_95_CONFIDENCE = 170
