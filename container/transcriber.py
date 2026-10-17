"""Speech-to-text with faster-whisper, shared by the Lambda handler and the CLI.

Run as a CLI inside the container:

    transcribe /data/recording.m4a --formats txt,srt
"""

import argparse
import gc
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

import av
import numpy as np

SAMPLE_RATE = 16000
SUPPORTED_FORMATS = ("txt", "srt", "vtt", "json")

# ffmpeg demuxer names accepted as input. Anything else (playlists such as
# hls, concat scripts, subtitle and image formats) is refused, because some of
# those demuxers make ffmpeg open other files or URLs named inside the upload.
ALLOWED_CONTAINERS = {
    "aac",
    "aiff",
    "amr",
    "asf",
    "avi",
    "caf",
    "flac",
    "matroska",
    "webm",
    "mov",
    "mp4",
    "m4a",
    "3gp",
    "3g2",
    "mj2",
    "mp3",
    "mpegts",
    "ogg",
    "w64",
    "wav",
}


# Audio codecs accepted. Anything else is refused before decoding, which keeps
# rarely used (and rarely fuzzed) FFmpeg decoders away from uploads.
ALLOWED_CODECS = {
    "aac", "aac_latm", "ac3", "alac", "eac3", "flac", "mp2", "mp3", "mp3float",
    "opus", "libopus", "vorbis", "libvorbis", "speex", "gsm", "gsm_ms",
}  # fmt: skip
ALLOWED_CODEC_PREFIXES = ("pcm_", "adpcm_", "amr", "wma")


class InputError(ValueError):
    """The input is not acceptable media (unsupported format, too long, no audio)."""


def env_bool(name, default):
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    model: str = "/opt/whisper-model"
    device: str = "auto"
    compute_type: str = "auto"
    language: str | None = None
    formats: list[str] = field(default_factory=lambda: list(SUPPORTED_FORMATS))
    vad_filter: bool = True
    beam_size: int = 5
    max_audio_seconds: int = 0

    @classmethod
    def from_env(cls):
        return cls(
            model=os.environ.get("WHISPER_MODEL", cls.model),
            device=os.environ.get("WHISPER_DEVICE", cls.device),
            compute_type=os.environ.get("WHISPER_COMPUTE_TYPE", cls.compute_type),
            language=os.environ.get("WHISPER_LANGUAGE") or None,
            formats=parse_formats(os.environ.get("OUTPUT_FORMATS", ",".join(SUPPORTED_FORMATS))),
            vad_filter=env_bool("WHISPER_VAD_FILTER", True),
            beam_size=int(os.environ.get("WHISPER_BEAM_SIZE", cls.beam_size)),
            max_audio_seconds=int(os.environ.get("MAX_AUDIO_SECONDS", cls.max_audio_seconds)),
        )


def parse_formats(value):
    formats = [f.strip().lower() for f in value.split(",") if f.strip()]
    unknown = sorted(set(formats) - set(SUPPORTED_FORMATS))
    if unknown or not formats:
        raise ValueError(f"OUTPUT_FORMATS must be a list of {SUPPORTED_FORMATS}, got {value!r}")
    return formats


def load_model(settings):
    import ctranslate2
    from faster_whisper import WhisperModel

    device = settings.device
    if device == "auto":
        device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
    compute_type = settings.compute_type
    if compute_type == "auto":
        compute_type = "float16" if device == "cuda" else "int8"
    return WhisperModel(
        settings.model,
        device=device,
        compute_type=compute_type,
        cpu_threads=os.cpu_count() or 0,
    )


def decode_audio(path, max_seconds=0):
    """Decode any supported media file to 16 kHz mono float32 samples.

    Mirrors faster_whisper.decode_audio (corrupt packets are skipped, and a
    damaged tail ends the audio instead of failing it), plus demuxer and codec
    allowlists, a file-only protocol whitelist and an optional duration cap.
    """
    max_samples = max_seconds * SAMPLE_RATE if max_seconds else 0
    too_long = InputError(f"audio is longer than MAX_AUDIO_SECONDS={max_seconds}")
    try:
        container = av.open(
            str(path),
            mode="r",
            container_options={
                "protocol_whitelist": "file",
                # Other demuxers may probe the bytes but never read the file.
                "format_whitelist": ",".join(sorted(ALLOWED_CONTAINERS)),
            },
        )
    except av.FFmpegError as e:
        raise InputError(f"unsupported or unreadable media: {e}") from e

    resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
    chunks, total = [], 0

    def keep(frames):
        nonlocal total
        for frame in frames:
            array = frame.to_ndarray().reshape(-1).astype(np.float32) / 32768.0
            total += array.size
            if max_samples and total > max_samples:
                raise too_long
            chunks.append(array)

    with container:
        if not container.streams.audio:
            raise InputError("no audio stream")
        stream = container.streams.audio[0]
        codec = stream.codec_context.name
        if codec not in ALLOWED_CODECS and not codec.startswith(ALLOWED_CODEC_PREFIXES):
            raise InputError(f"unsupported audio codec: {codec}")
        if max_seconds and container.duration and container.duration / av.time_base > max_seconds:
            raise too_long
        packets = container.demux(stream)
        while True:
            try:
                packet = next(packets)
            except StopIteration:
                break
            except av.FFmpegError as e:
                if chunks:
                    break  # damaged tail: keep what decoded so far
                raise InputError(f"cannot read media: {e}") from e
            try:
                frames = packet.decode()
            except av.FFmpegError:
                continue  # corrupt packet: skip it
            for frame in frames:
                keep(resampler.resample(frame))
        keep(resampler.resample(None))
    del resampler
    gc.collect()
    if not chunks:
        raise InputError("no audio decoded")
    return np.concatenate(chunks)


@dataclass
class Transcript:
    language: str
    language_probability: float
    duration: float
    segments: list[dict]


def transcribe(model, audio, settings):
    segments, info = model.transcribe(
        audio,
        language=settings.language,
        beam_size=settings.beam_size,
        vad_filter=settings.vad_filter,
    )
    return Transcript(
        language=info.language,
        language_probability=round(info.language_probability, 4),
        duration=round(info.duration, 3),
        segments=[
            {"start": round(s.start, 3), "end": round(s.end, 3), "text": s.text.strip()} for s in segments
        ],
    )


def timestamp(seconds, separator):
    ms = round(seconds * 1000)
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{separator}{ms:03d}"


def render(transcript, fmt):
    segments = transcript.segments
    if fmt == "txt":
        return "\n".join(s["text"] for s in segments) + "\n"
    if fmt == "srt":
        return "\n".join(
            f"{i}\n{timestamp(s['start'], ',')} --> {timestamp(s['end'], ',')}\n{s['text']}\n"
            for i, s in enumerate(segments, 1)
        )
    if fmt == "vtt":
        cues = "\n".join(
            f"{timestamp(s['start'], '.')} --> {timestamp(s['end'], '.')}\n{s['text']}\n" for s in segments
        )
        return "WEBVTT\n\n" + cues
    if fmt == "json":
        return json.dumps(transcript.__dict__, ensure_ascii=False, indent=2) + "\n"
    raise ValueError(f"unknown format {fmt}")


CONTENT_TYPES = {
    "txt": "text/plain; charset=utf-8",
    "srt": "application/x-subrip; charset=utf-8",
    "vtt": "text/vtt; charset=utf-8",
    "json": "application/json",
}


def write_output(path, text):
    """Write a transcript without following a symlink planted at its name."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="transcribe", description=__doc__.splitlines()[0])
    parser.add_argument("files", nargs="+", type=Path, help="audio or video files")
    parser.add_argument("-o", "--output-dir", type=Path, help="default: next to each input file")
    parser.add_argument("-f", "--formats", help="comma-separated: txt,srt,vtt,json")
    parser.add_argument("-l", "--language", help="language code such as en (default: auto-detect)")
    parser.add_argument("-m", "--model", help="model directory (default: the model baked into the image)")
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    if args.formats:
        settings.formats = parse_formats(args.formats)
    if args.language:
        settings.language = args.language
    if args.model:
        settings.model = args.model

    model = load_model(settings)
    failed = 0
    for path in args.files:
        try:
            transcript = transcribe(model, decode_audio(path, settings.max_audio_seconds), settings)
            out_dir = args.output_dir or path.parent
            out_dir.mkdir(parents=True, exist_ok=True)
            for fmt in settings.formats:
                out = out_dir / f"{path.name}.{fmt}"
                write_output(out, render(transcript, fmt))
                print(out)
        except (InputError, OSError) as e:
            print(f"{path}: {e}", file=sys.stderr)
            failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
