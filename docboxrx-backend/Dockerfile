FROM python:3.12-slim

WORKDIR /app

# Install poetry
RUN pip install poetry

# Copy dependency files
COPY pyproject.toml poetry.lock ./

# Install dependencies
RUN poetry config virtualenvs.create false && poetry install --no-dev --no-interaction --no-ansi

# Copy application code
COPY app ./app
COPY create_tables.py ./

# Expose port
EXPOSE 8000

# Run database initialization and start server
CMD python create_tables.py && uvicorn app.main:app --host 0.0.0.0 --port 8000
