FROM python:3.12-slim

WORKDIR /app

# Install uv
RUN pip install --no-cache-dir uv

# Copy only requirements first to leverage Docker cache
COPY ./requirements.txt ./

# Install dependencies using uv
RUN uv pip install --system --no-cache -r requirements.txt

# Copy the rest of the application code
COPY ./app /app/app

# GITHUB_TOKEN will be an empty string if not set as an environment variable during `docker run` or `docker-compose up`
# The application logic handles this. This line can cause a security warning during build if not careful.
# For production, secrets are best managed via runtime environment variables or secret management systems.
# ENV GITHUB_TOKEN="" # Optional: can be removed if backend handles None gracefully

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]