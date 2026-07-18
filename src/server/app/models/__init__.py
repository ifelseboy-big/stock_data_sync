def import_all_models() -> None:
    """Import model modules here so Alembic can discover their metadata."""

    from app.modules.operations import models as operations_models
    from app.modules.stocks import models as stock_models
    from app.modules.tasking import models as tasking_models

    _ = (operations_models, stock_models, tasking_models)
