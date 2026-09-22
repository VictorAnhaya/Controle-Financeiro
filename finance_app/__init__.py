from .config import AppConfig
from .database import Database
from .http import FinanceHttpApplication
from .repository import FinanceRepository
from .service import FinanceService


def create_application(config: AppConfig | None = None) -> FinanceHttpApplication:
    resolved_config = config or AppConfig.from_environment()
    database = Database(resolved_config.database_path)
    database.migrate()

    repository = FinanceRepository(database)
    service = FinanceService(repository, resolved_config.documents_path)
    service.seed_initial_data(resolved_config.seed_path)

    return FinanceHttpApplication(resolved_config, service)


__all__ = ["create_application", "AppConfig"]
