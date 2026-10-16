-- One active Google Sheets connection per (user, google account email)

CREATE UNIQUE INDEX IF NOT EXISTS idx_google_sheets_connections_user_email_active
    ON google_sheets_connections (user_id, LOWER(TRIM(google_account_email)))
    WHERE is_active = true AND google_account_email IS NOT NULL;
