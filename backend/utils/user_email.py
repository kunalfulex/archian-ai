"""Lookup helpers for user contact info."""

from typing import Optional

from db.connection import database


async def get_user_email(user_id: str) -> Optional[str]:
    """Get user email for notifications"""
    try:
        result = await database.fetch_one(
            "SELECT email FROM users WHERE id = :user_id",
            {"user_id": user_id}
        )
        return result['email'] if result else None
    except Exception as e:
        print(f"Error getting user email: {e}")
        return None
