FROM public.ecr.aws/lambda/python:3.12

RUN dnf -y install git wget tar xz
# Static Build of ffmpeg (open source - go through it if concerned!)
RUN wget https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz && tar xvf ffmpeg-release-amd64-static.tar.xz && mv ffmpeg-*-amd64-static/ffmpeg /usr/bin/ffmpeg && rm -Rf ffmpeg*
RUN pip install --no-cache-dir setuptools-rust
RUN pip install --no-cache-dir git+https://github.com/openai/whisper.git 
RUN whisper --model_dir /usr/local --model medium audio >> /dev/null 2>&1; exit 0

COPY app.py ${LAMBDA_TASK_ROOT}

# Set the CMD to your handler (could also be done as a parameter override outside of the Dockerfile)
CMD [ "app.handler" ]
