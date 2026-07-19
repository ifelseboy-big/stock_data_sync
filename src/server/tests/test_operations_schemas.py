from app.modules.operations.schemas import CreateBackfillCommand


def test_backfill_command_accepts_concise_reason() -> None:
    command = CreateBackfillCommand.model_validate(
        {
            "startDate": "2026-07-01",
            "endDate": "2026-07-02",
            "apiNames": ["daily"],
            "reason": "回填",
        }
    )

    assert command.reason == "回填"
