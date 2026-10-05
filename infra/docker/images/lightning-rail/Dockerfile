FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt ./
COPY src src/
RUN pip install --no-cache-dir -r requirements.txt gunicorn

RUN useradd --system --uid 10001 --home /nonexistent rail \
    && mkdir -p /var/lib/kerosene \
    && chown 10001:10001 /var/lib/kerosene
USER 10001:10001
EXPOSE 8091
CMD ["gunicorn", "src.adapters.inbound.http.app:create_app()", "--bind", "0.0.0.0:8091", "--workers", "1", "--threads", "4", "--timeout", "30", "--access-logfile", "-", "--error-logfile", "-"]
