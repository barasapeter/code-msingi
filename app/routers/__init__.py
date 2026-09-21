from fastapi import APIRouter

from app.routers.ebooks import router as ebooks_router

api_router = APIRouter()
api_router.include_router(ebooks_router, prefix="/ebooks", tags=["ebooks"])
