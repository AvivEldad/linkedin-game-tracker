FROM python:3.12-alpine

WORKDIR /app

COPY backend/requirements.txt .

RUN pip install -r requirements.txt

COPY . .

CMD ["python", "backend/main.py"]