FROM python:3.12-slim-bookworm

WORKDIR /app

COPY backend/requirements.txt .

RUN pip install -r requirements.txt

COPY . .

CMD ["python", "backend/main.py"]
