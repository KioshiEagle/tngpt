FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-docker.txt .

# Torch CPU en premier
RUN pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu

# Reste des dépendances
RUN pip install --no-cache-dir -r requirements-docker.txt

COPY . .

CMD ["python", "main.py"]