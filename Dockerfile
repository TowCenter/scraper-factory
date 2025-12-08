FROM mcr.microsoft.com/playwright/python:v1.56.0-jammy

WORKDIR /app

# Keep Python output unbuffered and pip quiet
ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Default entrypoint invokes the CLI; append args at docker run
ENTRYPOINT ["xvfb-run", "-a", "python", "cli.py"]

