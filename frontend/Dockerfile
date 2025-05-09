FROM python:3.12-slim

WORKDIR /app

# Install uv
RUN pip install --no-cache-dir uv

# Copy only requirements first to leverage Docker cache
COPY ./requirements.txt ./

# Install dependencies using uv
RUN uv pip install --system --no-cache -r requirements.txt

# Copy the application file
COPY ./app_streamlit.py /app/app_streamlit.py

# Environment variable for the backend API URL
ENV BACKEND_API_URL="http://backend:8000/api/fetch-repo"

EXPOSE 8501

HEALTHCHECK CMD curl --fail http://localhost:8501/_stcore/health || exit 1

CMD ["streamlit", "run", "app_streamlit.py", "--server.port=8501", "--server.address=0.0.0.0"]