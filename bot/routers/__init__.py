"""Interactive UI routers (menu, reviews, settings)."""

from bot.routers.menu import get_menu_router
from bot.routers.reviews import get_reviews_router
from bot.routers.settings import get_settings_router

__all__ = [
    "get_menu_router",
    "get_reviews_router",
    "get_settings_router",
]
