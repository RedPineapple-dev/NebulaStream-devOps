FROM python:3.12-slim AS builder
WORKDIR /build
RUN apt-get update && apt-get install -y --no-install-recommends gcc && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-slim AS runtime
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin nebula
COPY --from=builder /install /usr/local
WORKDIR /app
COPY --chown=nebula:nebula . .
USER nebula
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import httpx,sys; r=httpx.get('http://127.0.0.1:8000/healthz', timeout=2); sys.exit(0 if r.status_code==200 else 1)"
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
