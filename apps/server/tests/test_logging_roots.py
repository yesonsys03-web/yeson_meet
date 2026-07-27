"""영상 파이프라인 로그가 실제로 나가는지 — 익스포트 진단의 전제."""
import logging


def test_video_pipeline_logger_emits_info():
    """실기(윈도우 '익스포트 파일이 안 생긴다'): 서버 로그 1000줄이 전부 uvicorn
    액세스 로그였고, 익스포트가 '어디에 썼는지' 남기는 로그가 한 줄도 없었다.
    핸들러를 'apps.server'에만 달아 'yeson.*'의 INFO가 통째로 버려졌기 때문."""
    import apps.server.main  # noqa: F401  — 임포트 시 로깅 구성

    lg = logging.getLogger("yeson.video.pipeline")
    assert lg.isEnabledFor(logging.INFO), "INFO가 필터로 막히면 진단 로그가 사라진다"
    chain = [lg, logging.getLogger("yeson.video"), logging.getLogger("yeson")]
    assert any(x.handlers for x in chain), "체인에 핸들러가 없으면 출력되지 않는다"


def test_app_server_logger_still_configured():
    import apps.server.main  # noqa: F401

    lg = logging.getLogger("apps.server.api")
    assert lg.isEnabledFor(logging.INFO)
    assert logging.getLogger("apps.server").handlers
