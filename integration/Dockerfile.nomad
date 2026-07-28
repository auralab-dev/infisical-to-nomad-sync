FROM debian:bookworm-slim

ARG NOMAD_VERSION=2.0.4
ARG TARGETARCH

RUN apt-get update \
  && apt-get install --no-install-recommends --yes \
       ca-certificates \
       curl \
       python3 \
       python3-requests \
       unzip \
  && case "${TARGETARCH:-amd64}" in \
       amd64) NOMAD_ARCH=amd64 ;; \
       arm64) NOMAD_ARCH=arm64 ;; \
       *) echo "Unsupported architecture: ${TARGETARCH}" >&2; exit 1 ;; \
     esac \
  && curl --fail --silent --show-error --location \
       "https://releases.hashicorp.com/nomad/${NOMAD_VERSION}/nomad_${NOMAD_VERSION}_linux_${NOMAD_ARCH}.zip" \
       --output /tmp/nomad.zip \
  && unzip -q /tmp/nomad.zip -d /usr/local/bin \
  && chmod +x /usr/local/bin/nomad \
  && rm /tmp/nomad.zip \
  && apt-get clean \
  && rm -rf /var/lib/apt/lists/*

COPY integration/nomad.hcl /etc/nomad.d/nomad.hcl

ENTRYPOINT ["nomad"]
