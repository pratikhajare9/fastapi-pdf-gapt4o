# Use an official Python runtime as a parent image
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set working directory
WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Expose the port your app runs on (e.g., 8000 for FastAPI, Flask)
EXPOSE 8000

# Command to run the app - adjust as needed
# For FastAPI: uvicorn main:app --host 0.0.0.0 --port 8000
# For Flask: python app.py
CMD ["python", "main.py"]