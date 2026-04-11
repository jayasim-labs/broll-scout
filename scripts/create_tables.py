#!/usr/bin/env python3
"""Create DynamoDB tables for B-Roll Scout."""
import os
import sys

import boto3
from botocore.exceptions import ClientError

PREFIX = os.environ.get("DYNAMODB_TABLE_PREFIX", "broll_")
REGION = os.environ.get("AWS_REGION", "us-east-1")


def create_table(client, name: str, key_schema: list, attr_defs: list, gsi: list | None = None):
    full_name = f"{PREFIX}{name}"
    kwargs = {
        "TableName": full_name,
        "KeySchema": key_schema,
        "AttributeDefinitions": attr_defs,
        "BillingMode": "PAY_PER_REQUEST",
    }
    if gsi:
        kwargs["GlobalSecondaryIndexes"] = gsi
    try:
        client.create_table(**kwargs)
        print(f"  Created {full_name}")
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceInUseException":
            print(f"  {full_name} already exists")
        else:
            raise


def main():
    client = boto3.client("dynamodb", region_name=REGION)
    print(f"Creating DynamoDB tables with prefix '{PREFIX}' in {REGION}...\n")

    create_table(client, "jobs",
        key_schema=[{"AttributeName": "job_id", "KeyType": "HASH"}],
        attr_defs=[{"AttributeName": "job_id", "AttributeType": "S"}],
    )

    create_table(client, "segments",
        key_schema=[
            {"AttributeName": "job_id", "KeyType": "HASH"},
            {"AttributeName": "segment_id", "KeyType": "RANGE"},
        ],
        attr_defs=[
            {"AttributeName": "job_id", "AttributeType": "S"},
            {"AttributeName": "segment_id", "AttributeType": "S"},
        ],
    )

    create_table(client, "results",
        key_schema=[
            {"AttributeName": "job_id", "KeyType": "HASH"},
            {"AttributeName": "result_id", "KeyType": "RANGE"},
        ],
        attr_defs=[
            {"AttributeName": "job_id", "AttributeType": "S"},
            {"AttributeName": "result_id", "AttributeType": "S"},
            {"AttributeName": "video_id", "AttributeType": "S"},
        ],
        gsi=[{
            "IndexName": "video_id-index",
            "KeySchema": [{"AttributeName": "video_id", "KeyType": "HASH"}],
            "Projection": {"ProjectionType": "ALL"},
        }],
    )

    create_table(client, "transcripts",
        key_schema=[{"AttributeName": "video_id", "KeyType": "HASH"}],
        attr_defs=[
            {"AttributeName": "video_id", "AttributeType": "S"},
            {"AttributeName": "transcript_source", "AttributeType": "S"},
        ],
        gsi=[{
            "IndexName": "transcript_source-index",
            "KeySchema": [{"AttributeName": "transcript_source", "KeyType": "HASH"}],
            "Projection": {"ProjectionType": "ALL"},
        }],
    )

    create_table(client, "feedback",
        key_schema=[{"AttributeName": "result_id", "KeyType": "HASH"}],
        attr_defs=[{"AttributeName": "result_id", "AttributeType": "S"}],
    )

    create_table(client, "settings",
        key_schema=[{"AttributeName": "setting_key", "KeyType": "HASH"}],
        attr_defs=[{"AttributeName": "setting_key", "AttributeType": "S"}],
    )

    create_table(client, "channel_cache",
        key_schema=[{"AttributeName": "channel_id", "KeyType": "HASH"}],
        attr_defs=[{"AttributeName": "channel_id", "AttributeType": "S"}],
    )

    create_table(client, "projects",
        key_schema=[{"AttributeName": "project_id", "KeyType": "HASH"}],
        attr_defs=[{"AttributeName": "project_id", "AttributeType": "S"}],
    )

    create_table(client, "usage",
        key_schema=[{"AttributeName": "period", "KeyType": "HASH"}],
        attr_defs=[{"AttributeName": "period", "AttributeType": "S"}],
    )

    create_table(client, "search_cache",
        key_schema=[{"AttributeName": "cache_key", "KeyType": "HASH"}],
        attr_defs=[{"AttributeName": "cache_key", "AttributeType": "S"}],
    )

    # Enable TTL on search_cache so DynamoDB auto-deletes expired items
    full_cache_name = f"{PREFIX}search_cache"
    try:
        client.update_time_to_live(
            TableName=full_cache_name,
            TimeToLiveSpecification={"Enabled": True, "AttributeName": "expires_at"},
        )
        print(f"  Enabled TTL on {full_cache_name} (attribute: expires_at)")
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code == "ValidationException" and "already enabled" in str(e).lower():
            print(f"  TTL already enabled on {full_cache_name}")
        else:
            print(f"  Warning: Could not enable TTL on {full_cache_name}: {e}")

    print("\nAll tables created successfully!")


if __name__ == "__main__":
    main()
