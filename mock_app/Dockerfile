FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir "fastapi>=0.110" "uvicorn[standard]>=0.29" "redis>=5.0" "pydantic>=2.6"

COPY mock_app ./mock_app
COPY stream ./stream

CMD ["uvicorn", "mock_app.main:app", "--host", "0.0.0.0", "--port", "8000"]
