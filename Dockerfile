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
FROM python:3.13-slim

RUN pip install --no-cache-dir uv

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY src/ src/

RUN uv sync --frozen --no-dev --no-editable

COPY alembic.ini ./
COPY migrations/ migrations/

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
