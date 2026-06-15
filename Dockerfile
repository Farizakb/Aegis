FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml ./
COPY mock_app ./mock_app
COPY stream ./stream

RUN pip install --no-cache-dir .

CMD ["uvicorn", "mock_app.main:app", "--host", "0.0.0.0", "--port", "8000"]
