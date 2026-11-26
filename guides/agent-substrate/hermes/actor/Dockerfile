# Hermes Agent sandbox image for GKE Agent Substrate + llm-d.
#
# Runs one Hermes Agent gateway (`NousResearch/hermes-agent`) per gVisor
# sandbox on :80, keeps per-session SQLite state under /data/.hermes so full
# gVisor memory+rootfs snapshots (`SNAPSHOT_FIDELITY_MEMORY`) preserve multi-
# turn history across pause/resume cycles, and propagates `X-Hermes-Session-Id`
# and `x-llm-d-inference-objective` onto outbound OpenAI API calls to llm-d.
# Pinned by digest for the same reason as HERMES_REF below.
FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/data \
    HERMES_HOME=/data/.hermes \
    API_SERVER_HOST=0.0.0.0 \
    API_SERVER_PORT=80

# Pinned by commit so golden gVisor snapshots remain reproducible across builds.
ARG HERMES_REF=5ffbea81e995aaa3b6b6b5ba81d1f453a6b88ef8
# hadolint ignore=DL3008,DL3013
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && HERMES_NIX_BUILD=1 pip install \
      "hermes-agent[homeassistant] @ git+https://github.com/NousResearch/hermes-agent.git@${HERMES_REF}" \
    && apt-get purge -y git && apt-get autoremove -y && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /data/.hermes

COPY sitecustomize.py /usr/local/lib/python3.13/site-packages/sitecustomize.py
COPY entrypoint.sh /usr/local/bin/hermes-entrypoint
COPY config.yaml.tmpl hermes-persona.md warmup.py /opt/hermes/
RUN chmod +x /usr/local/bin/hermes-entrypoint

WORKDIR /data
EXPOSE 80 8081
ENTRYPOINT ["/usr/local/bin/hermes-entrypoint"]
