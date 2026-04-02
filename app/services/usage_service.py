"""
Aggregates API usage costs across all jobs and stores periodic snapshots.

Periods: "all_time", "YYYY-MM" (monthly), "YYYY-MM-DD" (daily)
"""

import asyncio
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Optional

import boto3
from botocore.exceptions import ClientError

from app.config import get_settings
from app.utils.cost_tracker import PRICING

logger = logging.getLogger(__name__)


def _dec(v: Any) -> float:
    if isinstance(v, Decimal):
        return float(v)
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


class UsageService:
    def __init__(self):
        settings = get_settings()
        self.dynamodb = boto3.resource(
            "dynamodb",
            region_name=settings.aws_region,
            aws_access_key_id=settings.aws_access_key_id or None,
            aws_secret_access_key=settings.aws_secret_access_key or None,
        )
        self.prefix = settings.dynamodb_table_prefix

    def _table(self, name: str):
        return self.dynamodb.Table(f"{self.prefix}{name}")

    async def _run(self, fn, *args, **kwargs):
        loop = asyncio.get_event_loop()
        from functools import partial
        return await loop.run_in_executor(None, partial(fn, *args, **kwargs))

    async def recalculate(self) -> Dict[str, Any]:
        """Scan all jobs table, aggregate costs, store snapshots."""
        try:
            resp = await self._run(
                self._table("jobs").scan,
                ProjectionExpression="job_id, #st, created_at, api_costs",
                ExpressionAttributeNames={"#st": "status"},
            )
            items = resp.get("Items", [])
        except ClientError:
            logger.exception("Failed to scan jobs for usage")
            return {}

        totals = _empty_totals()
        monthly: Dict[str, Dict] = {}
        daily: Dict[str, Dict] = {}
        job_count = 0

        for item in items:
            costs = item.get("api_costs")
            if not costs or not isinstance(costs, dict):
                continue
            job_count += 1
            _accumulate(totals, costs)

            created = item.get("created_at", "")
            if len(created) >= 10:
                m = created[:7]
                d = created[:10]
                if m not in monthly:
                    monthly[m] = _empty_totals()
                if d not in daily:
                    daily[d] = _empty_totals()
                _accumulate(monthly[m], costs)
                _accumulate(daily[d], costs)

        totals["job_count"] = job_count
        totals["last_calculated"] = datetime.now(timezone.utc).isoformat()

        await self._store_period("all_time", totals)
        for m, data in monthly.items():
            data["last_calculated"] = totals["last_calculated"]
            await self._store_period(m, data)
        for d, data in daily.items():
            data["last_calculated"] = totals["last_calculated"]
            await self._store_period(d, data)

        logger.info("Usage recalculated: %d jobs, $%.4f total", job_count, totals["estimated_cost_usd"])
        return totals

    async def get_usage(self, period: str = "all_time") -> Optional[Dict[str, Any]]:
        try:
            resp = await self._run(
                self._table("usage").get_item,
                Key={"period": period},
            )
            item = resp.get("Item")
            if not item:
                return None
            return {k: _dec(v) if isinstance(v, Decimal) else v for k, v in item.items()}
        except ClientError:
            logger.exception("Failed to get usage for period %s", period)
            return None

    async def get_all_usage(self) -> Dict[str, Any]:
        """Return all_time + current month + today, plus AWS infra costs."""
        now = datetime.now(timezone.utc)
        month_key = now.strftime("%Y-%m")
        day_key = now.strftime("%Y-%m-%d")

        all_time = await self.get_usage("all_time") or _empty_totals()
        current_month = await self.get_usage(month_key) or _empty_totals()
        today = await self.get_usage(day_key) or _empty_totals()

        aws_monthly = float(PRICING.get("aws_monthly_total", 18.06))
        days_this_month = now.day
        aws_cost_mtd = round(aws_monthly * days_this_month / 30, 2)
        aws_cost_today = round(aws_monthly / 30, 2)

        return {
            "all_time": all_time,
            "current_month": current_month,
            "today": today,
            "pricing": PRICING,
            "aws_cost": {
                "monthly_estimate": aws_monthly,
                "mtd": aws_cost_mtd,
                "today": aws_cost_today,
                "breakdown": {
                    "ec2_t3_small": float(PRICING.get("ec2_t3_small_monthly", 16.56)),
                    "dynamodb": float(PRICING.get("dynamodb_monthly_estimate", 1.00)),
                    "route53": float(PRICING.get("route53_monthly", 0.50)),
                },
            },
        }

    async def _store_period(self, period: str, data: Dict) -> None:
        try:
            item = {"period": period}
            for k, v in data.items():
                if isinstance(v, float):
                    item[k] = Decimal(str(round(v, 6)))
                else:
                    item[k] = v
            await self._run(self._table("usage").put_item, Item=item)
        except ClientError:
            logger.exception("Failed to store usage for period %s", period)


def _empty_totals() -> Dict[str, Any]:
    return {
        "openai_calls": 0,
        "openai_mini_calls": 0,
        "openai_input_tokens": 0,
        "openai_output_tokens": 0,
        "gpt4o_input_tokens": 0,
        "gpt4o_output_tokens": 0,
        "gpt4o_mini_input_tokens": 0,
        "gpt4o_mini_output_tokens": 0,
        "whisper_minutes": 0.0,
        "whisper_calls": 0,
        "youtube_api_units": 0,
        "google_cse_calls": 0,
        "gemini_calls": 0,
        "ytdlp_searches": 0,
        "ytdlp_detail_lookups": 0,
        "estimated_cost_usd": 0.0,
        "job_count": 0,
    }


_INT_KEYS = [
    "openai_calls", "openai_mini_calls", "openai_input_tokens",
    "openai_output_tokens", "gpt4o_input_tokens", "gpt4o_output_tokens",
    "gpt4o_mini_input_tokens", "gpt4o_mini_output_tokens",
    "whisper_calls", "youtube_api_units",
    "google_cse_calls", "gemini_calls", "ytdlp_searches",
    "ytdlp_detail_lookups", "job_count",
]
_FLOAT_KEYS = ["whisper_minutes", "estimated_cost_usd"]


def _accumulate(totals: Dict, costs: Dict) -> None:
    for key in _INT_KEYS:
        totals[key] = int(totals.get(key, 0)) + int(_dec(costs.get(key, 0)))
    for key in _FLOAT_KEYS:
        totals[key] = float(totals.get(key, 0.0)) + _dec(costs.get(key, 0))


_service: Optional[UsageService] = None


def get_usage_service() -> UsageService:
    global _service
    if _service is None:
        _service = UsageService()
    return _service
