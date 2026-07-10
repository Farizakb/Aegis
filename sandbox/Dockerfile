FROM python:3.11-slim

RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*

WORKDIR /repo
COPY pyproject.toml ./
COPY mock_app ./mock_app
COPY stream ./stream
COPY agent ./agent
COPY retrieval ./retrieval
COPY tools ./tools
COPY policy ./policy
COPY tests ./tests
COPY sandbox ./sandbox
COPY apply ./apply
COPY hitl ./hitl
RUN pip install --no-cache-dir .[dev]

RUN git config --global user.email "sandbox@aegis.local" \
    && git config --global user.name "aegis-sandbox" \
    && git init && git add -A && git commit -m "sandbox base"

COPY sandbox/entrypoint.sh /sandbox/entrypoint.sh
RUN chmod +x /sandbox/entrypoint.sh
