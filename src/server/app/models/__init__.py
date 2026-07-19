def import_all_models() -> None:
    """Import model modules here so Alembic can discover their metadata."""

    from app.modules.acquisition import models as acquisition_models
    from app.modules.etfs import models as etf_models
    from app.modules.indices import models as index_models
    from app.modules.operations import models as operations_models
    from app.modules.processing import models as processing_models
    from app.modules.stocks import models as stock_models
    from app.modules.topics import models as topic_models

    _ = (
        acquisition_models,
        etf_models,
        index_models,
        operations_models,
        processing_models,
        stock_models,
        topic_models,
    )
