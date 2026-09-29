# Container image for the MAK4I MCP server.
#
# The image is transport- and backend-agnostic: MAK4I_TRANSPORT,
# MAK4I_STORE, MAK4I_CONTROL_PLANE_DB and friends are supplied at run time
# (see docs/DEPLOYMENT.md), not baked in — the same image serves the
# stdio and the Streamable HTTP transports. MAK4I_HOST is deliberately
# left at its loopback default here; a deployment that must accept
# outside connections sets MAK4I_HOST=0.0.0.0 itself (deploy/compose does).
#
# The same image also runs the control-plane migrations
# (`alembic upgrade head`, from WORKDIR) and the operator CLI (`mak4i`).
#
# Base images are pinned to a reviewed version *and* digest (issue #10).
# To update: pick the new version, resolve its digest with
# `docker buildx imagetools inspect <image>:<version>`, rebuild, run the
# test suite and the image scan (.github/workflows/security.yml), and
# follow the remediation thresholds in SECURITY.md.
FROM ghcr.io/astral-sh/uv:0.11.32@sha256:df4cae8f3a96d175e2e5f992e597550000edbe78fdc2594d5cd8de1a217f504c AS uv

FROM python:3.13.15-slim@sha256:7c61056e61ac89e852de05f3dc6fa51a6dd2181797bceed46aa725dd7cb2cd3b

# Kept in step with pyproject.toml by scripts/check_release_consistency.py.
LABEL org.opencontainers.image.title="MAK4I Reference MCP server" \
      org.opencontainers.image.version="0.1.0-rc.4" \
      org.opencontainers.image.source="https://github.com/talvikai/mak4i-reference" \
      org.opencontainers.image.licenses="MIT"

# A pinned uv binary copied from its official image, instead of an
# unpinned `pip install uv`.
COPY --from=uv /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY src/ src/

RUN uv sync --frozen --no-dev --no-editable

COPY alembic.ini ./
COPY migrations/ migrations/

# The base image's system pip (and the packages it vendors) is never used
# at runtime — MAK4I runs from the uv-built /app/.venv — so remove it
# rather than ship and scan it.
RUN python -m pip uninstall --yes pip

# Unprivileged runtime user. /data is the one writable location: mount a
# persistent volume at /data/artifacts for LocalJSONStore.
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin mak4i \
    && mkdir -p /data/artifacts \
    && chown -R mak4i:mak4i /data

ENV PATH="/app/.venv/bin:${PATH}" \
    MAK4I_LOCAL_STORE_DIR=/data/artifacts

USER mak4i

EXPOSE 8080

CMD ["python", "-m", "mak4i.mcp_server"]
