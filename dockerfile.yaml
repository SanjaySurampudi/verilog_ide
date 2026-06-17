FROM python:3.10-slim

# Install Icarus Verilog toolchain dependencies
RUN apt-get update && apt-get install -y \
    iverilog \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy and install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all code to the container
COPY . .

# Expose and start the server on Render's required port mapping
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "10000"]