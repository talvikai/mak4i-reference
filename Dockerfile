# Container image for the MAK4I MCP server.
#
# The image is transport- and backend-agnostic: MAK4I_TRANSPORT,
# MAK4I_STORE, MAK4I_CONTROL_PLANE_DB and friends are supplied at run time
# (see docs/DEPLOYMENT.md), not baked in — the same image serves the
# stdio and the Streamable HTTP transports.
FROM python:3.13-slim

RUN pip install --no-cache-dir uv

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY src/ src/

RUN uv sync --frozen --no-dev --no-editable

ENV PATH="/app/.venv/bin:${PATH}"

EXPOSE 8080

CMD ["python", "-m", "mak4i.mcp_server"]
