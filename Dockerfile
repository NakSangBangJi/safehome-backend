FROM python:3.12-slim
WORKDIR /app
COPY requirements-lock.txt .
RUN pip install --no-cache-dir -r requirements-lock.txt
COPY . .
ENV DATABASE_PATH=/data/mock.sqlite3
EXPOSE 8000
CMD ["python","run.py","--host","0.0.0.0","--port","8000"]
