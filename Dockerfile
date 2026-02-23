# AgentCafe — single image, multiple services via different entrypoints.
# See docker-compose.yml for how each service uses this image.

FROM python:3.12-slim

WORKDIR /app

# Install dependencies first (layer caching).
# Copy pyproject.toml + minimal package stub so hatch can resolve the project.
COPY pyproject.toml .
COPY agentcafe/__init__.py agentcafe/__init__.py
RUN pip install --no-cache-dir .

# Copy full application code and design files
COPY agentcafe/ agentcafe/
COPY docs/design/ docs/design/

# Default: run the Cafe (override in docker-compose for backends)
EXPOSE 8000
CMD ["uvicorn", "agentcafe.main:app", "--host", "0.0.0.0", "--port", "8000"]
