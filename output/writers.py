"""输出模块 - CSV 和 JSON 格式写入."""

import csv
import json
import logging
import os
from datetime import datetime
from typing import List

from crawlers.trade_record import TradeRecord

logger = logging.getLogger(__name__)

CSV_FIELDS = [
    "source", "country", "direction", "period", "commodity",
    "hs_code", "value_usd", "quantity", "unit", "yoy_pct", "data_date",
]


def write_csv(records: List[TradeRecord], output_path: str) -> bool:
    """将 TradeRecord 列表写入 CSV 文件."""
    if not records:
        logger.warning(f"No records to write to {output_path}")
        return False

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    try:
        with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
            writer.writeheader()
            for record in records:
                writer.writerow(record.to_csv_row())

        logger.info(f"Written {len(records)} records to {output_path}")
        return True
    except Exception as e:
        logger.error(f"Failed to write CSV {output_path}: {e}")
        return False


def write_json(records: List[TradeRecord], output_path: str) -> bool:
    """将 TradeRecord 列表写入 JSON 文件."""
    if not records:
        logger.warning(f"No records to write to {output_path}")
        return False

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    try:
        data = {
            "generated": datetime.now().isoformat(),
            "record_count": len(records),
            "records": [r.to_dict() for r in records],
        }
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        logger.info(f"Written {len(records)} records to {output_path}")
        return True
    except Exception as e:
        logger.error(f"Failed to write JSON {output_path}: {e}")
        return False


def write_records(
    records: List[TradeRecord],
    output_dir: str,
    source: str,
    fmt: str = "csv",
) -> dict:
    """统一的记录写入函数.

    Returns:
        {"csv": path, "json": path} 写入的文件路径字典
    """
    today = datetime.now().strftime("%Y%m%d")
    result = {}

    if fmt in ("csv", "both"):
        csv_path = os.path.join(output_dir, source, f"{source}_{today}.csv")
        if write_csv(records, csv_path):
            result["csv"] = csv_path

    if fmt in ("json", "both"):
        json_path = os.path.join(output_dir, source, f"{source}_{today}.json")
        if write_json(records, json_path):
            result["json"] = json_path

    return result
