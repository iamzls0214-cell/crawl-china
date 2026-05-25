"""解析从 stats.customs.gov.cn 手动导出的 CSV 文件.

实测文件格式 (GBK 编码):
    数据年月,商品编码,商品名称,贸易伙伴编码,贸易伙伴名称,
    第一数量,第一计量单位,第二数量,第二计量单位,美元

海关国别代码 (stats.customs.gov.cn 体系):
    141 = 越南, 136 = 泰国, 112 = 印度尼西亚
    122 = 马来西亚 (非目标国, 自动过滤)

Usage:
    python main.py --parse-customs ~/Downloads/
"""

import csv
import os
import re
import sys
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Optional

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from crawlers.comtrade import HS_CHAPTER_TO_SECTION, HS_CHAPTER_NAMES

# stats.customs.gov.cn 国别代码
CUSTOMS_COUNTRY_CODE = {
    "141": "越南",
    "136": "泰国",
    "112": "印度尼西亚",
}

# 仅处理目标国家
TARGET_COUNTRIES = {"越南", "泰国", "印度尼西亚"}

# 文件编码备选列表
ENCODINGS = ["gbk", "gb2312", "gb18030", "utf-8-sig", "utf-8"]


class CustomsCSVParser:
    def __init__(self, input_dir: str, output_dir: str):
        self.input_dir = input_dir
        self.output_dir = output_dir
        self.records: List[dict] = []
        self._warnings: List[str] = []
        self._fallback_period = ""
        self._has_rmb = False

    def parse_all(self) -> List[dict]:
        csv_files = sorted(
            f for f in os.listdir(self.input_dir)
            if f.lower().endswith(".csv")
        )
        if not csv_files:
            print(f"错误: {self.input_dir} 中没有 CSV 文件")
            return []

        print(f"找到 {len(csv_files)} 个文件")
        for fname in csv_files:
            path = os.path.join(self.input_dir, fname)
            file_records = self._parse_file(path)
            self.records.extend(file_records)

            # 统计该文件的目标国家
            countries = set(r["country"] for r in file_records)
            months = set(r["period"] for r in file_records)
            print(f"  {fname}: {len(file_records)} 条 → {', '.join(countries)} {', '.join(sorted(months))}")

        for w in self._warnings:
            print(f"  ⚠ {w}")

        return self.records

    def _parse_file(self, filepath: str) -> List[dict]:
        encoding = self._detect_encoding(filepath)
        records = []
        self._has_rmb = False

        # 从文件名推断月份: 印尼3-4.csv → 印尼, 3, 4
        fname = os.path.basename(filepath)
        self._fallback_period = self._parse_period_from_filename(fname)

        with open(filepath, "r", encoding=encoding) as f:
            reader = csv.DictReader(f)
            if reader.fieldnames is None:
                return []

            for row in reader:
                record = self._extract(row)
                if record:
                    records.append(record)

        if self._has_rmb:
            self._warnings.append(f"{fname}: 币制为人民币，已按7.2汇率转为美元")

        return records

    def _parse_period_from_filename(self, fname: str) -> str:
        """从文件名推断月份，如 '印尼3-4月.csv' → '202603' (取第一个月)."""
        # 匹配数字
        nums = re.findall(r"(\d+)\s*[-_~到至]?\s*(\d+)?\s*月?", fname)
        if not nums:
            return ""
        m = nums[0]
        month = int(m[0]) if m[0] else 0
        if 1 <= month <= 12:
            year = datetime.now().year
            return f"{year}{month:02d}"
        return ""

    def _extract(self, row: dict) -> Optional[dict]:
        # HS 编码
        hs8 = (row.get("商品编码") or "").strip()
        if not re.match(r"^\d{6,10}$", hs8):
            return None

        hs2 = hs8[:2]
        try:
            chapter = int(hs2)
        except (ValueError, TypeError):
            return None

        # 国家
        partner_code = (row.get("贸易伙伴编码") or "").strip()
        partner_name = (row.get("贸易伙伴名称") or "").strip()
        country = CUSTOMS_COUNTRY_CODE.get(partner_code)
        if not country:
            return None
        if country not in TARGET_COUNTRIES:
            return None

        # 月份 (兼容 "数据年月" 和 "月份" 两种列名)
        period = (
            (row.get("数据年月") or "").strip() or
            (row.get("月份") or "").strip()
        )
        period = re.sub(r"[^\d]", "", period)
        if len(period) != 6:
            # 没有月份列 (未勾选"分月展示")，尝试从文件名推断
            period = self._fallback_period
        if not period or len(period) != 6:
            return None

        # 金额: 优先美元，其次人民币 (人民币需转换: 汇率约 7.2)
        value_str = (
            (row.get("美元") or "").strip() or
            (row.get("美元值") or "").strip()
        )
        is_rmb = False
        if not value_str:
            value_str = (row.get("人民币") or "").strip()
            is_rmb = bool(value_str)
        value_str = value_str.replace(",", "").replace(" ", "")
        try:
            value = float(value_str)
        except (ValueError, TypeError):
            return None
        if value == 0:
            return None

        if is_rmb:
            value = value / 7.2  # 人民币 → 美元 (近似)
            self._has_rmb = True

        # 数量和单位 (第一数量优先)
        qty_str = (row.get("第一数量") or "").strip().replace(",", "")
        unit = (row.get("第一计量单位") or "").strip()
        try:
            quantity = float(qty_str)
        except (ValueError, TypeError):
            quantity = 0.0

        section = HS_CHAPTER_TO_SECTION.get(chapter, "其他")
        chapter_name = HS_CHAPTER_NAMES.get(chapter, f"HS{hs2}")

        return {
            "country": country,
            "period": period,
            "commodity": chapter_name,
            "hs_code": hs2,
            "hs_code_full": hs8,
            "hs_section": section,
            "value_usd": value,
            "quantity": quantity,
            "unit": unit,
        }

    def _detect_encoding(self, filepath: str) -> str:
        for enc in ENCODINGS:
            try:
                with open(filepath, "r", encoding=enc) as f:
                    f.readline()
                return enc
            except (UnicodeDecodeError, UnicodeError):
                continue
        return "latin-1"

    def write_outputs(self):
        if not self.records:
            print("无记录可写入")
            return

        os.makedirs(self.output_dir, exist_ok=True)
        today = datetime.now().strftime("%Y%m%d")

        detail_path = os.path.join(self.output_dir, f"customs_detail_{today}.csv")
        self._write_detail(detail_path)

        summary_path = os.path.join(self.output_dir, f"customs_summary_{today}.csv")
        self._write_summary(summary_path)

        print(f"\n输出文件:")
        print(f"  明细: {detail_path}")
        print(f"  汇总: {summary_path}")
        self._print_preview()

    def _write_detail(self, path: str):
        fields = [
            "source", "country", "direction", "period", "commodity",
            "hs_code", "hs_code_full", "hs_section", "value_usd",
            "quantity", "unit",
        ]
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            for r in self.records:
                r_copy = dict(r)
                r_copy["source"] = "customs_manual"
                r_copy["direction"] = "export"
                writer.writerow(r_copy)
        print(f"  明细: {len(self.records)} 条")

    def _write_summary(self, path: str):
        agg = defaultdict(lambda: [0.0, 0.0, ""])
        for r in self.records:
            key = (r["hs_section"], r["country"], r["period"])
            agg[key][0] += r["value_usd"]
            agg[key][1] += r.get("quantity", 0)
            agg[key][2] = r.get("unit", "")

        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(["hs_section", "country", "period", "value_usd", "quantity", "unit"])
            for (section, country, period), (val, qty, unit) in sorted(agg.items()):
                writer.writerow([section, country, period, f"{val:.2f}", f"{qty:.2f}", unit])
        print(f"  汇总: {len(agg)} 组")

    def _print_preview(self):
        agg = defaultdict(lambda: defaultdict(float))
        months_covered = set()
        for r in self.records:
            agg[r["hs_section"]][r["country"]] += r["value_usd"]
            months_covered.add(r["period"])

        print(f"\n=== 海关各大类出口额 ({', '.join(sorted(months_covered))}) ===")
        for section in sorted(agg, key=lambda s: -sum(agg[s].values())):
            total = sum(agg[section].values())
            parts = " | ".join(f"{c}: ${v/1e6:.0f}M" for c, v in sorted(agg[section].items()))
            print(f"  {section}: ${total/1e9:.2f}B  ({parts})")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="解析 stats.customs.gov.cn 导出的海关 CSV")
    parser.add_argument("--input", "-i", required=True, help="CSV 文件所在目录")
    parser.add_argument("--output", "-o", default="data/customs_manual/", help="输出目录")
    args = parser.parse_args()

    p = CustomsCSVParser(args.input, args.output)
    p.parse_all()
    p.write_outputs()


if __name__ == "__main__":
    main()
