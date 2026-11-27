from __future__ import annotations

from web.config import UPLOAD_DIR
from web.services.jobs import Job, JobStatus, cleanup_job_file


def make_job(file_path: str = "/tmp/test.pdf") -> Job:  # noqa: S108
    return Job(job_id="test-id", filename="test.pdf", file_path=file_path)


def test_job_initial_state():
    job = make_job()
    assert job.status == JobStatus.PENDING
    assert job.result is None
    assert job.error is None
    assert job.error_code is None


def test_cleanup_removes_job_directory_inside_upload_dir():
    job_dir = UPLOAD_DIR / "job-cleanup-test"
    job_dir.mkdir(parents=True, exist_ok=True)
    pdf = job_dir / "test.pdf"
    pdf.write_bytes(b"%PDF-1.4")

    cleanup_job_file(make_job(str(pdf)))

    assert not job_dir.exists()
    assert UPLOAD_DIR.exists()


def test_cleanup_never_removes_upload_dir_itself():
    pdf = UPLOAD_DIR / "direct.pdf"
    pdf.write_bytes(b"%PDF-1.4")

    cleanup_job_file(make_job(str(pdf)))

    assert UPLOAD_DIR.exists()
    assert pdf.exists()
    pdf.unlink()


def test_cleanup_ignores_paths_outside_upload_dir(tmp_path):
    outside = tmp_path / "outside" / "test.pdf"
    outside.parent.mkdir()
    outside.write_bytes(b"%PDF-1.4")

    cleanup_job_file(make_job(str(outside)))

    assert outside.exists()


def test_cleanup_without_file_path_is_noop():
    cleanup_job_file(make_job(""))
