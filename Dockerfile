FROM python:3.10-slim

# Install Icarus Verilog, Yosys (RTL synthesis), and compiler dependencies
RUN apt-get update && apt-get install -y \
    iverilog \
    yosys \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy and install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the backend files
COPY . .

# Run the server on the port provided by Render
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "10000"]
