FROM ubuntu:noble
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg python3 python3-brotli fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY labelcast /app/labelcast
# No default label on purpose: without a mounted /conf/label.conf the container
# exits at startup instead of streaming video under an example classification.
# Mount the config directory: -v $PWD/conf:/conf

ENTRYPOINT ["python3", "-m", "labelcast"]
CMD ["-c", "/conf/label.conf"]
