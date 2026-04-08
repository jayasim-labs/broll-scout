#!/usr/bin/env python3
"""
Write min_video_duration_sec to DynamoDB to match app.config.DEFAULTS.

  # Only upgrade rows that still have the old 30s value (safe)
  python scripts/sync_min_video_duration_dynamo.py

  # Always set to current code default (overwrites any stored value)
  python scripts/sync_min_video_duration_dynamo.py --force

Requires AWS credentials / region (same as the API).
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def _main() -> None:
    from app.config import DEFAULTS
    from app.services.settings_service import get_settings_service

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force",
        action="store_true",
        help=f"Always set min_video_duration_sec to {DEFAULTS['min_video_duration_sec']} (overwrites existing)",
    )
    args = parser.parse_args()

    svc = get_settings_service()
    target = int(DEFAULTS["min_video_duration_sec"])

    if args.force:
        ok = await svc.update_setting(
            "min_video_duration_sec", target, editor_id="cli_sync_force",
        )
        print("update_setting:", "ok" if ok else "failed", "->", target)
        sys.exit(0 if ok else 1)

    changed = await svc.migrate_min_video_duration_to_current_default(force=False)
    if changed:
        print(f"Migrated legacy 30 -> {target}")
    else:
        print(
            f"No migration applied (stored value is not legacy 30, or key missing). "
            f"Current API default when unset: {target}s. Use --force to overwrite.",
        )


if __name__ == "__main__":
    asyncio.run(_main())
