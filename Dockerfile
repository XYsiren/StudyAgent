FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-chi-sim libgomp1 curl \
    && rm -rf /var/lib/apt/lists/*
COPY backend/ /app/
ARG INSTALL_EXTRAS=ml,evaluation
# CPU wheels avoid downloading a CUDA runtime for the default container.
RUN pip install torch --index-url https://download.pytorch.org/whl/cpu \
    && pip install ".[${INSTALL_EXTRAS}]" \
    && useradd --create-home --uid 10001 rag \
    && mkdir -p /app/data && chown -R rag:rag /app
USER rag
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s CMD curl -fsS http://localhost:8000/api/health || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
