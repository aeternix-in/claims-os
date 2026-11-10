# Build from the repo root:  docker build -f engine/Dockerfile -t aeternix-engine .
FROM python:3.12-slim
WORKDIR /srv
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr poppler-utils && rm -rf /var/lib/apt/lists/*
COPY engine/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY nhcx/aeternix-core /nhcx/aeternix-core
RUN pip install --no-cache-dir --no-deps /nhcx/aeternix-core
COPY engine/app app
COPY engine/rules rules
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
