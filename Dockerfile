# 1. Start with an official lightweight Python base image (matching your Python 3.14 environment)
FROM python:3.14-slim

# 2. Set environment variables to optimize Python inside a container
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# 3. Install basic build dependencies for native packages (tree-sitter, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# 4. Set directory inside container
WORKDIR /app

# 5. Copy requirements and install
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# 5. Copy the rest of your Django project code into the container
COPY . /app/

# 6. Expose the port Django runs on
EXPOSE 8000

# Start production server with migrations and static collection
CMD ["sh", "-c", "python manage.py migrate && python manage.py collectstatic --noinput && gunicorn myproject.wsgi:application --bind 0.0.0.0:8000 --workers 2 --threads 2"]