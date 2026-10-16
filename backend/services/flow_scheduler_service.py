"""
Flow Scheduler Service
Background service that checks and executes scheduled flows using APScheduler.
"""

import asyncio
from datetime import datetime
from typing import Dict, Any, Optional
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from db.connection import database
from services.schedule_calculator import calculate_next_run
from uuid import UUID
import traceback


class FlowSchedulerService:
    """
    Service for managing and executing scheduled flows.
    
    Implements concurrency limiting to prevent resource exhaustion when many
    flows trigger simultaneously (e.g., 100+ SMB customers with scheduled syncs).
    """
    
    def __init__(self, max_concurrent_flows: int = 15):
        """
        Initialize the flow scheduler service.
        
        Args:
            max_concurrent_flows: Maximum number of flows that can execute concurrently.
                                 Default 15 allows for balanced throughput while leaving
                                 database connections available for frontend requests.
                                 Recommended: 10-20 based on DB connection pool size.
        """
        self.scheduler = AsyncIOScheduler()
        self.is_running = False
        # Semaphore to limit concurrent flow executions
        # This prevents database connection pool exhaustion when many flows trigger
        # simultaneously (e.g., 100 SMB customers with scheduled flows at top of hour)
        self.flow_semaphore = asyncio.Semaphore(max_concurrent_flows)
        self.max_concurrent_flows = max_concurrent_flows
    
    async def execute_scheduled_flow(self, flow_id: str, user_id: str):
        """
        Execute a scheduled flow by calling the reuse_flow logic.
        
        This function runs in a background task (via asyncio.create_task) with
        concurrency limiting via semaphore to prevent resource exhaustion when
        many flows trigger simultaneously.
        
        Args:
            flow_id: Flow ID to execute
            user_id: User ID who owns the flow
        """
        # Acquire semaphore permit - waits if max_concurrent_flows already running
        # This ensures we don't exhaust DB connection pool or overwhelm event loop
        # when many flows trigger at once (e.g., 100+ SMB customers)
        async with self.flow_semaphore:
            try:
                # Update schedule BEFORE execution starts to:
                # 1. Set last_run_at to when execution starts (not when it completes)
                # 2. Set next_run_at immediately to prevent duplicate executions
                # 3. Ensure schedule sync happens immediately (non-blocking for frontend)
                await self.update_flow_schedule_before_execution(flow_id, user_id)
                
                # Import here to avoid circular dependencies
                from routes.flows_reuse import reuse_flow_internal
                
                # Execute the flow in a fire-and-forget background task
                # reuse_flow_internal will create additional background tasks for actual work,
                # so we don't await it to avoid blocking this task or the database connection pool
                asyncio.create_task(
                    self._execute_flow_internal(flow_id, user_id)
                )
                
            except Exception as e:
                print(f"❌ Error executing scheduled flow {flow_id}: {e}")
                traceback.print_exc()
                # Don't disable the schedule on single failure, but log it
    
    async def _execute_flow_internal(self, flow_id: str, user_id: str):
        """
        Internal helper to execute the flow logic.
        This runs in a background task to ensure non-blocking execution.
        
        Args:
            flow_id: Flow ID to execute
            user_id: User ID who owns the flow
        """
        try:
            from routes.flows_reuse import reuse_flow_internal
            result = None
            try:
                from services.planning.flow_plan_executor import try_execute_scheduled_flow_via_planner

                result = await try_execute_scheduled_flow_via_planner(
                    flow_id, user_id, user_prompt="", trigger_type="scheduled"
                )
            except Exception as planner_exc:
                print(f"⚠️ Planner scheduled flow failed, falling back: {planner_exc}")
                result = None

            if result is None:
                from routes.flows_reuse import reuse_flow_internal

                result = await reuse_flow_internal(
                    flow_id, user_id, user_prompt="", trigger_type="scheduled"
                )
        except Exception as e:
            print(f"❌ Error in flow execution background task {flow_id}: {e}")
            traceback.print_exc()
    
    async def update_flow_schedule_before_execution(self, flow_id: str, user_id: str):
        """
        Update last_run_at and calculate next_run_at BEFORE flow execution starts.
        This ensures:
        - last_run_at reflects when the schedule was triggered (start time), not completion time
        - next_run_at is set immediately to prevent duplicate executions
        - Schedule sync happens immediately without blocking frontend requests
        
        Args:
            flow_id: Flow ID
            user_id: User ID
        """
        try:
            # Get flow schedule details
            flow = await database.fetch_one(
                """
                SELECT schedule_enabled, schedule_frequency, schedule_config, schedule_timezone
                FROM migration_flows
                WHERE id = :flow_id AND user_id = :user_id
                """,
                {"flow_id": flow_id, "user_id": user_id}
            )
            
            if not flow:
                return
            
            # Convert Record to dict for easier handling
            flow_dict = dict(flow) if flow else {}
            
            if not flow_dict.get("schedule_enabled"):
                return
            
            schedule_config = flow_dict.get("schedule_config") or {}
            frequency = flow_dict.get("schedule_frequency")
            timezone = flow_dict.get("schedule_timezone") or "America/Los_Angeles|Los Angeles, USA"
            
            # Handle JSONB config (might be dict or string)
            if isinstance(schedule_config, str):
                import json
                try:
                    schedule_config = json.loads(schedule_config)
                except:
                    schedule_config = {}
            
            # Calculate next run time based on current time (before execution starts)
            next_run_at = calculate_next_run(
                frequency=frequency,
                schedule_config=schedule_config,
                timezone=timezone,
                current_time=datetime.utcnow()
            )
            
            # Update database IMMEDIATELY before execution starts
            # This sets last_run_at to the start time (not completion time)
            # and sets next_run_at to prevent duplicate executions
            await database.execute(
                """
                UPDATE migration_flows
                SET last_run_at = NOW(),
                    next_run_at = :next_run_at,
                    updated_at = NOW()
                WHERE id = :flow_id AND user_id = :user_id
                """,
                {
                    "flow_id": flow_id,
                    "user_id": user_id,
                    "next_run_at": next_run_at
                }
            )
            
            
        except Exception as e:
            print(f"❌ Error updating flow schedule {flow_id}: {e}")
            traceback.print_exc()
    
    async def check_and_execute_scheduled_flows(self):
        """
        Check for flows that need to be executed and execute them.
        This runs periodically (every minute).
        """
        try:
            
            # First, let's see all scheduled flows and their next_run_at times for debugging
            all_scheduled = await database.fetch_all(
                """
                SELECT id, migration_name, schedule_enabled, next_run_at, schedule_frequency
                FROM migration_flows
                WHERE schedule_enabled = TRUE
                  AND next_run_at IS NOT NULL
                  AND parent_flow_id IS NULL
                ORDER BY next_run_at ASC
                """
            )
            
            
            # Query flows that are scheduled and due for execution
            # Get all scheduled flows first for debugging
            all_scheduled_flows = await database.fetch_all(
                """
                SELECT id, migration_name, schedule_frequency, schedule_config, schedule_timezone, next_run_at
                FROM migration_flows
                WHERE schedule_enabled = TRUE
                  AND next_run_at IS NOT NULL
                  AND parent_flow_id IS NULL
                ORDER BY next_run_at ASC
                """
            )
            
            
            # Now query for flows that are actually due
            # Compare at minute level (ignore seconds) since users don't specify seconds
            flows_to_execute = await database.fetch_all(
                """
                SELECT id, user_id, schedule_frequency, schedule_config, schedule_timezone, next_run_at
                FROM migration_flows
                WHERE schedule_enabled = TRUE
                  AND next_run_at IS NOT NULL
                  AND parent_flow_id IS NULL
                  AND date_trunc('minute', next_run_at) <= date_trunc('minute', NOW())
                ORDER BY next_run_at ASC
                """
            )
            
            if not flows_to_execute:
                return
            
            # Execute each flow
            for flow in flows_to_execute:
                # Convert Record to dict for easier handling
                flow_dict = dict(flow) if hasattr(flow, '_mapping') or hasattr(flow, 'keys') else flow
                flow_id = flow_dict["id"]
                user_id = str(flow_dict["user_id"])
                
                # Execute in background to avoid blocking
                asyncio.create_task(self.execute_scheduled_flow(flow_id, user_id))
                
        except Exception as e:
            print(f"❌ Error checking scheduled flows: {e}")
            traceback.print_exc()
    
    def start(self):
        """Start the scheduler service."""
        if self.is_running:
            return
        
        # Add periodic job to check for scheduled flows (every minute)
        # APScheduler AsyncIOScheduler can handle async functions directly
        self.scheduler.add_job(
            self.check_and_execute_scheduled_flows,
            trigger=IntervalTrigger(minutes=1),
            id='check_scheduled_flows',
            name='Check and execute scheduled flows',
            replace_existing=True,
            max_instances=1  # Prevent overlapping executions
        )
        
        self.scheduler.start()
        self.is_running = True
    
    def stop(self):
        """Stop the scheduler service."""
        if not self.is_running:
            return
        
        self.scheduler.shutdown()
        self.is_running = False
    
    def is_active(self) -> bool:
        """Check if scheduler is running."""
        return self.is_running and self.scheduler.running


# Global scheduler instance with concurrency limit of 15
# This allows 15 flows to execute concurrently while leaving DB connections
# available for frontend requests. Adjust based on your connection pool size.
flow_scheduler_service = FlowSchedulerService(max_concurrent_flows=15)

