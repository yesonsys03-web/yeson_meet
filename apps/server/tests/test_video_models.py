"""VideoJob / VideoSegment ORM round-trip."""
from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select

from apps.server.db.models import AppUser, VideoJob, VideoSegment


async def test_video_job_and_segments_roundtrip(db_session, admin_user: AppUser):
    job = VideoJob(
        external_id=uuid4(),
        owner_user_id=admin_user.id,
        title="Test clip",
        source_type="upload",
        source_ref="clip.mp4",
        whisper_model="small",
        status="queued",
    )
    db_session.add(job)
    await db_session.flush()

    db_session.add(VideoSegment(job_id=job.id, seq=1, start_ms=0, end_ms=1500,
                                text_en="Hello", text_ko="안녕하세요"))
    await db_session.commit()

    loaded = (await db_session.execute(
        select(VideoJob).where(VideoJob.external_id == job.external_id)
    )).scalar_one()
    assert loaded.status == "queued"
    assert loaded.progress == 0
    seg = (await db_session.execute(
        select(VideoSegment).where(VideoSegment.job_id == loaded.id)
    )).scalar_one()
    assert seg.text_ko == "안녕하세요"
