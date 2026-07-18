from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def is_database_ready(db: AsyncSession) -> bool:
    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        return False
    return True
