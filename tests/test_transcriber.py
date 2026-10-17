import json
import random
import wave
from pathlib import Path

import numpy as np
import pytest

import transcriber


def write_wav(path, seconds, rate=44100):
    t = np.linspace(0, seconds, int(rate * seconds), endpoint=False)
    samples = (np.sin(2 * np.pi * 440 * t) * 10000).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.tobytes())


def test_decode_resamples_to_16k_mono(tmp_path):
    path = tmp_path / "tone.wav"
    write_wav(path, 2)
    audio = transcriber.decode_audio(path)
    assert audio.dtype == np.float32
    assert abs(audio.size - 2 * transcriber.SAMPLE_RATE) < 200
    assert 0 < np.abs(audio).max() <= 1


def test_decode_enforces_max_duration(tmp_path):
    path = tmp_path / "tone.wav"
    write_wav(path, 3)
    with pytest.raises(transcriber.InputError, match="MAX_AUDIO_SECONDS"):
        transcriber.decode_audio(path, max_seconds=1)


def test_decode_rejects_long_audio_from_header(tmp_path, monkeypatch):
    path = tmp_path / "tone.wav"
    write_wav(path, 3)
    # Fails on the container's declared duration, before decoding a single frame.
    monkeypatch.setattr(transcriber.av.AudioResampler, "resample", None, raising=False)
    with pytest.raises(transcriber.InputError, match="MAX_AUDIO_SECONDS"):
        transcriber.decode_audio(path, max_seconds=2)


def test_decode_caps_samples_when_header_understates(tmp_path, monkeypatch):
    path = tmp_path / "tone.wav"
    write_wav(path, 3)
    # Make the header claim ~0 s, so only the running sample count can catch it.
    monkeypatch.setattr(transcriber.av, "time_base", 10**12)
    with pytest.raises(transcriber.InputError, match="MAX_AUDIO_SECONDS"):
        transcriber.decode_audio(path, max_seconds=2)


def test_decode_tolerates_truncated_file(tmp_path):
    full = tmp_path / "full.wav"
    write_wav(full, 2)
    cut = tmp_path / "cut.wav"
    cut.write_bytes(full.read_bytes()[: full.stat().st_size // 2])
    audio = transcriber.decode_audio(cut)
    assert 0 < audio.size < 2 * transcriber.SAMPLE_RATE


SAMPLE = Path(__file__).resolve().parents[1] / "samples" / "recording.m4a"


@pytest.mark.parametrize("seed", range(40))
def test_decode_skips_corrupt_packets(tmp_path, seed):
    # 64 random bytes in the middle of a real m4a; some seeds hit packets that
    # FFmpeg reports as InvalidData, others as "not yet implemented".
    rng = random.Random(seed)
    data = bytearray(SAMPLE.read_bytes())
    offset = rng.randrange(len(data) // 4, 3 * len(data) // 4)
    data[offset : offset + 64] = bytes(rng.randrange(256) for _ in range(64))
    path = tmp_path / "damaged.m4a"
    path.write_bytes(data)
    assert transcriber.decode_audio(path).size > 5 * transcriber.SAMPLE_RATE


def test_decode_rejects_playlists(tmp_path):
    path = tmp_path / "evil.m3u8"
    path.write_text("#EXTM3U\n#EXTINF:10,\nhttp://127.0.0.1:9/segment.ts\n#EXT-X-ENDLIST\n")
    with pytest.raises(transcriber.InputError):
        transcriber.decode_audio(path)


def test_decode_rejects_non_media(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("hello")
    with pytest.raises(transcriber.InputError):
        transcriber.decode_audio(path)


def test_decode_rejects_openable_non_audio_containers(tmp_path):
    path = tmp_path / "subtitles.srt"
    path.write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n")
    with pytest.raises(transcriber.InputError, match="unsupported or unreadable media"):
        transcriber.decode_audio(path)


def test_parse_formats():
    assert transcriber.parse_formats(" txt, SRT ") == ["txt", "srt"]
    with pytest.raises(ValueError):
        transcriber.parse_formats("txt,docx")
    with pytest.raises(ValueError):
        transcriber.parse_formats("")


TRANSCRIPT = transcriber.Transcript(
    language="en",
    language_probability=0.99,
    duration=3725.5,
    segments=[
        {"start": 0.0, "end": 1.5, "text": "Hello."},
        {"start": 3723.25, "end": 3725.5, "text": "Goodbye."},
    ],
)


def test_render_txt():
    assert transcriber.render(TRANSCRIPT, "txt") == "Hello.\nGoodbye.\n"


def test_render_srt():
    assert transcriber.render(TRANSCRIPT, "srt") == (
        "1\n00:00:00,000 --> 00:00:01,500\nHello.\n\n2\n01:02:03,250 --> 01:02:05,500\nGoodbye.\n"
    )


def test_render_vtt():
    out = transcriber.render(TRANSCRIPT, "vtt")
    assert out.startswith("WEBVTT\n\n00:00:00.000 --> 00:00:01.500\nHello.\n")


def test_render_json():
    data = json.loads(transcriber.render(TRANSCRIPT, "json"))
    assert data["language"] == "en"
    assert data["segments"][1]["text"] == "Goodbye."


def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("WHISPER_LANGUAGE", "fr")
    monkeypatch.setenv("OUTPUT_FORMATS", "srt")
    monkeypatch.setenv("WHISPER_VAD_FILTER", "false")
    s = transcriber.Settings.from_env()
    assert (s.language, s.formats, s.vad_filter) == ("fr", ["srt"], False)


def test_write_output_refuses_symlinks(tmp_path):
    target = tmp_path / "precious.txt"
    target.write_text("keep me")
    link = tmp_path / "talk.wav.txt"
    link.symlink_to(target)
    with pytest.raises(OSError):
        transcriber.write_output(link, "overwritten")
    assert target.read_text() == "keep me"
    transcriber.write_output(tmp_path / "new.txt", "ok")
    assert (tmp_path / "new.txt").read_text() == "ok"
