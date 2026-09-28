FROM python:3.11-slim
WORKDIR /app
ENV PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
RUN apt-get update && apt-get install -y --no-install-recommends git curl openssh-client postgresql-client && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
COPY database.py .
COPY conversation_log.py .
COPY curator_provisioner.py .
COPY grafana_generator.py .
COPY maintenance.py .
COPY statistics.py .
COPY deliberation_policy.py .
COPY debate_analysis_policy.py .
COPY backup_retention.py .
COPY templates/ templates/
COPY static/ static/
COPY lang/ lang/

EXPOSE 8088
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8088", "--proxy-headers", "--forwarded-allow-ips", "*"]
