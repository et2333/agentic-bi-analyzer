import json
from typing import Any

import pandas as pd
from langchain_core.tools import tool

from core.dataset_store import get_dataset, update_dataset
from tools.tool_trace import trace_tool_call


_NUMERIC_MISSING_TOKENS = {"", "-", "nan", "none", "null", "na", "n/a"}


def _pick_text(payload: dict[str, Any], *keys: str) -> str:
    for key in keys:
        raw = payload.get(key)
        if isinstance(raw, str):
            text = raw.strip()
            if text:
                return text
    return ""


def _normalize_conversion_aliases(conversion: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(conversion)
    column = _pick_text(conversion, "column", "column_name")
    target_type = _pick_text(
        conversion, "target_type", "new_type", "to", "conversion_type"
    ).lower()
    mapping = conversion.get("map_values") or conversion.get("mapping")
    if isinstance(mapping, dict) and mapping:
        # Manager models commonly express a binary mapping as new_type="int".
        # The explicit mapping is the stronger signal and must win over that alias.
        target_type = "binary_map"
        normalized["map_values"] = mapping
    elif target_type in {"int", "integer", "float", "number"}:
        target_type = "numeric"
    if target_type == "categorical":
        target_type = "category"

    if column:
        normalized["column"] = column
    if target_type:
        normalized["target_type"] = target_type
    return normalized


def _clean_numeric_series(series: pd.Series) -> pd.Series:
    cleaned = (
        series.astype(str)
        .str.strip()
        .str.replace("(", "", regex=False)
        .str.replace(")", "", regex=False)
        .str.replace("$", "", regex=False)
        .str.replace(",", "", regex=False)
        .str.strip()
    )
    # A standalone "-" indicates no data and is converted to NaN; it cannot be replaced uniformly above, as that would also erase negative signs.
    cleaned = cleaned.replace({"-": None, "": None, "nan": None, "None": None})
    return pd.to_numeric(cleaned, errors="coerce")


def _unexpected_numeric_values(original: pd.Series, converted: pd.Series) -> list[str]:
    failed = original.notna() & converted.isna()
    if not failed.any():
        return []

    normalized = (
        original.astype(str)
        .str.strip()
        .str.replace("(", "", regex=False)
        .str.replace(")", "", regex=False)
        .str.replace("$", "", regex=False)
        .str.replace(",", "", regex=False)
        .str.strip()
        .str.lower()
    )
    unexpected = failed & ~normalized.isin(_NUMERIC_MISSING_TOKENS)
    if not unexpected.any():
        return []
    return [str(value) for value in original.loc[unexpected].drop_duplicates().head(20)]


@tool
def clean_data_types(dataset_id: str, conversions: list[dict[str, Any]] | None = None) -> str:
    """Fix data types; supports numeric, datetime, object, category, and maps."""
    tool_inputs = {"dataset_id": dataset_id, "conversions": conversions or []}
    converted_columns = []
    skipped_columns = []
    try:
        df = get_dataset(dataset_id).copy()
        for conversion in conversions or []:
            if not isinstance(conversion, dict):
                skipped_columns.append(
                    {"column": None, "reason": "conversion_must_be_object", "conversion": conversion}
                )
                continue

            normalized = _normalize_conversion_aliases(conversion)
            column = normalized.get("column")
            target_type = normalized.get("target_type")
            if not isinstance(column, str) or not column:
                skipped_columns.append(
                    {"column": column, "reason": "missing_or_invalid_column", "conversion": conversion}
                )
                continue
            if column not in df.columns:
                skipped_columns.append({"column": column, "reason": "column_not_found"})
                continue
            if target_type == "numeric":
                converted = _clean_numeric_series(df[column])
                unparsed_values = _unexpected_numeric_values(df[column], converted)
                if unparsed_values:
                    skipped_columns.append(
                        {
                            "column": column,
                            "reason": "numeric_conversion_has_unparseable_values",
                            "unparsed_values": unparsed_values,
                        }
                    )
                    continue
                df[column] = converted
            elif target_type == "datetime":
                df[column] = pd.to_datetime(df[column], errors="coerce")
            elif target_type == "object":
                df[column] = df[column].astype("object")
            elif target_type == "category":
                df[column] = df[column].astype("category")
            elif target_type == "binary_map":
                mapping = normalized.get("map_values") or normalized.get("mapping")
                if not isinstance(mapping, dict) or not mapping:
                    skipped_columns.append(
                        {"column": column, "reason": "binary_map_requires_map_values"}
                    )
                    continue
                mapped = df[column].map(mapping)
                unmatched = df.loc[df[column].notna() & mapped.isna(), column].unique().tolist()
                if unmatched:
                    skipped_columns.append(
                        {
                            "column": column,
                            "reason": "binary_map_has_unmapped_values",
                            "unmapped_values": [str(value) for value in unmatched[:20]],
                        }
                    )
                    continue
                df[column] = mapped
            else:
                skipped_columns.append(
                    {"column": column, "reason": f"unsupported_target_type:{target_type}"}
                )
                continue
            converted_columns.append(column)
        update_dataset(dataset_id, df)
        report = {
            "dataset_id": dataset_id,
            "converted_columns": converted_columns,
            "skipped_columns": skipped_columns,
        }
    except Exception as exc:
        report = {"error": {"type": exc.__class__.__name__, "message": str(exc)}}

    output = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    trace_tool_call("clean_data_types", tool_inputs, output)
    return output
