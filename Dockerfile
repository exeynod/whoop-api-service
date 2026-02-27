FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app

COPY requirements.txt .
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

COPY app ./app

RUN addgroup --system app && adduser --system --ingroup app app \
    && mkdir -p /cache /secrets \
    && chown -R app:app /app /cache /secrets

USER app

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import json,sys,urllib.request; r=urllib.request.urlopen('http://127.0.0.1:8001/health', timeout=3); d=json.loads(r.read().decode()); sys.exit(0 if d.get('status')=='ok' else 1)"

EXPOSE 8001

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
