from __future__ import annotations

from statistics import mean
from typing import Any


MEASURE_TYPE_LABELS = {
    "fasting": "空腹",
    "post_meal": "餐后",
    "before_sleep": "睡前",
}

DEFAULT_TARGET_RANGES = {
    "fasting": {"min": 3.9, "max": 7.0},
    "before_sleep": {"min": 3.9, "max": 7.0},
    "post_meal": {"min": 3.9, "max": 10.0},
}


def _format_float(value: float) -> str:
    return f"{value:.1f}".rstrip("0").rstrip(".")


def _build_trend_text(first_value: float, last_value: float) -> str:
    delta = last_value - first_value
    if abs(delta) < 0.3:
        return "首末记录变化不大，整体较平稳"
    if delta > 0:
        return f"末次记录较首次上升约 {_format_float(delta)} mmol/L"
    return f"末次记录较首次下降约 {_format_float(abs(delta))} mmol/L"


def _get_target_range(
    measure_type: str,
    target_ranges: dict[str, dict[str, float]],
) -> dict[str, float]:
    return target_ranges.get(measure_type) or target_ranges["post_meal"]


def _is_in_range(value: float, target_range: dict[str, float]) -> bool:
    return target_range["min"] <= value <= target_range["max"]


def summarize_glucose_records(
    records: list[dict[str, Any]],
    *,
    days: int = 7,
    target_ranges: dict[str, dict[str, float]] | None = None,
) -> dict[str, Any]:
    ranges = target_ranges or DEFAULT_TARGET_RANGES
    aggregate_target = {
        "min": min(target_range["min"] for target_range in ranges.values()),
        "max": max(target_range["max"] for target_range in ranges.values()),
    }

    if not records:
        return {
            "count": 0,
            "target": aggregate_target,
            "targets": ranges,
            "summary": f"近{days}日暂无血糖记录，暂时无法生成趋势总结。",
        }

    sorted_records = sorted(records, key=lambda record: str(record.get("measure_time") or ""))
    normalized_records = [
        {
            "value": float(record["value"]),
            "measure_type": str(record.get("measure_type") or "post_meal"),
        }
        for record in sorted_records
    ]
    values = [record["value"] for record in normalized_records]
    in_range_count = sum(
        1
        for record in normalized_records
        if _is_in_range(record["value"], _get_target_range(record["measure_type"], ranges))
    )
    high_count = sum(
        1
        for record in normalized_records
        if record["value"] > _get_target_range(record["measure_type"], ranges)["max"]
    )
    low_count = sum(
        1
        for record in normalized_records
        if record["value"] < _get_target_range(record["measure_type"], ranges)["min"]
    )
    average_value = mean(values)
    min_value = min(values)
    max_value = max(values)
    in_range_ratio = round(in_range_count / len(values) * 100)
    trend_text = _build_trend_text(values[0], values[-1])

    type_counts: dict[str, int] = {}
    for record in normalized_records:
        measure_type = record["measure_type"]
        type_counts[measure_type] = type_counts.get(measure_type, 0) + 1

    type_summary = "、".join(
        f"{MEASURE_TYPE_LABELS.get(measure_type, measure_type)} {count} 次"
        for measure_type, count in sorted(type_counts.items())
    )

    summary = (
        f"近{days}日共记录 {len(values)} 次血糖，平均值约 {_format_float(average_value)} mmol/L，"
        f"最低 {_format_float(min_value)} mmol/L，最高 {_format_float(max_value)} mmol/L。"
        f"其中 {in_range_count} 次处于对应测量类型的目标范围内，"
        f"达标占比约 {in_range_ratio}%；高于目标范围 {high_count} 次，低于目标范围 {low_count} 次。"
        f"{trend_text}。记录类型包括：{type_summary}。"
    )

    return {
        "count": len(values),
        "target": aggregate_target,
        "targets": ranges,
        "average": round(average_value, 1),
        "min": min_value,
        "max": max_value,
        "in_range_count": in_range_count,
        "high_count": high_count,
        "low_count": low_count,
        "summary": summary,
    }
