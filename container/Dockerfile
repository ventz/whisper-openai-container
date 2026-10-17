# AWS Lambda Python base image, pinned by multi-arch digest (amd64 + arm64).
# Bump with: docker buildx imagetools inspect public.ecr.aws/lambda/python:3.14
FROM public.ecr.aws/lambda/python:3.14@sha256:27483b5d8a13e85d0f8207706759a30d6a7c89e60aa9d48ec926b445bdc518d2

# Model baked into the image; one of the names in models.lock.json.
ARG WHISPER_MODEL=medium
# true adds the CUDA 12 / cuDNN 9 libraries for running on an NVIDIA GPU (amd64 only).
ARG GPU=false

COPY requirements.txt requirements-gpu.txt ./
RUN pip install --no-cache-dir --require-hashes --only-binary :all: -r requirements.txt \
    && if [ "$GPU" = "true" ]; then \
         [ "$(uname -m)" = x86_64 ] || { echo "GPU=true needs linux/amd64" >&2; exit 1; }; \
         pip install --no-cache-dir --require-hashes --only-binary :all: -r requirements-gpu.txt; \
       fi \
    && rm requirements*.txt

COPY fetch_model.py models.lock.json /opt/build/
RUN python /opt/build/fetch_model.py "$WHISPER_MODEL" /opt/whisper-model \
    && rm -rf /opt/build

ENV WHISPER_MODEL=/opt/whisper-model \
    HF_HUB_OFFLINE=1 \
    HOME=/tmp \
    LD_LIBRARY_PATH=${LD_LIBRARY_PATH}:/var/lang/lib/python3.14/site-packages/nvidia/cublas/lib:/var/lang/lib/python3.14/site-packages/nvidia/cudnn/lib

COPY transcriber.py app.py ${LAMBDA_TASK_ROOT}/
COPY --chmod=755 transcribe /usr/local/bin/transcribe

# Lambda runs functions as its own unprivileged user; this keeps standalone
# `docker run` (CLI and local emulator) off root too.
USER 10001

CMD [ "app.handler" ]
