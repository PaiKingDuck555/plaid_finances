FROM python:3.13-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

ENV PORT=8080
ENV DATABASE_PATH=/data/transactions.db
EXPOSE 8080

CMD ["python", "app.py"]
