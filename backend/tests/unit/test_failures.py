import json

import pytest

from app import db
from app.failures import record_failure


async def test_record_failure_never_raises_and_logs_payload_to_stderr(
    capsys: pytest.CaptureFixture[str],
) -> None:
    db.set_pool(None)  # no database: the write must fail softly

    await record_failure(
        request_id="00000000-0000-0000-0000-000000000001",
        conversation_id=None,
        stage="validation",
        failure_type="schema_validation_failed",
        raw_output="not json",
    )

    line = next(ln for ln in capsys.readouterr().err.splitlines() if "FAILURE_RECORD" in ln)
    payload = json.loads(line.split(" ", 1)[1])
    assert payload["failure"]["failure_type"] == "schema_validation_failed"
    assert payload["failure"]["raw_output"] == "not json"


async def test_record_failure_survives_bad_ids(capsys: pytest.CaptureFixture[str]) -> None:
    await record_failure(
        request_id="not-a-uuid", conversation_id=None, stage="storage", failure_type="x"
    )
    assert "FAILURE_RECORD_WRITE_FAILED" in capsys.readouterr().err
