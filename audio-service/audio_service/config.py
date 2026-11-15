"""Where the service keeps its data (overridable for tests and, later, the server)."""

import os
from dataclasses import dataclass
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    host: str = "127.0.0.1"  # local only until the move to a server (stage 7)
    port: int = 8770

    @property
    def database_path(self) -> Path:
        return self.data_dir / "service.db"

    @property
    def storage_dir(self) -> Path:
        return self.data_dir / "objects"


def load_settings() -> Settings:
    data_dir = Path(os.environ.get("AUDIO_SERVICE_DATA", SERVICE_ROOT / "data"))
    return Settings(data_dir=data_dir, port=int(os.environ.get("AUDIO_SERVICE_PORT", "8770")))
