"""
Redis service for distributed job tracking.
Replaces in-memory dictionaries with Redis for scalability and persistence.
"""

import json
import os
from typing import Dict, Any, Optional
from datetime import timedelta
from dotenv import load_dotenv

load_dotenv()

# Try to import redis, but make it optional for graceful degradation
try:
    import redis.asyncio as redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    redis = None
    print("⚠️ Redis module not installed. Job tracking will use in-memory storage only.")

# Redis configuration
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
# Above this record count, don't store full data in job result (prevents OOM); frontend fetches from API
REDIS_SLIM_RESULT_THRESHOLD = int(os.getenv("REDIS_SLIM_RESULT_THRESHOLD", "1000"))
REDIS_DB = int(os.getenv("REDIS_DB", "0"))
DEFAULT_JOB_TTL = int(os.getenv("REDIS_JOB_TTL_SECONDS", "86400"))  # 24 hours default TTL

# Global Redis connection pool
_redis_client: Optional[Any] = None
_redis_connection_error = False


async def get_redis_client() -> Optional[Any]:
    """
    Get or create Redis client connection.
    Returns None if Redis is unavailable (allows graceful degradation).
    """
    global _redis_client, _redis_connection_error
    
    # If redis module is not available, return None immediately
    if not REDIS_AVAILABLE:
        return None
    
    # If we previously failed to connect and Redis is not configured, return None
    if _redis_connection_error and REDIS_URL == "redis://localhost:6379/0":
        return None
    
    if _redis_client is None:
        if not REDIS_AVAILABLE or redis is None:
            return None
        try:
            _redis_client = redis.from_url(
                REDIS_URL,
                db=REDIS_DB,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
                retry_on_timeout=True,
                health_check_interval=30
            )
            # Test connection
            await _redis_client.ping()
            _redis_connection_error = False
        except Exception as e:
            print(f"⚠️ Redis connection failed (will use fallback): {e}")
            _redis_connection_error = True
            _redis_client = None
            return None
    
    return _redis_client


async def close_redis_client():
    """Close Redis client connection"""
    global _redis_client
    if _redis_client:
        try:
            await _redis_client.aclose()
        except Exception as e:
            print(f"⚠️ Error closing Redis connection: {e}")
        finally:
            _redis_client = None


class RedisJobStore:
    """
    Redis-backed job storage to replace in-memory dictionaries.
    Provides automatic TTL (time-to-live) for job cleanup.
    Falls back gracefully if Redis is unavailable.
    """
    
    def __init__(self, key_prefix: str = "job:", ttl_seconds: int = DEFAULT_JOB_TTL, fallback_store: Optional[Dict] = None):
        """
        Initialize Redis job store
        
        Args:
            key_prefix: Prefix for all job keys (e.g., "job:", "migration_job:")
            ttl_seconds: Time-to-live for jobs in seconds (default 24 hours)
            fallback_store: Optional in-memory dict for fallback if Redis unavailable
        """
        self.key_prefix = key_prefix
        self.ttl_seconds = ttl_seconds
        self.fallback_store = fallback_store if fallback_store is not None else {}
        self.use_fallback = False
    
    def _get_key(self, job_id: str) -> str:
        """Generate Redis key for job_id"""
        return f"{self.key_prefix}{job_id}"
    
    async def _check_redis_available(self) -> bool:
        """Check if Redis is available, fallback to in-memory if not"""
        if self.use_fallback:
            return False
        
        client = await get_redis_client()
        if client is None:
            if not self.use_fallback:
                print(f"⚠️ Redis unavailable for {self.key_prefix}, using in-memory fallback")
                self.use_fallback = True
            return False
        return True
    
    async def set_job(self, job_id: str, job_data: Dict[str, Any], ttl: Optional[int] = None) -> bool:
        """
        Store job data in Redis with TTL (or fallback to in-memory)
        
        Args:
            job_id: Unique job identifier
            job_data: Dictionary containing job information
            ttl: Optional TTL override (if None, uses instance default)
        
        Returns:
            True if successful
        """
        try:
            if await self._check_redis_available():
                client = await get_redis_client()
                key = self._get_key(job_id)
                
                # Serialize job data to JSON
                json_data = json.dumps(job_data, default=str)
                
                # Store with TTL
                ttl_to_use = ttl if ttl is not None else self.ttl_seconds
                await client.setex(key, ttl_to_use, json_data)
                return True
            else:
                # Fallback to in-memory
                self.fallback_store[job_id] = job_data.copy()
                return True
        except Exception as e:
            print(f"❌ Error setting job {job_id} in Redis: {e}")
            # Fallback to in-memory on error
            try:
                self.fallback_store[job_id] = job_data.copy()
                self.use_fallback = True
                return True
            except Exception as fallback_error:
                print(f"❌ Error in fallback store: {fallback_error}")
                return False
    
    async def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve job data from Redis (or fallback to in-memory)
        
        Args:
            job_id: Unique job identifier
        
        Returns:
            Job data dictionary or None if not found
        """
        try:
            if await self._check_redis_available():
                client = await get_redis_client()
                key = self._get_key(job_id)
                
                json_data = await client.get(key)
                if json_data is None:
                    # Also check fallback store
                    return self.fallback_store.get(job_id)
                
                return json.loads(json_data)
            else:
                # Fallback to in-memory
                return self.fallback_store.get(job_id)
        except Exception as e:
            print(f"❌ Error getting job {job_id} from Redis: {e}")
            # Fallback to in-memory on error
            return self.fallback_store.get(job_id)
    
    async def update_job(self, job_id: str, updates: Dict[str, Any], ttl: Optional[int] = None) -> bool:
        """
        Update job data (merge updates with existing data)
        
        Args:
            job_id: Unique job identifier
            updates: Dictionary with fields to update
            ttl: Optional TTL to reset (if None, keeps existing TTL)
        
        Returns:
            True if successful
        """
        try:
            # Get existing job data
            existing = await self.get_job(job_id)
            if existing is None:
                # Job doesn't exist, create new one
                return await self.set_job(job_id, updates, ttl)
            
            # Merge updates
            existing.update(updates)
            
            # Store updated data
            return await self.set_job(job_id, existing, ttl)
        except Exception as e:
            print(f"❌ Error updating job {job_id} in Redis: {e}")
            # Fallback: update in-memory store
            if job_id in self.fallback_store:
                self.fallback_store[job_id].update(updates)
                return True
            return False
    
    async def delete_job(self, job_id: str) -> bool:
        """
        Delete job from Redis (and fallback store)
        
        Args:
            job_id: Unique job identifier
        
        Returns:
            True if successful
        """
        try:
            if await self._check_redis_available():
                client = await get_redis_client()
                key = self._get_key(job_id)
                result = await client.delete(key)
                # Also remove from fallback
                if job_id in self.fallback_store:
                    del self.fallback_store[job_id]
                return result > 0
            else:
                # Fallback to in-memory
                if job_id in self.fallback_store:
                    del self.fallback_store[job_id]
                    return True
                return False
        except Exception as e:
            print(f"❌ Error deleting job {job_id} from Redis: {e}")
            # Fallback
            if job_id in self.fallback_store:
                del self.fallback_store[job_id]
                return True
            return False
    
    async def job_exists(self, job_id: str) -> bool:
        """
        Check if job exists in Redis (or fallback store)
        
        Args:
            job_id: Unique job identifier
        
        Returns:
            True if job exists
        """
        try:
            if await self._check_redis_available():
                client = await get_redis_client()
                key = self._get_key(job_id)
                exists = await client.exists(key) > 0
                # Also check fallback
                if not exists:
                    exists = job_id in self.fallback_store
                return exists
            else:
                # Fallback to in-memory
                return job_id in self.fallback_store
        except Exception as e:
            print(f"❌ Error checking job {job_id} existence in Redis: {e}")
            # Fallback
            return job_id in self.fallback_store
    
    async def get_all_jobs(self, pattern: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
        """
        Get all jobs matching pattern (for debugging/admin purposes)
        
        Args:
            pattern: Optional pattern to match (e.g., "job:*")
        
        Returns:
            Dictionary mapping job_id to job_data
        """
        try:
            if await self._check_redis_available():
                client = await get_redis_client()
                search_pattern = pattern or f"{self.key_prefix}*"
                
                jobs = {}
                async for key in client.scan_iter(match=search_pattern):
                    job_id = key.replace(self.key_prefix, "")
                    job_data = await self.get_job(job_id)
                    if job_data:
                        jobs[job_id] = job_data
                
                # Also include fallback jobs
                for job_id, job_data in self.fallback_store.items():
                    if job_id not in jobs:
                        jobs[job_id] = job_data
                
                return jobs
            else:
                # Fallback to in-memory
                return self.fallback_store.copy()
        except Exception as e:
            print(f"❌ Error getting all jobs from Redis: {e}")
            # Fallback
            return self.fallback_store.copy()


# Global instances for different job types
# These will use in-memory fallback if Redis is unavailable
_extraction_jobs_fallback = {}
_migration_jobs_fallback = {}
_sql_tasks_fallback = {}
_assessment_jobs_fallback = {}
_metadata_sync_jobs_fallback = {}
_enterprise_graph_jobs_fallback = {}

extraction_job_store = RedisJobStore(
    key_prefix="extraction_job:", 
    ttl_seconds=DEFAULT_JOB_TTL,
    fallback_store=_extraction_jobs_fallback
)

migration_job_store = RedisJobStore(
    key_prefix="migration_job:", 
    ttl_seconds=DEFAULT_JOB_TTL,
    fallback_store=_migration_jobs_fallback
)

sql_task_store = RedisJobStore(
    key_prefix="sql_task:", 
    ttl_seconds=DEFAULT_JOB_TTL,
    fallback_store=_sql_tasks_fallback
)

assessment_job_store = RedisJobStore(
    key_prefix="assessment_job:",
    ttl_seconds=DEFAULT_JOB_TTL,
    fallback_store=_assessment_jobs_fallback,
)

metadata_sync_job_store = RedisJobStore(
    key_prefix="metadata_sync_job:",
    ttl_seconds=DEFAULT_JOB_TTL,
    fallback_store=_metadata_sync_jobs_fallback,
)

enterprise_graph_job_store = RedisJobStore(
    key_prefix="enterprise_graph_job:",
    ttl_seconds=DEFAULT_JOB_TTL,
    fallback_store=_enterprise_graph_jobs_fallback,
)
