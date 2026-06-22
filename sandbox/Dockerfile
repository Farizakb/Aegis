FROM python:3.11-slim

RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*

WORKDIR /repo
COPY pyproject.toml ./
RUN pip install --no-cache-dir .[dev]

COPY mock_app ./mock_app
COPY stream ./stream
COPY agent ./agent
COPY retrieval ./retrieval
COPY tools ./tools
COPY policy ./policy
COPY tests ./tests

RUN git init && git add -A && git commit -m "sandbox base"

COPY sandbox/entrypoint.sh /sandbox/entrypoint.sh
RUN chmod +x /sandbox/entrypoint.sh
