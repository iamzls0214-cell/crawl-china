#!/usr/bin/env python3
"""中国贸易数据爬虫 - 入口脚本.

Usage:
    python main.py                         # 运行全部爬虫
    python main.py --source mofcom         # 仅商务部
    python main.py --source customs        # 仅海关
    python main.py --source trends         # 仅 Google Trends
    python main.py --cron                  # cron 模式 (读 CRON_MODE 环境变量)
    python main.py --output json           # JSON 输出
    python main.py --output both           # CSV + JSON 输出
    python main.py --months 12             # 覆盖查询月数
    python main.py --dry-run               # 验证连通性
    python main.py --refresh-cookies       # 强制刷新 Playwright cookies
"""

import argparse
import fcntl
import logging
import os
import sys
from datetime import datetime
from typing import List

from utils.logging_setup import setup_logging
from crawlers.mofcom import MofcomCrawler
from crawlers.customs import CustomsCrawler
from crawlers.google_trends import GoogleTrendsCrawler
from crawlers.trade_record import TradeRecord
from output.writers import write_records
from utils.cookie_manager import get_customs_cookies

LOCK_FILE = "/tmp/crawl-china.lock"


def parse_args():
    parser = argparse.ArgumentParser(
        description="China Trade Data Crawler - 中国贸易数据爬虫"
    )
    parser.add_argument(
        "--source",
        choices=["all", "mofcom", "customs", "trends"],
        default="all",
        help="选择数据源 (default: all)",
    )
    parser.add_argument(
        "--cron",
        action="store_true",
        help="cron 模式，从 CRON_MODE 环境变量决定运行内容",
    )
    parser.add_argument(
        "--output", "-o",
        choices=["csv", "json", "both"],
        default="csv",
        help="输出格式 (default: csv)",
    )
    parser.add_argument(
        "--months",
        type=int,
        default=None,
        help="查询月数 (覆盖 config.yaml)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="验证连通性，不写入文件",
    )
    parser.add_argument(
        "--refresh-cookies",
        action="store_true",
        help="强制刷新 customs cookies",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="配置文件路径",
    )
    return parser.parse_args()


def acquire_lock() -> bool:
    """获取文件锁，防止并发 cron 运行."""
    try:
        lock_fd = open(LOCK_FILE, "w")
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except (BlockingIOError, OSError):
        return False


def run_crawler(
    crawler,
    source_name: str,
    output_dir: str,
    output_fmt: str,
    dry_run: bool,
) -> List[TradeRecord]:
    """运行单个爬虫并写入结果."""
    logger = logging.getLogger("main")
    logger.info(f"{'='*50}")
    logger.info(f"Running {source_name} crawler...")

    try:
        records = crawler.fetch()
        logger.info(f"{source_name}: fetched {len(records)} records")

        if dry_run:
            if records:
                # 打印前 5 条预览
                for r in records[:5]:
                    logger.info(f"  Preview: {r}")
            return records

        if records:
            result = write_records(records, output_dir, source_name, output_fmt)
            for fmt, path in result.items():
                logger.info(f"  {fmt}: {path}")
        else:
            logger.warning(f"  {source_name}: no records to write")

        return records

    except Exception as e:
        logger.error(f"{source_name} crawler failed: {e}", exc_info=True)
        return []


def main():
    args = parse_args()

    # 确定配置路径
    config_path = args.config
    if config_path is None:
        config_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "config.yaml"
        )

    # 日志设置
    is_cron = args.cron or bool(os.environ.get("CRON_MODE"))
    log_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "crawler.log")
    logger = setup_logging(
        name="main",
        log_file=log_file,
        level="INFO",
    )
    logger.info(f"Crawl-China starting at {datetime.now().isoformat()}")
    logger.info(f"Config: {config_path}")

    # cron 模式下检查锁
    if is_cron and not acquire_lock():
        logger.warning("Another instance is running (lock file exists). Exiting.")
        sys.exit(0)

    # cron 模式: 按 CRON_MODE 决定运行内容
    if is_cron:
        cron_mode = os.environ.get("CRON_MODE", "all")
        logger.info(f"CRON_MODE={cron_mode}")
        if cron_mode == "weekly":
            args.source = "mofcom"  # 运行 mofcom + customs
        elif cron_mode == "trends":
            args.source = "trends"

    # 刷新 cookies
    if args.refresh_cookies:
        logger.info("Force refreshing customs cookies...")
        cookies = get_customs_cookies(force_refresh=True)
        if cookies:
            logger.info(f"Got {len(cookies)} cookies")
        else:
            logger.error("Failed to refresh cookies")
        if args.source == "all" and not args.dry_run:
            return  # 仅刷新 cookie 模式

    # 输出目录
    output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

    all_records = []

    if args.source in ("all", "mofcom"):
        mofcom = MofcomCrawler(config_path)
        if args.months:
            mofcom.months_back = args.months
        records = run_crawler(mofcom, "mofcom", output_dir, args.output, args.dry_run)
        all_records.extend(records)

    if args.source in ("all", "customs"):
        customs = CustomsCrawler(config_path)
        if args.months:
            customs.months_back = args.months
        records = run_crawler(customs, "customs", output_dir, args.output, args.dry_run)
        all_records.extend(records)

    if args.source in ("all", "trends"):
        trends = GoogleTrendsCrawler(config_path)
        records = run_crawler(trends, "google_trends", output_dir, args.output, args.dry_run)
        all_records.extend(records)

    logger.info(f"Done. Total records: {len(all_records)}")

    # 如果没有任何数据，以非零退出（用于 cron 监控）
    if not all_records and not args.dry_run:
        logger.error("All crawlers returned zero records!")
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
