# Demo server: the API plus the frontend-d viewer on one port. Data and cache are mounted, not baked in.
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.8.17 /uv /bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --extra parsers --extra brain --extra detector
COPY . .
ENV BLUEPRINTS_DATA=/data BLUEPRINTS_CACHE=/cache
EXPOSE 8000
CMD ["uv", "run", "--no-sync", "uvicorn", "blueprints.api:app", "--host", "0.0.0.0", "--port", "8000"]
