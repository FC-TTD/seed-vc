FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04

# 引入 uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

LABEL maintainer="ttd@server"
LABEL version="dev"
LABEL description="Docker image for seed-vc"


# Install 3rd party apps
ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Asia/Shanghai
RUN apt-get update && \
    apt-get install -y --no-install-recommends python3 python-is-python3 build-essential python3-dev tzdata tini ffmpeg libsox-dev vim parallel aria2 git git-lfs locales && \
    git lfs install && \
    rm -rf /var/lib/apt/lists/*

#RUN locale-gen zh_CN.UTF-8 

# Copy only requirements.txt initially to leverage Docker cache
WORKDIR /app

ENV UV_SYSTEM_PYTHON=1

RUN --mount=type=bind,source=requirements.txt,target=/tmp/requirements.txt \
    uv pip install --no-cache -r /tmp/requirements.txt

RUN uv pip install --no-cache 'ttd_fastapi_utils>=0.3.3' --extra-index-url http://pypi-server/simple/ --trusted-host pypi-server

# Copy the rest of the application
COPY . /app

HEALTHCHECK --interval=30s --timeout=3s --start-period=1m --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:7856/health', timeout=3)" || exit 1

EXPOSE 7856

ENTRYPOINT ["tini", "--"]
CMD [ "python", "api2.py" ]
