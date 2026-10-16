FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
# Ragas imports GitPython even when only its evaluation API is used.
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-chi-sim libgomp1 curl git postgresql-client \
    && rm -rf /var/lib/apt/lists/*
# CPU wheels avoid downloading a CUDA runtime for the default container.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --retries 8 --timeout 60 torch --index-url https://download.pytorch.org/whl/cpu
COPY backend/ /app/
ARG INSTALL_EXTRAS=ml,evaluation
ARG PIP_INDEX_URL=https://pypi.org/simple
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --retries 8 --timeout 60 ".[${INSTALL_EXTRAS}]" \
    && useradd --create-home --uid 10001 rag \
    && mkdir -p /app/data && chown -R rag:rag /app
USER rag
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=15s --start-period=60s CMD curl -fsS http://localhost:8000/api/ready || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
