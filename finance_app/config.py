from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class AppConfig:
    host: str
    port: int
    database_path: Path
    seed_path: Path
    static_path: Path
    documents_path: Path
    max_upload_bytes: int = 15 * 1024 * 1024

    @classmethod
    def from_environment(cls) -> "AppConfig":
        project_root = Path(__file__).resolve().parent.parent
        data_dir = Path(os.getenv("FINANCE_DATA_DIR", project_root / "data"))
        data_dir.mkdir(parents=True, exist_ok=True)

        return cls(
            host=os.getenv("FINANCE_HOST", "127.0.0.1"),
            port=int(os.getenv("PORT", os.getenv("FINANCE_PORT", "8080"))),
            database_path=Path(os.getenv("FINANCE_DATABASE", data_dir / "finance.db")),
            seed_path=Path(
                os.getenv("FINANCE_SEED", project_root / "data" / "initial_transactions.json")
            ),
            static_path=project_root / "finance_app" / "static",
            documents_path=Path(os.getenv("FINANCE_DOCUMENTS_DIR", data_dir / "documents")),
        )
