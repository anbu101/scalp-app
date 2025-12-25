# ---------- Base image (Apple Silicon compatible) ----------
    FROM python:3.10-slim

    # ----------------------------------
    # System tools (sqlite3 REQUIRED)
    # ----------------------------------
    RUN apt-get update \
        && apt-get install -y --no-install-recommends sqlite3 \
        && rm -rf /var/lib/apt/lists/*

    # ---------- Set working directory ----------
    WORKDIR /app
    
    # ---------- System dependencies (required for numpy/pandas) ----------
    RUN apt-get update && apt-get install -y \
        build-essential \
        gcc \
        g++ \
        && rm -rf /var/lib/apt/lists/*
    
    # ---------- Copy requirements first (better Docker caching) ----------
    COPY requirements.txt .
    
    # ---------- Install Python dependencies ----------
    RUN pip install --no-cache-dir -r requirements.txt
    
    # ---------- Copy backend source code ----------
    COPY . .
    
    # ---------- Expose FastAPI port ----------
    EXPOSE 8000
    
    # ---------- Start backend ----------
    CMD ["uvicorn", "app.api_server:app", "--host", "0.0.0.0", "--port", "8000"]
    