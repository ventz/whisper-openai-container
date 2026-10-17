# Common tasks. Override any variable on the command line, for example:
#   make build MODEL=turbo
#   make transcribe FILE=$HOME/Downloads/meeting.m4a FORMATS=txt,srt

SHELL := /bin/bash

IMAGE     ?= whisper
MODEL     ?= medium
# Local builds default to this machine's architecture.
PLATFORM  ?= $(if $(filter arm64 aarch64,$(shell uname -m)),linux/arm64,linux/amd64)
GPU       ?= false
FILE      ?= samples/recording.m4a
FORMATS   ?= txt,srt,vtt,json
LANGUAGE  ?=
EVENT     ?= events/s3-put.json
# Bucket (and optionally its owner account) the local Lambda emulator may use.
# BUCKET is required with AWS_PROFILE; without credentials it defaults to any.
BUCKET    ?=
OWNER     ?=

# AWS settings for push / deploy targets. ARCH is the Lambda architecture.
ARCH       ?= x86_64
AWS_REGION ?= us-east-1
REPO       ?= whisper
FUNCTION   ?= whisper
TAG        ?= latest

# Passes a variable to cdk/terraform only when it was set on the command line,
# so cdk.json and terraform.tfvars keep working.
given = $(filter command line,$(origin $(1)))

.PHONY: help build shell transcribe lambda-local invoke test lint \
        push update-function cdk-deploy tf-deploy

help: ## Show targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'

build: ## Build the image (MODEL, PLATFORM, GPU)
	docker buildx build --platform $(PLATFORM) --provenance=false --load \
		--build-arg WHISPER_MODEL=$(MODEL) --build-arg GPU=$(GPU) \
		-t $(IMAGE) container

shell: ## Open a shell in the image
	docker run --rm -it --platform $(PLATFORM) -v "$(CURDIR):/data" --entrypoint /bin/bash $(IMAGE)

# FILE reaches the recipe as an environment variable, never as shell source, so
# any file name is safe. The recipe also expands a leading ~ (zsh leaves it
# alone in FILE=~/x). The container gets no network and no capabilities.
transcribe: export TRANSCRIBE_FILE = $(FILE)
transcribe: ## Transcribe FILE locally; outputs land next to it
	@f="$$TRANSCRIBE_FILE"; case "$$f" in "~/"*) f="$$HOME/$${f#\~/}";; esac; \
	[ -f "$$f" ] || { echo "FILE not found: $$f" >&2; exit 1; }; \
	dir="$$(cd "$$(dirname "$$f")" && pwd -P)"; name="$$(basename "$$f")"; \
	set -x; docker run --rm --platform $(PLATFORM) --user "$$(id -u):$$(id -g)" \
		--network none --cap-drop ALL --security-opt no-new-privileges --read-only --tmpfs /tmp \
		$(if $(filter true,$(GPU)),--gpus all,) \
		-v "$$dir:/data" \
		$(if $(LANGUAGE),-e WHISPER_LANGUAGE=$(LANGUAGE),) \
		--entrypoint transcribe $(IMAGE) "/data/$$name" --formats $(FORMATS)

# The emulator has no authentication, so with real credentials it is limited to
# one named bucket. Stop it when you are done.
lambda-local: export ALLOWED_BUCKETS = $(or $(BUCKET),*)
lambda-local: export EXPECTED_BUCKET_OWNER = $(OWNER)
lambda-local: ## Run the Lambda emulator on 127.0.0.1:9000 (AWS_PROFILE=... BUCKET=... to reach real S3)
	$(if $(AWS_PROFILE),$(if $(BUCKET),,$(error BUCKET=<bucket> is required with AWS_PROFILE)))
	docker run --rm -it --platform $(PLATFORM) -p 127.0.0.1:9000:8080 \
		$(if $(AWS_PROFILE),--env-file <(aws configure export-credentials --profile $(AWS_PROFILE) --format env-no-export),) \
		-e AWS_REGION=$(AWS_REGION) -e ALLOWED_BUCKETS -e EXPECTED_BUCKET_OWNER $(IMAGE)

invoke: ## Send EVENT to the local emulator
	curl -s -XPOST http://127.0.0.1:9000/2015-03-31/functions/function/invocations -d @$(EVENT); echo

test: ## Unit tests
	uv run pytest -q

lint: ## ruff, plus terraform fmt
	uv run ruff check . && uv run ruff format --check .
	$(TERRAFORM) -chdir=infra/terraform fmt -check

# ------------------------------------------------------------------ AWS

# Looked up once, and only when an AWS target runs.
ACCOUNT  = $(eval ACCOUNT := $$(shell aws sts get-caller-identity --query Account --output text))$(ACCOUNT)
REGISTRY = $(ACCOUNT).dkr.ecr.$(AWS_REGION).amazonaws.com
LAMBDA_PLATFORM = $(if $(filter arm64,$(ARCH)),linux/arm64,linux/amd64)
# A separate local tag, so pushing never replaces the image `make transcribe` uses.
LAMBDA_IMAGE = $(IMAGE)-lambda-$(ARCH)

push: ## Build for ARCH (CPU only), then push to ECR repository REPO
	$(MAKE) build PLATFORM=$(LAMBDA_PLATFORM) IMAGE=$(LAMBDA_IMAGE) GPU=false
	aws ecr get-login-password --region $(AWS_REGION) | docker login --username AWS --password-stdin $(REGISTRY)
	docker tag $(LAMBDA_IMAGE) $(REGISTRY)/$(REPO):$(TAG)
	docker push $(REGISTRY)/$(REPO):$(TAG)

update-function: push ## AWS CLI path: push, then point FUNCTION at the new image
	aws lambda update-function-code --region $(AWS_REGION) --function-name $(FUNCTION) \
		--image-uri $(REGISTRY)/$(REPO):$(TAG) --query LastUpdateStatus --output text
	aws lambda wait function-updated-v2 --region $(AWS_REGION) --function-name $(FUNCTION)

# No build attestations: Lambda rejects image indexes that carry them.
cdk-deploy: export BUILDX_NO_DEFAULT_ATTESTATIONS = 1
cdk-deploy: ## CDK path: build, push and deploy everything
	cd infra/cdk && $(if $(call given,AWS_REGION),AWS_REGION=$(AWS_REGION)) cdk deploy \
		$(if $(call given,MODEL),-c model=$(MODEL)) $(if $(call given,ARCH),-c architecture=$(ARCH))

# TERRAFORM=tofu for OpenTofu.
TERRAFORM ?= terraform
TF = $(TERRAFORM) -chdir=infra/terraform
TF_VARS = $(if $(call given,AWS_REGION),-var region=$(AWS_REGION)) $(if $(call given,REPO),-var name=$(REPO)) \
	$(if $(call given,ARCH),-var architecture=$(ARCH)) $(if $(call given,TAG),-var image_tag=$(TAG))

tf-deploy: ## Terraform path: ECR repository, push, then everything else
	$(TF) init -input=false
	$(TF) apply $(TF_VARS) -target=aws_ecr_repository.this -target=aws_ecr_lifecycle_policy.this
	repo="$$($(TF) output -raw repository_name)" && region="$$($(TF) output -raw region)" && \
	arch="$$($(TF) output -raw architecture)" && tag="$$($(TF) output -raw image_tag)" && \
	$(MAKE) push REPO="$$repo" AWS_REGION="$$region" ARCH="$$arch" TAG="$$tag"
	$(TF) apply $(TF_VARS)
