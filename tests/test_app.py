import importlib
import io
import tempfile
import urllib.parse

import pytest

import transcriber
from test_transcriber import TRANSCRIPT, write_wav


class FakeS3:
    def __init__(self, objects):
        self.objects = objects
        self.puts = {}
        self.calls = []

    def get_object(self, Bucket, Key, **kwargs):
        self.calls.append(("get", Bucket, Key, kwargs))
        data = self.objects[(Bucket, Key)].read_bytes()
        body = io.BytesIO(data)
        body.iter_chunks = lambda size: iter(lambda: body.read(size), b"")
        return {"ContentLength": self.reported_length or len(data), "Body": body}

    reported_length = None

    def put_object(self, Bucket, Key, Body, ContentType, **kwargs):
        self.puts[Key] = (Body, ContentType, kwargs)


@pytest.fixture
def app(monkeypatch, tmp_path):
    """Import app.py with a fake model and fake S3 client."""
    monkeypatch.setattr(transcriber, "load_model", lambda settings: "fake-model")
    monkeypatch.setattr(transcriber, "transcribe", lambda model, audio, settings: TRANSCRIPT)
    monkeypatch.setenv("ALLOWED_BUCKETS", "media")
    monkeypatch.setenv("EXPECTED_BUCKET_OWNER", "111122223333")
    monkeypatch.setenv("OUTPUT_FORMATS", "txt,srt")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.delenv("MAX_AUDIO_SECONDS", raising=False)
    audio = tmp_path / "talk.wav"
    write_wav(audio, 1)
    module = importlib.reload(importlib.import_module("app"))
    module.s3 = FakeS3({("media", "uploads/team a/talk.wav"): audio})
    return module


def event(key, bucket="media", **obj):
    return {
        "Records": [
            {
                "eventSource": "aws:s3",
                "s3": {"bucket": {"name": bucket}, "object": {"key": urllib.parse.quote_plus(key), **obj}},
            }
        ]
    }


def test_writes_transcripts_under_output_prefix(app):
    result = app.handler(event("uploads/team a/talk.wav"), None)
    assert result == {"outputs": ["transcripts/team a/talk.wav.txt", "transcripts/team a/talk.wav.srt"]}
    body, content_type, kwargs = app.s3.puts["transcripts/team a/talk.wav.txt"]
    assert body == b"Hello.\nGoodbye.\n"
    assert content_type.startswith("text/plain")
    assert kwargs == {"ExpectedBucketOwner": "111122223333"}


def test_passes_version_id(app):
    app.handler(event("uploads/team a/talk.wav", versionId="v1"), None)
    get = next(c for c in app.s3.calls if c[0] == "get")
    assert get[3] == {"VersionId": "v1", "ExpectedBucketOwner": "111122223333"}


def test_ignores_keys_outside_input_prefix(app):
    assert app.handler(event("transcripts/talk.wav.txt"), None) == {"outputs": []}
    assert app.s3.calls == []


def test_rejects_unlisted_bucket(app):
    with pytest.raises(PermissionError):
        app.handler(event("uploads/team a/talk.wav", bucket="other"), None)


def test_rejects_oversized_object(app, monkeypatch):
    monkeypatch.setattr(app, "MAX_INPUT_MB", 0)
    with pytest.raises(transcriber.InputError, match="MAX_INPUT_MB"):
        app.handler(event("uploads/team a/talk.wav"), None)
    assert app.s3.puts == {}


def test_size_cap_holds_while_streaming(app, monkeypatch):
    # The object grows past the cap even though S3 reported a small size.
    monkeypatch.setattr(app, "MAX_INPUT_MB", 0)
    app.s3.reported_length = -1
    with pytest.raises(transcriber.InputError, match="MAX_INPUT_MB"):
        app.handler(event("uploads/team a/talk.wav"), None)


def test_wildcard_allows_any_bucket(app, monkeypatch):
    monkeypatch.setattr(app, "ALLOWED_BUCKETS", {"*"})
    app.s3.objects[("other", "uploads/a.wav")] = app.s3.objects[("media", "uploads/team a/talk.wav")]
    assert app.handler(event("uploads/a.wav", bucket="other"), None)["outputs"]


def test_rejects_non_s3_events(app):
    with pytest.raises(ValueError):
        app.handler({"Records": [{"eventSource": "aws:sqs"}]}, None)


@pytest.mark.parametrize(
    "key",
    ["uploads/../../etc/x.wav", "uploads/./x.wav", "uploads//x.wav", "uploads/a\x01b.wav", "uploads/.."],
)
def test_skips_unsafe_keys(app, key):
    assert app.handler(event(key), None) == {"outputs": []}
    assert app.s3.calls == []


def test_dotted_names_are_fine(app):
    app.s3.objects[("media", "uploads/..notes/v1.2.wav")] = app.s3.objects[
        ("media", "uploads/team a/talk.wav")
    ]
    assert app.handler(event("uploads/..notes/v1.2.wav"), None)["outputs"]


def test_rejects_keys_whose_outputs_exceed_s3_limit(app):
    key = "uploads/" + "a" * 1010 + ".wav"  # valid input; output is longer than 1024 bytes
    app.s3.objects[("media", key)] = app.s3.objects[("media", "uploads/team a/talk.wav")]
    with pytest.raises(transcriber.InputError, match="1024"):
        app.handler(event(key), None)
    assert app.s3.calls == []


def test_lambda_caps_duration_by_default(app):
    assert app.settings.max_audio_seconds == 3600


def test_removes_stale_temp_dirs(monkeypatch, tmp_path):
    monkeypatch.setattr(transcriber, "load_model", lambda settings: None)
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("ALLOWED_BUCKETS", "media")
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    stale = tmp_path / "whisper-transcribe-old"
    stale.mkdir()
    (stale / "input").write_bytes(b"x")
    importlib.reload(importlib.import_module("app"))
    assert not stale.exists()


@pytest.mark.parametrize(
    "input_prefix, output_prefix, allowed",
    [
        ("uploads/", "uploads/transcripts/", "media"),  # output inside input
        ("transcripts/in/", "transcripts/", "media"),  # input inside output
        ("uploads/", "uploads/", "media"),
        ("uploads", "transcripts/", "media"),  # no trailing slash
        ("uploads/", "", "media"),
        ("uploads/", "transcripts/", ""),  # ALLOWED_BUCKETS missing
    ],
)
def test_refuses_unsafe_config(monkeypatch, input_prefix, output_prefix, allowed):
    monkeypatch.setattr(transcriber, "load_model", lambda settings: None)
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("INPUT_PREFIX", input_prefix)
    monkeypatch.setenv("OUTPUT_PREFIX", output_prefix)
    monkeypatch.setenv("ALLOWED_BUCKETS", allowed)
    with pytest.raises(RuntimeError):
        importlib.reload(importlib.import_module("app"))
