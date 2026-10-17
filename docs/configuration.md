# Configuration

- [Environment variables](#environment-variables)
- [Choosing a model](#choosing-a-model)
- [Limits and Lambda sizing](#limits-and-lambda-sizing)
- [GPU](#gpu)
- [faster-whisper vs. openai-whisper](#faster-whisper-vs-openai-whisper)
- [Updating dependencies](#updating-dependencies)

## Environment variables

The same variables work in the Lambda function and with the `transcribe` CLI (`docker run -e ...`).

| Variable | Default | Meaning |
|---|---|---|
| `WHISPER_LANGUAGE` | *(empty: auto-detect)* | Language code such as `en`, `fr`, `de`. Setting it skips detection and avoids misdetection on short clips. |
| `OUTPUT_FORMATS` | `txt,srt,vtt,json` | Any of `txt`, `srt`, `vtt`, `json`. The JSON has the language, duration and timed segments. |
| `WHISPER_VAD_FILTER` | `true` | Skips silence with Silero VAD before decoding. Faster, and it stops Whisper from inventing text in long silences. |
| `WHISPER_BEAM_SIZE` | `5` | Lower (1) is faster and slightly less accurate. |
| `WHISPER_DEVICE` | `auto` | `auto`, `cpu` or `cuda`. |
| `WHISPER_COMPUTE_TYPE` | `auto` | `auto` is `int8` on CPU and `float16` on GPU. Other values: `int8_float16`, `float32`. |
| `MAX_AUDIO_SECONDS` | `0` (no limit) for the CLI; `3600` in Lambda | Rejects longer recordings, using the duration in the file's header and again while decoding. |
| `WHISPER_MODEL` | `/opt/whisper-model` | Path to the model baked into the image. Change the model with the build argument instead (below). |
| `INPUT_PREFIX` | `uploads/` | Lambda only: uploads under this prefix are transcribed. Everything else is ignored. |
| `OUTPUT_PREFIX` | `transcripts/` | Lambda only: where transcripts are written. Both prefixes must end in `/` and must not contain each other; the function refuses to start otherwise. |
| `ALLOWED_BUCKETS` | **required** | Lambda only: comma-separated bucket names the function accepts events for, or `*` for any. The function refuses to start without it. All three deploy options set it. |
| `EXPECTED_BUCKET_OWNER` | *(empty)* | Lambda only: account ID that must own the bucket, checked by S3 on every call. The stacks set it. |
| `MAX_INPUT_MB` | `1024` | Lambda only: larger objects are rejected before and during the download. |
| `LOG_LEVEL` | `INFO` | Lambda only: Python log level. |

## Choosing a model

The model is downloaded and verified when the image is built, so the function never touches the network for it. Choose it with the `WHISPER_MODEL` build argument:

```bash
make build MODEL=turbo                       # local
make cdk-deploy MODEL=turbo                  # CDK
make tf-deploy MODEL=turbo                   # Terraform
```

| Model | Size | Notes |
|---|---|---|
| `tiny`, `base` | 75–145 MB | Fast, noticeably less accurate. Fine for clear, single-speaker English. |
| `small` | 480 MB | Good speed/accuracy balance on CPU. |
| `medium` | 1.5 GB | Default. Accurate across languages. |
| `turbo` | 1.6 GB | large-v3-turbo: close to large-v3 accuracy at a fraction of its cost. |
| `large-v3` | 3.1 GB | Most accurate, slowest. Usually needs a GPU to be practical. |

Measured locally on a 5.4-minute English interview (Apple M3, 12 CPU cores, arm64 image, int8): `small` 160 s, `turbo` 224 s, `medium` about 430 s. `turbo` and `medium` produced equally accurate transcripts; `turbo` also caught a few words `medium` dropped. Lambda's x86_64 vCPUs will differ, so time your own recordings.

Each model is pinned to a Hugging Face commit and the SHA-256 of every file in [`container/models.lock.json`](../container/models.lock.json). The build fails if any file doesn't match. To add a model, add an entry with its repository, commit and file hashes. `turbo` comes from the `dropbox-dash` Hugging Face organization: the `mobiuslabsgmbh` repository that faster-whisper references for that model now redirects there.

## Limits and Lambda sizing

- **Time:** Lambda stops after 15 minutes. CPU speed scales with memory, so the stacks use the maximum, 10,240 MB (6 vCPUs). Because billing is per GB-second, a faster function costs about the same per recording. If your recordings regularly run long, use `small` or `turbo`, or run the container on ECS/Fargate or a GPU host instead.
- **Cold starts:** the first request on a new execution environment loads the model, which takes several seconds on top of pulling the image. Later requests reuse it.
- **How long a recording fits:** Lambda has no GPU, so a recording has to transcribe within 15 minutes on 6 vCPUs. Measure with your own audio before relying on long recordings. `MAX_AUDIO_SECONDS` rejects recordings that are too long before any transcription is paid for.
- **Storage:** uploads are downloaded to `/tmp` (4 GB in the stacks) and deleted after each one. `MAX_INPUT_MB` is checked against the size S3 reports and again while streaming.
- **Failures:** bad media, oversized files and timeouts are not retried. The event goes to the failure SQS queue so you can inspect it.
- **Concurrency:** all three deploy options reserve 5 concurrent executions, which caps how many recordings run at once, and so the cost an uploader can cause. Change it with `reservedConcurrency` (CDK) or `reserved_concurrency` (Terraform). Accounts with a total limit of 10 (common for new accounts) can't reserve any: the deploy fails with "decreases account's UnreservedConcurrentExecution below its minimum value". Set it to `""` (CDK) or `-1` (Terraform), or request a higher limit.
- **Retries:** failed invocations aren't retried. S3 can still occasionally deliver an event twice, and a later re-upload of the same key overwrites its transcripts.

## GPU

faster-whisper runs on NVIDIA GPUs through CUDA 12 and cuDNN 9, which is far faster than CPU for the larger models. Lambda has no GPUs, so this applies to the standalone container on a GPU host:

```bash
make build GPU=true PLATFORM=linux/amd64
make transcribe GPU=true FILE=meeting.m4a      # adds --gpus all
```

`GPU=true` adds NVIDIA's cuBLAS and cuDNN Python wheels (about 1 GB more). The host needs an NVIDIA driver and the NVIDIA Container Toolkit. `WHISPER_DEVICE=auto` picks the GPU when one is visible and falls back to CPU otherwise.

## faster-whisper vs. openai-whisper

Version 2 switched from [openai-whisper](https://github.com/openai/whisper) to [faster-whisper](https://github.com/SYSTRAN/faster-whisper). Both run the same Whisper models; faster-whisper uses the CTranslate2 inference engine instead of PyTorch.

| | openai-whisper (v1) | faster-whisper (v2) |
|---|---|---|
| Speed on CPU | Baseline | About 4x faster (int8) |
| Memory | Baseline | About half |
| Image size | Several GB of PyTorch and CUDA libraries, plus the model | About 380 MB of Python packages plus the model (1.75 GB compressed with `medium`) |
| ffmpeg | Separate static binary, downloaded at build | Bundled in PyAV; no separate binary |
| Accuracy | Reference | Same models; int8 differences are negligible in practice |
| Extras | | Built-in VAD silence filtering, word timestamps, batched GPU inference |
| GPU | PyTorch CUDA | CUDA 12 + cuDNN 9 (`GPU=true`) |
| Trade-offs | | A conversion step sits between OpenAI's weights and the model (pinned and hashed here); CTranslate2 is less flexible for research work such as custom decoding hooks or fine-tuning |

## Updating dependencies

Everything is pinned: the base image by digest, Python packages by hash, and models by commit and hash. To move to the newest versions:

```bash
cd container
uv pip compile -U requirements.in --universal --generate-hashes --python-version 3.14 -o requirements.txt
uv pip compile -U requirements-gpu.in --generate-hashes --python-version 3.14 \
    --python-platform x86_64-manylinux_2_28 -o requirements-gpu.txt
docker buildx imagetools inspect public.ecr.aws/lambda/python:3.14   # new digest for the Dockerfile FROM line
```

Then rebuild and run `make test` and `make transcribe` before deploying.
