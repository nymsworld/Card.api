# Uploads part 12's files (update_prices.py's R2_DIR: every Magic and Yu-Gi-Oh! single, too big for git) to the
# public Cloudflare R2 bucket the website and the shop app read them from (R2_PUBLIC, data.kantomisplay.cc).
# Run by daily_pricing.yml after part 12 (or all), before prices_version.json is pushed, so the version never
# names files that aren't there yet. Needs R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY and R2_BUCKET
# (an R2 API token with Object Read & Write on that bucket only), and boto3.
#
# Usage: python publish_r2.py
import os
import sys

import boto3
from botocore.config import Config

R2_DIR = "r2"
# Readers ask with ?v=<changedAt>, so a short cache is enough and a new file shows up within minutes
CACHE_CONTROL = "public, max-age=300"


def main():
    names = []
    for root, _, files in os.walk(R2_DIR):
        for name in files:
            if name.endswith(".json"):
                names.append(os.path.relpath(os.path.join(root, name), R2_DIR).replace(os.sep, "/"))
    if not names:
        sys.exit(f"Nothing in {R2_DIR}/ to publish")
    s3 = boto3.client(
        "s3",
        endpoint_url=f"https://{os.environ['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
        # R2 doesn't take the checksums newer boto3 adds to every upload by default (Cloudflare's R2 docs)
        config=Config(retries={"max_attempts": 5, "mode": "standard"},
                      request_checksum_calculation="when_required", response_checksum_validation="when_required"),
    )
    bucket = os.environ["R2_BUCKET"]
    # The card files first and the big game files last: a reader that sees a new game file finds its card files
    for key in sorted(names, key=lambda n: (not n.startswith("cards/"), n)):
        s3.upload_file(os.path.join(R2_DIR, key), bucket, key,
                       ExtraArgs={"ContentType": "application/json", "CacheControl": CACHE_CONTROL})
    print(f"Uploaded {len(names)} files to R2 ({bucket})")


if __name__ == "__main__":
    main()
