"""apply_job_authority locks source pins from JobContext."""
from services.intent.job_authority import apply_job_authority
from services.intent.job_context import JobContext, ResourcePin


def _file_job() -> JobContext:
    return JobContext(
        job_id="job-file-1",
        source=ResourcePin(
            connector_type="file",
            display_name="contacts.xlsx",
            entity_key="Contact",
        ),
        last_completed_step="extract",
    )


def test_apply_job_authority_locks_file_source():
    cls = {"source": "salesforce", "source_name": "SF 1", "destination": "salesforce"}
    out = apply_job_authority(cls, _file_job())
    assert out["source"] == "file"
    assert out["source_name"] == "contacts.xlsx"
    assert out["document_type"] == "Contact"
    assert out["active_job_id"] == "job-file-1"


def test_base_classification_file_branch():
    from services.intent.continuation_planning import _base_classification_from_job
    from services.intent.session_continuation import SessionContinuationDecision

    decision = SessionContinuationDecision(
        session_action="continue_active_job",
        continuation_phase="map",
        reuse_job_id=True,
        job_id="job-file-1",
    )
    cls = _base_classification_from_job(decision, _file_job())
    assert cls["source"] == "file"
    assert cls["document_type"] == "Contact"
    assert cls["source_entity_key"] == "Contact"
