from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from finance_app.config import AppConfig


class AppConfigTests(unittest.TestCase):
    def test_render_port_and_persistent_data_directory_are_used(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            with patch.dict(
                os.environ,
                {
                    "PORT": "10000",
                    "FINANCE_PORT": "8080",
                    "FINANCE_DATA_DIR": temporary_directory,
                },
                clear=True,
            ):
                config = AppConfig.from_environment()

        self.assertEqual(config.port, 10000)
        self.assertEqual(config.database_path, Path(temporary_directory) / "finance.db")
        self.assertEqual(config.documents_path, Path(temporary_directory) / "documents")
        self.assertTrue(config.seed_path.name == "initial_transactions.json")
        self.assertNotEqual(config.seed_path.parent, Path(temporary_directory))

if __name__ == "__main__":
    unittest.main()
