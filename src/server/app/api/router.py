from fastapi import APIRouter

from app.modules.operations.api import router as operations_router
from app.modules.stocks.api import router as stocks_router
from app.modules.system.api import resources_router
from app.modules.system.api import router as system_router

api_router = APIRouter()
api_router.include_router(system_router, prefix="/health", tags=["health"])
api_router.include_router(stocks_router, prefix="/stocks", tags=["stocks"])
api_router.include_router(operations_router, prefix="/operations", tags=["operations"])
api_router.include_router(resources_router, prefix="/system", tags=["system"])
