FROM ubuntu:noble
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg python3 python3-brotli \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY labelcast /app/labelcast
# Default config; mount your own directory over /conf to edit it while running.
COPY examples /conf

ENTRYPOINT ["python3", "-m", "labelcast"]
CMD ["-c", "/conf/label.conf"]
