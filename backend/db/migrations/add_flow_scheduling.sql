-- Migration: Add flow scheduling columns to migration_flows table
-- Purpose: Enable scheduled execution of flows at hourly, daily, monthly, or yearly intervals
-- Date: Flow Scheduling Feature Implementation

-- Add scheduling columns to migration_flows table
ALTER TABLE migration_flows
ADD COLUMN IF NOT EXISTS schedule_enabled BOOLEAN DEFAULT FALSE,
ADD COLUMN IF NOT EXISTS schedule_frequency VARCHAR(20),
ADD COLUMN IF NOT EXISTS schedule_config JSONB,
ADD COLUMN IF NOT EXISTS next_run_at TIMESTAMPTZ,
ADD COLUMN IF NOT EXISTS last_run_at TIMESTAMPTZ,
ADD COLUMN IF NOT EXISTS schedule_timezone VARCHAR(100);

-- Create index for efficient querying of scheduled flows
CREATE INDEX IF NOT EXISTS idx_migration_flows_schedule_enabled 
    ON migration_flows(schedule_enabled) 
    WHERE schedule_enabled = TRUE;

-- Create index for next_run_at to optimize scheduler queries
CREATE INDEX IF NOT EXISTS idx_migration_flows_next_run_at 
    ON migration_flows(next_run_at) 
    WHERE schedule_enabled = TRUE AND next_run_at IS NOT NULL;

-- Add comment to table documenting the scheduling feature
COMMENT ON COLUMN migration_flows.schedule_enabled IS 'Enable/disable scheduled execution for this flow';
COMMENT ON COLUMN migration_flows.schedule_frequency IS 'Schedule frequency: hourly, daily, monthly, yearly';
COMMENT ON COLUMN migration_flows.schedule_config IS 'JSONB config: {time_of_day, day_of_week, day_of_month, timezone}';
COMMENT ON COLUMN migration_flows.next_run_at IS 'Next scheduled execution time (UTC)';
COMMENT ON COLUMN migration_flows.last_run_at IS 'Last execution time (UTC)';
COMMENT ON COLUMN migration_flows.schedule_timezone IS 'Timezone for schedule (e.g., UTC-8 (Pacific Time)), defaults to user timezone';

