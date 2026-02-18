from fastapi import APIRouter

router = APIRouter(tags=["Sistem"])


@router.get("/health")
async def health_check() -> dict[str, str]:
    """Sağlık kontrolü."""
    return {"status": "ok", "service": "Maliyet Asistanı API"}
