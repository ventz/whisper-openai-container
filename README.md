# whisper-openai-container

<p><strong>⭐ If whisper-openai-container is useful to you, please <a href="https://github.com/ventz/whisper-openai-container">star the repo</a>.</strong></p>

<a href="https://github.com/ventz/whisper-openai-container/stargazers"><img src="https://img.shields.io/github/stars/ventz/whisper-openai-container?style=social" alt="GitHub stars"></a>
<a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License: MIT"></a>
<img src="https://img.shields.io/badge/python-3.14-blue.svg" alt="Python 3.14">

Accurate, private speech-to-text with OpenAI's Whisper models, packaged as one container image. Run it on your own machine, or deploy it as an AWS Lambda function that transcribes every recording dropped into an S3 bucket. No per-minute fees, and the audio never leaves your machine or your AWS account.

## Table of Contents

- [Overview](#overview)
- [Quick Install](#quick-install)
- [Features](#features)
- [Deploy to AWS: three ways](#deploy-to-aws-three-ways)
- [Usage](#usage)
- [Documentation](#documentation)
- [Development](#development)
- [License](#license)

## Overview

The image runs [faster-whisper](https://github.com/SYSTRAN/faster-whisper), a reimplementation of Whisper on the CTranslate2 engine that is about four times faster than OpenAI's original code on CPU while using the same models. The model is baked into the image when it is built, so transcription runs fully offline.

The same image works two ways:

- **Locally**, as a command-line tool: point it at a file, get `.txt`, `.srt`, `.vtt` and `.json` next to it.
- **On AWS Lambda**: upload to `s3://your-bucket/uploads/`, and the transcripts appear under `transcripts/`.

## Quick Install

Requires Docker and `make`.

```bash
git clone https://github.com/ventz/whisper-openai-container
cd whisper-openai-container
make build
make transcribe FILE=samples/recording.m4a
cat samples/recording.m4a.txt
```

The first build downloads about 2 GB: the base image, the Python packages and the `medium` model.

## Features

- **Fast on CPU:** int8 inference with silence skipping (VAD). Uses a GPU when one is available ([GPU builds](docs/configuration.md#gpu)).
- **Any audio or video file:** m4a, mp3, wav, flac, ogg, webm, mp4, mov, mkv and more, decoded by the FFmpeg bundled in PyAV.
- **Four output formats:** plain text, SRT and WebVTT subtitles, and JSON with timed segments and the detected language.
- **Any language:** auto-detected, or set with `WHISPER_LANGUAGE`.
- **Choice of model:** `tiny` through `large-v3`, including `turbo`, picked at build time ([models](docs/configuration.md#choosing-a-model)).
- **Event-driven on AWS:** prefix-filtered S3 trigger, least-privilege IAM, capped concurrency, no retries on bad input, and a failure queue ([how it works](docs/how-it-works.md)).
- **Sandboxed locally:** `make transcribe` runs the container with no network, no capabilities and a read-only filesystem.
- **Reproducible and verified builds:** base image pinned by digest, Python packages by hash, and models by commit and SHA-256.
- **amd64 and arm64:** runs natively on Apple Silicon, and on Graviton Lambdas.

## Deploy to AWS: three ways

All three create the same setup: a private bucket, the function, an `uploads/` trigger, a least-privilege role and a failure queue. Pick the tool you already use.

| Option | Best for | Command |
|---|---|---|
| **1. [AWS CDK](docs/deploy-cdk.md)** (recommended) | One command; CDK builds and pushes the image for you | `cd infra/cdk && cdk deploy` |
| **2. [Terraform / OpenTofu](docs/deploy-terraform.md)** | Teams that already manage AWS with Terraform | `make tf-deploy` |
| **3. [AWS CLI, step by step](docs/deploy-aws-cli.md)** | No infrastructure tool, or to see every resource | copy-paste commands |

Then:

```bash
aws s3 cp meeting.m4a s3://YOUR-BUCKET/uploads/meeting.m4a
aws s3 ls s3://YOUR-BUCKET/transcripts/          # meeting.m4a.txt, .srt, .vtt, .json
```

## Usage

```bash
make transcribe FILE="$HOME/Downloads/my interview.mp3"        # all formats, next to the file
make transcribe FILE=talk.m4a FORMATS=srt LANGUAGE=en          # subtitles only, skip detection
make build MODEL=turbo                                         # rebuild with another model
make shell                                                     # shell inside the image
make help                                                      # every target
```

Without `make`:

```bash
docker build -t whisper container
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD:/data" --entrypoint transcribe whisper /data/talk.m4a --formats txt,srt
```

To test the Lambda handler locally against a real bucket, edit the bucket and key in [`events/s3-put.json`](events/s3-put.json), run `make lambda-local AWS_PROFILE=your-profile BUCKET=your-bucket` in one terminal and `make invoke` in another. The emulator only listens on 127.0.0.1 and has no authentication, so stop it when you're done.

## Documentation

- [Configuration](docs/configuration.md): environment variables, models, limits and Lambda sizing, GPU, faster-whisper vs. openai-whisper, updating dependencies
- [How it works](docs/how-it-works.md): the S3 flow and the security design
- Deploy: [CDK](docs/deploy-cdk.md), [Terraform](docs/deploy-terraform.md), [AWS CLI](docs/deploy-aws-cli.md)

## Development

```bash
make test      # unit tests (uv)
make lint      # ruff and terraform fmt
```

## License

[MIT](LICENSE) © Ventz Petkov
