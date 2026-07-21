"""FastAPI router factories for the Dashboard HTTP composition layer."""

from .admin import AdminAccess, create_admin_router
from .market import create_market_router
from .messages import create_messages_router
from .practice import create_practice_router
from .system import create_system_router

__all__ = [
    "AdminAccess",
    "create_admin_router",
    "create_market_router",
    "create_messages_router",
    "create_practice_router",
    "create_system_router",
]
