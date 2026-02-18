"""FastAPI bağımlılıkları – servis instance'ları."""

from typing import Annotated

from fastapi import Depends, HTTPException, Request


def get_bot_manager(request: Request):
    """BotManager instance (lifespan'da atanır)."""
    mgr = getattr(request.app.state, "bot_manager", None)
    if mgr is None:
        raise HTTPException(status_code=503, detail="Servis henüz hazır değil: bot_manager")
    return mgr


def get_maps_service(request: Request):
    mgr = getattr(request.app.state, "maps_service", None)
    if mgr is None:
        raise HTTPException(status_code=503, detail="Harita servisi henüz hazır değil")
    return mgr


def get_maps_service_optional(request: Request):
    """Harita servisi (opsiyonel; yoksa None)."""
    return getattr(request.app.state, "maps_service", None)


def get_ai_service(request: Request):
    svc = getattr(request.app.state, "ai_service", None)
    if svc is None:
        raise HTTPException(status_code=503, detail="AI servisi henüz hazır değil")
    return svc


def get_filter_service(request: Request):
    svc = getattr(request.app.state, "filter_service", None)
    if svc is None:
        raise HTTPException(status_code=503, detail="Servis henüz hazır değil: filter_service")
    return svc


def get_data_processor(request: Request):
    proc = getattr(request.app.state, "data_processor", None)
    if proc is None:
        raise HTTPException(status_code=503, detail="Servis henüz hazır değil: data_processor")
    return proc


def require_services(request: Request):
    """Tüm ana servislerin hazır olmasını kontrol eder."""
    missing = []
    if getattr(request.app.state, "bot_manager", None) is None:
        missing.append("bot_manager")
    if getattr(request.app.state, "filter_service", None) is None:
        missing.append("filter_service")
    if getattr(request.app.state, "data_processor", None) is None:
        missing.append("data_processor")
    if missing:
        raise HTTPException(status_code=503, detail=f"Servis henüz hazır değil: {', '.join(missing)}")
    return None
