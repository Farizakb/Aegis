FROM python:3.11-slim

WORKDIR /app

# Deps-first layering: dependency layers depend only on pyproject.toml.
COPY pyproject.toml ./

# CPU-only torch first so sentence-transformers doesn't pull CUDA wheels (~6GB).
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

RUN python -c "import tomllib; d = tomllib.load(open('pyproject.toml','rb')); print(chr(10).join(d['project']['dependencies']))" > /tmp/reqs.txt \
    && pip install --no-cache-dir -r /tmp/reqs.txt

COPY mock_app ./mock_app
COPY stream ./stream
COPY agent ./agent
COPY retrieval ./retrieval
COPY tools ./tools
COPY policy ./policy
COPY sandbox ./sandbox
COPY apply ./apply
COPY hitl ./hitl
COPY evals ./evals
COPY observability ./observability
COPY dashboard ./dashboard
RUN pip install --no-cache-dir --no-deps .

CMD ["uvicorn", "mock_app.main:app", "--host", "0.0.0.0", "--port", "8000"]
