FROM python:3.11-slim

WORKDIR /app

COPY . .
RUN pip install --no-cache-dir .

EXPOSE 7860

CMD ["uvicorn", "trippilot.api:app", "--host", "0.0.0.0", "--port", "7860"]
