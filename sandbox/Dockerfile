FROM python:3.11-slim

RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*

WORKDIR /repo

# Deps-first layering: everything up to the source COPYs depends only on
# pyproject.toml, so source edits reuse the cached (expensive) dependency layers.
COPY pyproject.toml ./

# CPU-only torch first, so sentence-transformers doesn't pull CUDA wheels.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

# Install the project's dependencies WITHOUT the project itself (its package
# dirs aren't copied yet). chr(10) sidesteps shell/Dockerfile "\n" escaping.
RUN python -c "import tomllib; d = tomllib.load(open('pyproject.toml','rb')); print(chr(10).join(d['project']['dependencies'] + d['project']['optional-dependencies']['dev']))" > /tmp/reqs.txt \
    && pip install --no-cache-dir -r /tmp/reqs.txt

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
COPY evals ./evals
RUN pip install --no-cache-dir --no-deps .[dev]

RUN git config --global user.email "sandbox@aegis.local" \
    && git config --global user.name "aegis-sandbox" \
    && git init && git add -A && git commit -m "sandbox base"

COPY sandbox/entrypoint.sh /sandbox/entrypoint.sh
RUN chmod +x /sandbox/entrypoint.sh
