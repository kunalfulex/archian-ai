from databases import Database
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# PostgreSQL database connection URL
DATABASE_URL = os.getenv("DB_URL")

# Database connection pool configuration
# These settings allow handling 100+ concurrent users
# min_size: Minimum connections kept alive (reduces connection overhead)
# max_size: Maximum concurrent connections (critical for scalability)
DB_POOL_MIN_SIZE = int(os.getenv("DB_POOL_MIN_SIZE", "10"))
DB_POOL_MAX_SIZE = int(os.getenv("DB_POOL_MAX_SIZE", "100"))

# Initialize database connection with pool configuration
if DATABASE_URL:
    database = Database(
        DATABASE_URL,
        min_size=DB_POOL_MIN_SIZE,
        max_size=DB_POOL_MAX_SIZE
    )
else:
    # Fallback for development
    DATABASE_URL = "postgresql://edison_dev_user:edison_dev_user@localhost/edison_dev_db"
    database = Database(
        DATABASE_URL,
        min_size=DB_POOL_MIN_SIZE,
        max_size=DB_POOL_MAX_SIZE
    )

# Dedicated small pool for background export tasks so long export SELECTs
# don't tie up the main pool and block other requests
EXPORT_POOL_MIN = int(os.getenv("DB_EXPORT_POOL_MIN", "1"))
EXPORT_POOL_MAX = int(os.getenv("DB_EXPORT_POOL_MAX", "5"))
export_database = Database(
    DATABASE_URL,
    min_size=EXPORT_POOL_MIN,
    max_size=EXPORT_POOL_MAX
)
