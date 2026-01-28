#!/usr/bin/env python3
"""
Standalone entry point for PyInstaller bundling.
Runs the FastAPI app with uvicorn programmatically.
"""
import sys
import os

# Ensure app module is importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import uvicorn
from app.api_server import app

if __name__ == "__main__":
    # Get environment variables (set by Tauri)
    host = os.getenv("SCALP_HOST", "127.0.0.1")
    port = int(os.getenv("SCALP_PORT", "47321"))
    
    print(f"[BACKEND] Starting server on {host}:{port}", flush=True)
    
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info",
        access_log=False  # Reduce noise in logs
    )
