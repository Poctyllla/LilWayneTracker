"""Start the Audio Service locally:  .venv/Scripts/python.exe run.py  (http://127.0.0.1:8770/health)"""

import uvicorn

from audio_service.app import create_app
from audio_service.config import load_settings

if __name__ == "__main__":
    settings = load_settings()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)
