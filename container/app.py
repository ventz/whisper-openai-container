"""AWS Lambda handler: transcribe audio uploaded to S3.

An upload to s3://BUCKET/uploads/path/name.m4a produces
s3://BUCKET/transcripts/path/name.m4a.{txt,srt,vtt,json}.
"""

import glob
import json
import logging
import os
import re
import shutil
import tempfile
import urllib.parse

import boto3

import transcriber

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

INPUT_PREFIX = os.environ.get("INPUT_PREFIX", "uploads/")
OUTPUT_PREFIX = os.environ.get("OUTPUT_PREFIX", "transcripts/")
# Required: comma-separated bucket names, or "*" for any bucket.
ALLOWED_BUCKETS = {b.strip() for b in os.environ.get("ALLOWED_BUCKETS", "").split(",") if b.strip()}
EXPECTED_BUCKET_OWNER = os.environ.get("EXPECTED_BUCKET_OWNER", "")
MAX_INPUT_MB = int(os.environ.get("MAX_INPUT_MB", "1024"))


def check_config():
    for name, prefix in (("INPUT_PREFIX", INPUT_PREFIX), ("OUTPUT_PREFIX", OUTPUT_PREFIX)):
        if not prefix.endswith("/") or prefix == "/":
            raise RuntimeError(f"{name} must be a folder-style prefix ending in '/', got {prefix!r}")
    # Transcripts written inside the input prefix (or uploads inside the output
    # prefix) would re-trigger this function.
    if OUTPUT_PREFIX.startswith(INPUT_PREFIX) or INPUT_PREFIX.startswith(OUTPUT_PREFIX):
        raise RuntimeError("INPUT_PREFIX and OUTPUT_PREFIX must not contain each other")
    if not ALLOWED_BUCKETS:
        raise RuntimeError("ALLOWED_BUCKETS is required (bucket names, or * for any bucket)")


check_config()

TMP_PREFIX = "whisper-transcribe-"
# A timed-out invocation never reaches its cleanup, and /tmp outlives it.
for stale in glob.glob(os.path.join(tempfile.gettempdir(), TMP_PREFIX + "*")):
    shutil.rmtree(stale, ignore_errors=True)

# Lambda always caps duration; the CLI default (0) means no limit.
os.environ.setdefault("MAX_AUDIO_SECONDS", "3600")
s3 = boto3.client("s3")
settings = transcriber.Settings.from_env()
# Loaded once per execution environment, so warm invocations reuse it.
model = transcriber.load_model(settings)


def owner_args():
    return {"ExpectedBucketOwner": EXPECTED_BUCKET_OWNER} if EXPECTED_BUCKET_OWNER else {}


UNSAFE_KEY = re.compile(r"[\x00-\x1f\x7f]|(^|/)\.{1,2}(/|$)|//")


def output_key(key):
    return OUTPUT_PREFIX + key[len(INPUT_PREFIX) :]


def download(bucket, key, version, local_path):
    """Stream the object to disk, enforcing MAX_INPUT_MB before and while reading."""
    limit = MAX_INPUT_MB * 1024 * 1024
    version_args = {"VersionId": version} if version else {}
    response = s3.get_object(Bucket=bucket, Key=key, **version_args, **owner_args())
    body = response["Body"]
    try:
        if response["ContentLength"] > limit:
            raise transcriber.InputError(f"object is larger than MAX_INPUT_MB={MAX_INPUT_MB}")
        written = 0
        with open(local_path, "wb") as f:
            for chunk in body.iter_chunks(1 << 20):
                written += len(chunk)
                if written > limit:
                    raise transcriber.InputError(f"object is larger than MAX_INPUT_MB={MAX_INPUT_MB}")
                f.write(chunk)
    finally:
        body.close()


def process_record(record, tmp_dir):
    bucket = record["s3"]["bucket"]["name"]
    key = urllib.parse.unquote_plus(record["s3"]["object"]["key"], encoding="utf-8")
    version = record["s3"]["object"].get("versionId")

    if "*" not in ALLOWED_BUCKETS and bucket not in ALLOWED_BUCKETS:
        raise PermissionError(f"bucket {bucket!r} is not in ALLOWED_BUCKETS")
    if not key.startswith(INPUT_PREFIX) or key.endswith("/"):
        logger.info(json.dumps({"skipped": key, "reason": "outside input prefix"}))
        return None
    if UNSAFE_KEY.search(key):
        # Output keys copy the upload's path; keep "..", "." and control
        # characters out of anything that later syncs transcripts to disk.
        logger.info(json.dumps({"skipped": key, "reason": "unsafe key"}))
        return None

    base = output_key(key)
    longest = max(len(f"{base}.{fmt}".encode()) for fmt in settings.formats)
    if longest > 1024:
        # S3's key limit; fail before paying for the download and transcription.
        raise transcriber.InputError(f"output key would be {longest} bytes; S3 allows 1024")

    # Fixed local name: the S3 key is never used as a filesystem path.
    local_path = os.path.join(tmp_dir, "input")
    download(bucket, key, version, local_path)

    audio = transcriber.decode_audio(local_path, settings.max_audio_seconds)
    os.remove(local_path)
    transcript = transcriber.transcribe(model, audio, settings)

    written = []
    for fmt in settings.formats:
        out_key = f"{base}.{fmt}"
        s3.put_object(
            Bucket=bucket,
            Key=out_key,
            Body=transcriber.render(transcript, fmt).encode("utf-8"),
            ContentType=transcriber.CONTENT_TYPES[fmt],
            **owner_args(),
        )
        written.append(out_key)
    logger.info(
        json.dumps(
            {
                "bucket": bucket,
                "key": key,
                "outputs": written,
                "language": transcript.language,
                "audio_seconds": transcript.duration,
            }
        )
    )
    return written


def handler(event, context):
    results = []
    for record in event.get("Records", []):
        if record.get("eventSource") != "aws:s3":
            raise ValueError("not an S3 event record")
        # A fresh directory per record; removed even when processing fails.
        with tempfile.TemporaryDirectory(prefix=TMP_PREFIX) as tmp_dir:
            written = process_record(record, tmp_dir)
        if written:
            results.extend(written)
    # Exceptions propagate on purpose: for asynchronous S3 invocations that is
    # what marks the invocation failed and routes the event to the
    # on-failure destination.
    return {"outputs": results}
