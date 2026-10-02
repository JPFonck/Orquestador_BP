FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install . && useradd --create-home --uid 1000 appuser

USER appuser

ENV LLM_PROVIDER=anthropic \
    STATE_BACKEND=memory

EXPOSE 8000

# keep-alive mayor que el idle timeout del balanceador para evitar 502 por conexiones cerradas.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*", "--timeout-keep-alive", "200"]
