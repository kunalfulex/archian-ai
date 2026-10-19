-- Migration: Credit-based billing – Phase 1 foundation
-- Purpose: Add tables and columns for credit balance, transactions, monthly usage, and plan credits.
-- Monthly credit usage is stored so users can see their usage (e.g. credits_used in usage_tracking).
-- Rollback is charged at per-unit rate: 0.5 per record, 0.75 per relationship, 2 per file.
-- Run this migration before implementing CreditService and deduct/refund logic.

-- =============================================================================
-- 1. Extend existing tables (new columns only)
-- =============================================================================

-- plan_limits: monthly credit allowance per plan (Free=500, Growth=55000, Enterprise=-1, PAYG=0)
ALTER TABLE plan_limits
ADD COLUMN IF NOT EXISTS credits_per_month INTEGER;

COMMENT ON COLUMN plan_limits.credits_per_month IS 'Credits granted per month for this plan. NULL = use legacy limits; -1 = unlimited.';

-- user_plans: billing cycle for subscription plans (monthly vs annual); NULL for free/PAYG
ALTER TABLE user_plans
ADD COLUMN IF NOT EXISTS billing_interval VARCHAR(10);

COMMENT ON COLUMN user_plans.billing_interval IS 'monthly | annual for subscription plans; NULL for free or pay_as_you_go.';

-- user_plans: remove billing_type (replaced by billing_interval + plan_type for PAYG)
ALTER TABLE user_plans
DROP COLUMN IF EXISTS billing_type;

-- usage_tracking: monthly credits used (for user-visible usage)
ALTER TABLE usage_tracking
ADD COLUMN IF NOT EXISTS credits_used INTEGER DEFAULT 0;

COMMENT ON COLUMN usage_tracking.credits_used IS 'Total credits deducted this month (for display in usage).';

-- =============================================================================
-- 2. New tables
-- =============================================================================

-- user_credit_balance: one row per user, current spendable balance
CREATE TABLE IF NOT EXISTS user_credit_balance (
    user_id UUID NOT NULL PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    balance INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE user_credit_balance IS 'Current credit balance per user.';

CREATE INDEX IF NOT EXISTS idx_user_credit_balance_updated_at
    ON user_credit_balance(updated_at);

-- credit_transactions: audit trail for every credit add/deduct
CREATE TABLE IF NOT EXISTS credit_transactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    amount INTEGER NOT NULL,
    type VARCHAR(30) NOT NULL,
    reference_type VARCHAR(50),
    reference_id VARCHAR(255),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON COLUMN credit_transactions.amount IS 'Positive = grant/purchase/refund, negative = deduct.';
COMMENT ON COLUMN credit_transactions.type IS 'grant_monthly, free_signup, purchase, deduct, refund';
COMMENT ON COLUMN credit_transactions.reference_type IS 'migration, cleanup, transformation, analysis, rollback, schema_analysis, etc.';

CREATE INDEX IF NOT EXISTS idx_credit_transactions_user_id
    ON credit_transactions(user_id);

CREATE INDEX IF NOT EXISTS idx_credit_transactions_created_at
    ON credit_transactions(created_at DESC);

CREATE UNIQUE INDEX IF NOT EXISTS idx_credit_transactions_deduct_idempotent
    ON credit_transactions(reference_type, reference_id)
    WHERE type = 'deduct';

-- =============================================================================
-- 3. Optional backfill: set credits_per_month for existing plan_limits rows
-- Free = 0 (no monthly refill; users get one-time 500 credits at signup only).
-- Paid plans = credits by tier (1000 credits ≈ $10; limits aligned to record counts).
-- Uncomment and run; adjust plan_type values to match your DB.
-- =============================================================================

-- UPDATE plan_limits SET credits_per_month = 0      WHERE plan_type = 'free';       -- one-time 500 at signup only
-- UPDATE plan_limits SET credits_per_month = 55000  WHERE plan_type = 'starter';   -- 50k records + buffer
-- UPDATE plan_limits SET credits_per_month = 165000 WHERE plan_type = 'pro';      -- 150k records + buffer
-- UPDATE plan_limits SET credits_per_month = -1    WHERE plan_type = 'enterprise'; -- unlimited
-- UPDATE plan_limits SET credits_per_month = 220000 WHERE plan_type = 'xTag enterprise';

-- =============================================================================
-- 4. Pay as you go plan (for Phase 3)
-- Only run if plan_limits has no row for pay_as_you_go (adjust columns to match your table).
-- =============================================================================

-- INSERT INTO plan_limits (plan_type, credits_per_month, price_monthly, is_standard_plan, max_records_per_month, max_file_size_mb, max_file_attachments_per_month, features)
-- VALUES ('pay_as_you_go', 0, 15, true, 0, 10, 0, '["Buy credits as you go","No monthly record limit","Same features as Growth"]')
-- ON CONFLICT (plan_type) DO NOTHING;

-- If pay_as_you_go row already exists, fix max file size to 10 MB for PAYG:
-- UPDATE plan_limits SET max_file_size_mb = 10 WHERE plan_type = 'pay_as_you_go';

-- =============================================================================
-- 5. Optional one-time backfill: grant current-period credits to existing subscribers
-- Only run AFTER enabling credits_per_month (section 3). Otherwise existing
-- subscribers would have 0 balance until their next Stripe renewal.
-- Run once after migration + plan_limits backfill.
-- =============================================================================

-- Grant credits to all active subscribers (add to balance and log transaction)
-- INSERT INTO user_credit_balance (user_id, balance, updated_at)
-- SELECT up.user_id, pl.credits_per_month, NOW()
-- FROM user_plans up
-- JOIN plan_limits pl ON pl.plan_type = up.plan_type
-- WHERE up.status = 'active' AND up.stripe_subscription_id IS NOT NULL
--   AND pl.credits_per_month IS NOT NULL AND pl.credits_per_month > 0
-- ON CONFLICT (user_id) DO UPDATE SET
--   balance = user_credit_balance.balance + EXCLUDED.balance,
--   updated_at = NOW();

-- INSERT INTO credit_transactions (user_id, amount, type, reference_type, reference_id)
-- SELECT up.user_id, pl.credits_per_month, 'grant_monthly', 'grant_monthly_backfill', up.user_id::text
-- FROM user_plans up
-- JOIN plan_limits pl ON pl.plan_type = up.plan_type
-- WHERE up.status = 'active' AND up.stripe_subscription_id IS NOT NULL
--   AND pl.credits_per_month IS NOT NULL AND pl.credits_per_month > 0;

-- =============================================================================
-- 6. PAYG subscription limits (only what user_plans does not have)
-- user_plans already has: user_id, stripe_subscription_id, stripe_customer_id,
-- billing_type, status, current_period_start, current_period_end.
-- This table stores only: amount and credits_per_month for the user's pack.
-- =============================================================================

CREATE TABLE IF NOT EXISTS user_payg_subscription (
    user_id UUID NOT NULL PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    amount_cents INTEGER NOT NULL,
    credits_per_month INTEGER NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE user_payg_subscription IS 'PAYG pack details per user: amount and credits per month. Subscription id, customer id, status live in user_plans.';
COMMENT ON COLUMN user_payg_subscription.amount_cents IS 'Price per month in cents, e.g. 5000 = $50. Stored in cents for precision and consistency with Stripe/payment APIs (avoids floating-point).';
COMMENT ON COLUMN user_payg_subscription.credits_per_month IS 'Credits granted each month for this pack, e.g. 1000.';
