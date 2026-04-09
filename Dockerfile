FROM python:3.11-slim

WORKDIR /app

# Systémové věci
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Příprava složek předem
RUN mkdir -p /app/instance && chmod -R 777 /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Ještě jednou práva po zkopírování souborů
RUN chmod -R 777 /app

EXPOSE 5000

CMD ["python", "app.py"]
