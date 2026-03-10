FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY miner.py .

EXPOSE 8099
CMD ["python", "-u", "miner.py"]
