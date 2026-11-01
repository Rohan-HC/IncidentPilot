FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

COPY pyproject.toml README.md ./
COPY src ./src
# The service performs embedding inference on CPU. Installing torch from the
# CPU wheel index avoids pulling multi-gigabyte CUDA runtime packages.
RUN python -m pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
    && python -m pip install --no-cache-dir '.[rag,graph]'

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "incidentpilot.api:app", "--host", "0.0.0.0", "--port", "8000"]
