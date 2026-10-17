# Deploy with AWS CDK (recommended)

One command builds the image, pushes it to ECR and creates the whole stack:

- a private, encrypted S3 bucket (TLS only, public access blocked)
- the container-image Lambda function (10 GB memory, 15-minute timeout, 4 GB `/tmp`)
- an S3 trigger on `uploads/` only, so transcripts never re-trigger the function
- an IAM role that can read only `uploads/*`, write only `transcripts/*`, and write only its own log group
- reserved concurrency of 5
- an SQS queue for failed transcriptions (no retries), and a log group with 30-day retention

## Requirements

- AWS credentials for the target account (`aws sts get-caller-identity` works)
- Docker, [uv](https://docs.astral.sh/uv/) and the CDK CLI (`npm install -g aws-cdk`)
- A bootstrapped account and region (once per account/region): `cdk bootstrap`

## Deploy

```bash
cd infra/cdk
BUILDX_NO_DEFAULT_ATTESTATIONS=1 cdk deploy
```

`BUILDX_NO_DEFAULT_ATTESTATIONS=1` stops Docker from attaching build attestations, which turn the image into an index that Lambda rejects. `make cdk-deploy` from the repository root sets it for you.

The outputs show where to upload:

```
Whisper.UploadTo       = s3://whisper-bucket83908e77-abc123/uploads/
Whisper.TranscriptsAt  = s3://whisper-bucket83908e77-abc123/transcripts/
```

## Try it

```bash
BUCKET=$(aws cloudformation describe-stacks --stack-name Whisper \
  --query "Stacks[0].Outputs[?OutputKey=='BucketName'].OutputValue" --output text)
aws s3 cp ../../samples/recording.m4a "s3://$BUCKET/uploads/recording.m4a"
# Wait a minute, then:
aws s3 ls "s3://$BUCKET/transcripts/"
aws s3 cp "s3://$BUCKET/transcripts/recording.m4a.txt" -
```

## Settings

Every setting lives in the `context` block of [`infra/cdk/cdk.json`](../infra/cdk/cdk.json). Edit it, or override per deploy with `-c`:

```bash
cdk deploy -c model=turbo -c architecture=arm64 -c language=en -c reservedConcurrency=10
```

| Context key | Default | Notes |
|---|---|---|
| `stackName` | `Whisper` | Use different names to deploy several copies. |
| `bucketName` | *(generated)* | Set a name to choose it yourself. |
| `model` | `medium` | Any name in `container/models.lock.json`. |
| `architecture` | `x86_64` | `arm64` (Graviton) is about 20% cheaper per GB-second. On an Apple Silicon Mac an `x86_64` image is built under emulation, which is slower to build. |
| `memoryMb`, `timeoutSeconds`, `ephemeralStorageMb` | `10240`, `900`, `4096` | See [Limits and Lambda sizing](configuration.md#limits-and-lambda-sizing). |
| `reservedConcurrency` | `5` | Caps parallel runs and cost. `""` means no cap; see [Concurrency](configuration.md#limits-and-lambda-sizing). |
| `language`, `outputFormats`, `maxInputMb`, `maxAudioSeconds` | | Passed through as [environment variables](configuration.md#environment-variables). |
| `inputPrefix`, `outputPrefix` | `uploads/`, `transcripts/` | |

## Update and remove

Re-run `cdk deploy` (or `make cdk-deploy` from the repository root) after changing code or settings; CDK rebuilds the image only when its files change. `make cdk-deploy` passes `-c` flags only for variables you set on its command line (`MODEL=`, `ARCH=`), so values in `cdk.json` are kept.

`cdk destroy` removes everything except the bucket, which is kept (with your recordings and transcripts) on purpose. Empty and delete it yourself when you no longer need it.
