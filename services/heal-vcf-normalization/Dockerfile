FROM ubuntu:24.04

RUN apt-get update \
    && apt-get install -y --no-install-recommends bcftools samtools ca-certificates gzip \
    && rm -rf /var/lib/apt/lists/*

ENTRYPOINT ["bcftools"]
