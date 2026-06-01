FROM python:3.12-slim

# tzdata is required for Europe/Berlin timezone in pandas
RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata \
 && rm -rf /var/lib/apt/lists/*

ENV TZ=Europe/Berlin

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p assets/models

ENV PYTHONUNBUFFERED=1

# Run gunicorn from src/ so all inter-module imports resolve without a package prefix.
# Long timeout — scraping 200+ detail pages can take up to 30 minutes.
CMD exec gunicorn --chdir /app/src --bind "0.0.0.0:$PORT" --workers 1 --threads 2 --timeout 1800 app:app
