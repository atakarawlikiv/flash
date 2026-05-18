FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

COPY . .

EXPOSE 8000

ENV HOME=/tmp

CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "200", "--worker-tmp-dir", "/tmp", "app:app"]
