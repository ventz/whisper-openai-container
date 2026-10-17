# Deploy with Terraform

Creates the same resources as the [CDK stack](deploy-cdk.md), plus an ECR repository that expires untagged images beyond the newest ten. Works with Terraform 1.5+ and OpenTofu (`make tf-deploy TERRAFORM=tofu`).

Terraform can't build container images, so a deploy runs in three steps: create the ECR repository, push the image, then apply everything else. `make tf-deploy` does all three, and is safe to re-run after code changes:

```bash
make tf-deploy AWS_REGION=us-east-1
```

## Step by step

```bash
cd infra/terraform
terraform init

# 1. Only the ECR repository
terraform apply -target=aws_ecr_repository.this -target=aws_ecr_lifecycle_policy.this

# 2. Build the image and push it to the repository
cd ../.. && make push AWS_REGION=us-east-1 REPO=whisper && cd infra/terraform

# 3. Bucket, role, queue, function and the S3 trigger
terraform apply
```

The outputs show where to upload (run from `infra/terraform`):

```bash
cd infra/terraform
terraform output upload_to        # s3://whisper-111122223333-us-east-1/uploads/
aws s3 cp ../../samples/recording.m4a "$(terraform output -raw upload_to)recording.m4a"
aws s3 ls "$(terraform output -raw transcripts_at)"
```

## Settings

All variables are in [`infra/terraform/variables.tf`](../infra/terraform/variables.tf). Put them in a `terraform.tfvars` file (ignored by git) or pass `-var`:

```hcl
region               = "us-east-1"
name                 = "whisper"
architecture         = "arm64"     # must match the image: make push ARCH=arm64
language             = "en"
reserved_concurrency = 5
```

The model is chosen at build time: `make tf-deploy MODEL=turbo`. `make tf-deploy` passes `-var` only for the Makefile variables you set on its command line (`AWS_REGION=` → `region`, `ARCH=` → `architecture`, `TAG=` → `image_tag`, and `REPO=` → `name`, which names the function, role, queue and default bucket as well as the repository), so `terraform.tfvars` keeps working. It reads the repository, region, architecture and tag back from Terraform when it pushes.

## State

The configuration has no backend block, so state is stored locally in `infra/terraform/terraform.tfstate` (ignored by git). For shared use, add a `backend "s3"` block.

## Update and remove

After code changes, run `make tf-deploy` again. The function is pinned to the image digest, so a new push always shows up as a change.

`terraform destroy` removes everything. It fails on a non-empty bucket or repository, which protects your recordings; empty them first if you mean it.
