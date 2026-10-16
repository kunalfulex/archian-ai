import os
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, Tuple
from db.connection import database
from services.entitlements import get_entitlements
from dotenv import load_dotenv

load_dotenv()

class UserManagementService:
    
    @staticmethod
    async def soft_delete_user(user_id: str, reason: str = "User requested account deletion") -> Tuple[bool, str]:
        """Soft delete a user account"""
        try:
            # Soft delete the user
            await database.execute(
                """
                UPDATE users 
                SET deleted_at = NOW(),
                    deletion_reason = :reason,
                    is_deleted = TRUE,
                    is_active = FALSE,
                    updated_at = NOW()
                WHERE id = :user_id
                """,
                values={
                    "user_id": user_id,
                    "reason": reason
                }
            )
            
            # Also soft delete related user data
            await UserManagementService._soft_delete_user_related_data(user_id)
            
            await get_entitlements().on_account_deleted(user_id)
            
            print(f"✅ User {user_id} soft deleted successfully")
            return True, "Account deleted successfully. You can reactivate within 30 days."
            
        except Exception as e:
            print(f"❌ Error soft deleting user {user_id}: {e}")
            return False, f"Error deleting account: {str(e)}"
    
    @staticmethod
    async def _soft_delete_user_related_data(user_id: str):
        """Soft delete user-related data"""
        try:
            # Update user plans status to paused_for_deletion (if subscription exists)
            try:
                await database.execute(
                    """
                    UPDATE user_plans 
                    SET status = 'paused_for_deletion', 
                        updated_at = NOW()
                    WHERE user_id = :user_id AND stripe_subscription_id IS NOT NULL
                    """,
                    values={"user_id": user_id}
                )
                print(f"✅ User plans status updated to 'paused_for_deletion' for user {user_id}")
            except Exception as e:
                print(f"⚠️ Note: Could not update user plans status: {e}")
            
            # Add more tables as needed
            # await database.execute("UPDATE user_flows SET is_deleted = TRUE WHERE user_id = :user_id", {"user_id": user_id})
            # await database.execute("UPDATE user_connections SET is_deleted = TRUE WHERE user_id = :user_id", {"user_id": user_id})
            
            print(f"✅ Related data soft deleted for user {user_id}")
            
        except Exception as e:
            print(f"⚠️ Warning: Could not soft delete related data for user {user_id}: {e}")
    
    @staticmethod
    async def reactivate_user(user_id: str) -> Tuple[bool, str]:
        """Reactivate a soft-deleted user account"""
        try:
            # Check if user exists and is soft-deleted
            user = await database.fetch_one(
                """
                SELECT id, email, is_deleted, deleted_at
                FROM users 
                WHERE id = :user_id
                """,
                values={"user_id": user_id}
            )
            
            if not user:
                return False, "User not found"
            
            if not hasattr(user, 'is_deleted') or not user.is_deleted:
                return False, "User account is already active"
            
            # Check if within 30-day reactivation window
            if hasattr(user, 'deleted_at') and user.deleted_at:
                deletion_date = user.deleted_at
                
                # Ensure deletion_date is timezone-aware
                if isinstance(deletion_date, str):
                    deletion_date = datetime.fromisoformat(deletion_date.replace('Z', '+00:00'))
                elif deletion_date and deletion_date.tzinfo is None:
                    # If it's a naive datetime, assume it's UTC
                    deletion_date = deletion_date.replace(tzinfo=timezone.utc)
                
                # Get current time in UTC
                current_time = datetime.now(timezone.utc)
                
                days_since_deletion = (current_time - deletion_date).days
                
                if days_since_deletion > 30:
                    return False, "Account cannot be reactivated after 30 days"
            
            # Reactivate the user
            await database.execute(
                """
                UPDATE users 
                SET deleted_at = NULL,
                    deletion_reason = NULL,
                    is_deleted = FALSE,
                    is_active = TRUE,
                    updated_at = NOW()
                WHERE id = :user_id
                """,
                values={"user_id": user_id}
            )
            
            # Reactivate related data
            await UserManagementService._reactivate_user_related_data(user_id)
            
            await get_entitlements().on_account_reactivated(user_id)
            
            print(f"✅ User {user_id} reactivated successfully")
            return True, "Account reactivated successfully"
            
        except Exception as e:
            print(f"❌ Error reactivating user {user_id}: {e}")
            return False, f"Error reactivating account: {str(e)}"
    
    @staticmethod
    async def _reactivate_user_related_data(user_id: str):
        """Reactivate user-related data"""
        try:
            # Reactivate user plans (if is_deleted column exists)
            try:
                await database.execute(
                    """
                    UPDATE user_plans 
                    SET status = 'active', 
                        updated_at = NOW()
                    WHERE user_id = :user_id AND stripe_subscription_id IS NOT NULL
                    """,
                    values={"user_id": user_id}
                )
                print(f"✅ User plans status updated to 'active' for user {user_id}")
            except Exception as e:
                print(f"⚠️ Note: Could not update user plans status: {e}")
                # If column doesn't exist, just log it
            
            # Add more tables as needed
            # await database.execute("UPDATE user_flows SET is_deleted = FALSE WHERE user_id = :user_id", {"user_id": user_id})
            # await database.execute("UPDATE user_connections SET is_deleted = FALSE WHERE user_id = :user_id", {"user_id": user_id})
            
            print(f"✅ Related data reactivated for user {user_id}")
            
        except Exception as e:
            print(f"⚠️ Warning: Could not reactivate related data for user {user_id}: {e}")
    
    @staticmethod
    async def cleanup_expired_deletions() -> Tuple[bool, str, int]:
        """Permanently delete users after 30 days"""
        try:
            await get_entitlements().cleanup_expired_deletions()
            
            # Get count of expired deletions
            expired_users = await database.fetch_all(
                """
                SELECT id, email, deleted_at
                FROM users 
                WHERE is_deleted = TRUE 
                AND deleted_at < NOW() - INTERVAL '30 days'
                """
            )
            
            if not expired_users:
                return True, "No expired deletions to clean up", 0
            
            # Permanently delete expired users
            await database.execute(
                """
                DELETE FROM users 
                WHERE is_deleted = TRUE 
                AND deleted_at < NOW() - INTERVAL '30 days'
                """
            )
            
            # Also clean up related data
            for user in expired_users:
                await UserManagementService._permanently_delete_user_data(user.id)
            
            deleted_count = len(expired_users)
            print(f"✅ Cleaned up {deleted_count} expired user deletions")
            
            return True, f"Successfully cleaned up {deleted_count} expired deletions", deleted_count
            
        except Exception as e:
            print(f"❌ Error cleaning up expired deletions: {e}")
            return False, f"Error cleaning up expired deletions: {str(e)}", 0
    
    @staticmethod
    async def _permanently_delete_user_data(user_id: str):
        """Permanently delete user-related data"""
        try:
            # Permanently delete user plans
            try:
                await database.execute(
                    "DELETE FROM user_plans WHERE user_id = :user_id",
                    values={"user_id": user_id}
                )
                print(f"✅ User plans permanently deleted for user {user_id}")
            except Exception as e:
                print(f"⚠️ Note: Could not delete user_plans: {e}")
            
            # Add more tables as needed
            # await database.execute("DELETE FROM user_flows WHERE user_id = :user_id", {"user_id": user_id})
            # await database.execute("DELETE FROM user_connections WHERE user_id = :user_id", {"user_id": user_id})
            
            print(f"✅ Related data permanently deleted for user {user_id}")
            
        except Exception as e:
            print(f"⚠️ Warning: Could not permanently delete related data for user {user_id}: {e}")
    
    @staticmethod
    async def get_user_deletion_status(user_id: str) -> Optional[Dict[str, Any]]:
        """Get user deletion status"""
        try:
            result = await database.fetch_one(
                """
                SELECT id, email, is_deleted, deleted_at, deletion_reason, is_active
                FROM users 
                WHERE id = :user_id
                """,
                values={"user_id": user_id}
            )
            
            if result:
                return dict(result)
            return None
            
        except Exception as e:
            print(f"❌ Error getting user deletion status: {e}")
            return None
    
    @staticmethod
    async def check_user_can_reactivate(email: str) -> Tuple[bool, str, Optional[str]]:
        """Check if a user can reactivate their account"""
        try:
            result = await database.fetch_one(
                """
                SELECT id, is_deleted, deleted_at
                FROM users 
                WHERE email = :email
                """,
                values={"email": email}
            )
            
            if not result:
                return False, "No account found with this email", None
            
            if not hasattr(result, 'is_deleted') or not result.is_deleted:
                return False, "Account is already active", None
            
            # Check if within 30-day reactivation window
            if hasattr(result, 'deleted_at') and result.deleted_at:
                deletion_date = result.deleted_at
                
                # Ensure deletion_date is timezone-aware
                if isinstance(deletion_date, str):
                    deletion_date = datetime.fromisoformat(deletion_date.replace('Z', '+00:00'))
                elif deletion_date and deletion_date.tzinfo is None:
                    # If it's a naive datetime, assume it's UTC
                    deletion_date = deletion_date.replace(tzinfo=timezone.utc)
                
                # Get current time in UTC
                current_time = datetime.now(timezone.utc)
                
                days_since_deletion = (current_time - deletion_date).days
                
                if days_since_deletion > 30:
                    return False, "Account cannot be reactivated after 30 days", None
                
                days_remaining = 30 - days_since_deletion
                return True, f"Account can be reactivated. {days_remaining} days remaining.", result['id']
            
            return False, "Account deletion date not found", None
            
        except Exception as e:
            print(f"❌ Error checking user reactivation: {e}")
            return False, f"Error checking account status: {str(e)}", None

    @staticmethod
    async def get_user_subscription_status(user_id: str) -> Optional[Dict[str, Any]]:
        """Get user's subscription status including paused subscriptions"""
        try:
            result = await database.fetch_one(
                """
                SELECT 
                    up.stripe_subscription_id, 
                    up.stripe_customer_id, 
                    up.plan_type, 
                    up.status,
                    up.current_period_start,
                    up.current_period_end,
                    u.is_deleted,
                    u.deleted_at
                FROM user_plans up
                JOIN users u ON up.user_id = u.id
                WHERE up.user_id = :user_id
                """,
                values={"user_id": user_id}
            )
            
            if result:
                subscription_data = dict(result)
                
                # Add subscription details from Stripe if available
                if subscription_data.get('stripe_subscription_id'):
                    stripe_status = await get_entitlements().subscription_details(
                        subscription_data['stripe_subscription_id']
                    )
                    if stripe_status:
                        subscription_data.update(stripe_status)
                
                return subscription_data
            return None
            
        except Exception as e:
            print(f"❌ Error getting user subscription status: {e}")
            return None
