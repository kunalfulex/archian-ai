"""
Schedule Calculator Service
Calculates next_run_at timestamps for scheduled flows based on frequency and configuration.
"""

from datetime import datetime, timedelta
from typing import Dict, Any, Optional, Tuple
from utils.timezone_utils import get_timezone_object, parse_timezone_string
import pytz


def calculate_next_run(
    frequency: str,
    schedule_config: Dict[str, Any],
    timezone: str,
    current_time: Optional[datetime] = None
) -> datetime:
    """
    Calculate the next run time based on schedule configuration.
    
    Args:
        frequency: 'hourly', 'daily', 'monthly', 'yearly'
        schedule_config: Dict with keys like 'time_of_day', 'day_of_week', 'day_of_month'
        timezone: Timezone string (e.g., "UTC-8 (Pacific Time)")
        current_time: Current UTC datetime (defaults to now)
    
    Returns:
        Next run time as UTC datetime
    """
    if current_time is None:
        current_time = datetime.utcnow()
    
    # Ensure current_time is timezone-aware (UTC)
    if current_time.tzinfo is None:
        current_time = pytz.UTC.localize(current_time)
    else:
        current_time = current_time.astimezone(pytz.UTC)
    
    # Get timezone object (supports IANA timezones with DST or legacy UTC offset format)
    user_tz = get_timezone_object(timezone)
    
    # Convert current time to user timezone for calculations (DST-aware if IANA timezone)
    current_user_time = current_time.astimezone(user_tz)
    
    if frequency == 'every_15_minutes':
        next_run_user = current_user_time + timedelta(minutes=15)

    elif frequency == 'hourly':
        # For hourly: run at the specified minute of every hour (default minute 0)
        minute = schedule_config.get('minute', 0)
        
        # Ensure minute is valid
        if not (0 <= minute < 60):
            minute = 0
        
        # Set to current hour with specified minute
        next_run_user = current_user_time.replace(minute=minute, second=0, microsecond=0)
        
        # If this time is in the past (for the current hour), schedule for next hour
        # If it's exactly the current time or future, use it
        if next_run_user <= current_user_time:
            next_run_user += timedelta(hours=1)
    
    elif frequency == 'daily':
        # Daily at specified time
        time_of_day = schedule_config.get('time_of_day', '00:00')
        hour, minute = map(int, time_of_day.split(':'))
        
        next_run_user = current_user_time.replace(hour=hour, minute=minute, second=0, microsecond=0)
        
        # If the time has already passed today, schedule for tomorrow
        if next_run_user <= current_user_time:
            next_run_user += timedelta(days=1)
    
    elif frequency == 'monthly':
        # Monthly on specified day at specified time
        day_of_month = schedule_config.get('day_of_month', 1)
        time_of_day = schedule_config.get('time_of_day', '00:00')
        hour, minute = map(int, time_of_day.split(':'))
        
        
        # Helper function to get last day of a month
        def get_last_day_of_month(year, month):
            from calendar import monthrange
            return monthrange(year, month)[1]
        
        # Helper function to safely set day of month
        def safe_set_day(base_date, target_day):
            last_day = get_last_day_of_month(base_date.year, base_date.month)
            actual_day = min(target_day, last_day)  # Use last day if target_day exceeds month length
            return base_date.replace(day=actual_day)
        
        # First, try to set it for the current month
        current_year = current_user_time.year
        current_month = current_user_time.month
        current_day = current_user_time.day
        current_hour = current_user_time.hour
        current_minute = current_user_time.minute
        
        # Create scheduled time for current month
        try:
            current_month_scheduled = current_user_time.replace(day=day_of_month, hour=hour, minute=minute, second=0, microsecond=0)
        except ValueError:
            # Day doesn't exist in current month (e.g., Feb 31), use last day
            last_day = get_last_day_of_month(current_year, current_month)
            current_month_scheduled = current_user_time.replace(day=last_day, hour=hour, minute=minute, second=0, microsecond=0)
        
        
        # Check if we can schedule for current month
        # Case 1: Scheduled day is greater than current day -> use this month
        # Case 2: Scheduled day equals current day -> check if time hasn't passed yet
        # Case 3: Scheduled day is less than current day -> use next month
        # Case 4: Scheduled day doesn't exist (handled above) -> use last day and check if it's passed
        
        scheduled_day = current_month_scheduled.day  # This might be adjusted if target day doesn't exist
        
        if scheduled_day > current_day:
            # Scheduled day is in the future this month
            next_run_user = current_month_scheduled
        elif scheduled_day == current_day:
            # Same day - check if time has passed
            scheduled_time = (hour, minute)
            current_time_tuple = (current_hour, current_minute)
            
            if scheduled_time > current_time_tuple:
                # Time hasn't passed yet today, use this month
                next_run_user = current_month_scheduled
            else:
                # Time has passed, use next month
                if current_month == 12:
                    next_run_user = current_user_time.replace(year=current_year + 1, month=1, day=1, hour=hour, minute=minute, second=0, microsecond=0)
                else:
                    next_run_user = current_user_time.replace(month=current_month + 1, day=1, hour=hour, minute=minute, second=0, microsecond=0)
                
                # Set the target day in next month
                next_run_user = safe_set_day(next_run_user, day_of_month)
        else:
            # Scheduled day has already passed this month, use next month
            if current_month == 12:
                next_run_user = current_user_time.replace(year=current_year + 1, month=1, day=1, hour=hour, minute=minute, second=0, microsecond=0)
            else:
                next_run_user = current_user_time.replace(month=current_month + 1, day=1, hour=hour, minute=minute, second=0, microsecond=0)
            
            # Set the target day in next month
            next_run_user = safe_set_day(next_run_user, day_of_month)
        
    
    elif frequency == 'yearly':
        # Yearly on specified month/day at specified time
        day_of_month = schedule_config.get('day_of_month', 1)
        month = schedule_config.get('month', 1)  # 1-12
        time_of_day = schedule_config.get('time_of_day', '00:00')
        hour, minute = map(int, time_of_day.split(':'))
        
        # Start with current year
        next_run_user = current_user_time.replace(
            month=month, day=day_of_month, hour=hour, minute=minute, second=0, microsecond=0
        )
        
        # If the date has already passed this year, schedule for next year
        if next_run_user <= current_user_time:
            try:
                next_run_user = next_run_user.replace(year=next_run_user.year + 1, day=day_of_month)
            except ValueError:
                # Day doesn't exist (e.g., Feb 29 in non-leap year), try Feb 28
                next_run_user = next_run_user.replace(day=28)
                if next_run_user <= current_user_time:
                    next_run_user = next_run_user.replace(year=next_run_user.year + 1, day=day_of_month)
    else:
        raise ValueError(f"Unsupported frequency: {frequency}")
    
    # Convert back to UTC for storage
    next_run_utc = next_run_user.astimezone(pytz.UTC)
    
    
    # Return timezone-aware UTC datetime
    # PostgreSQL TIMESTAMPTZ expects timezone-aware datetime
    # We'll keep it timezone-aware and let PostgreSQL store it correctly
    return next_run_utc


def parse_time_of_day(time_str: str) -> tuple:
    """
    Parse time string "HH:MM" into (hour, minute) tuple.
    
    Args:
        time_str: Time string in "HH:MM" format
    
    Returns:
        Tuple of (hour, minute) as integers
    """
    try:
        hour, minute = map(int, time_str.split(':'))
        return hour, minute
    except (ValueError, AttributeError):
        raise ValueError(f"Invalid time format: {time_str}. Expected HH:MM")


def validate_schedule_config(frequency: str, schedule_config: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Validate schedule configuration.
    
    Args:
        frequency: Schedule frequency
        schedule_config: Schedule configuration dict
    
    Returns:
        Tuple of (is_valid, error_message)
    """
    if frequency not in ['every_15_minutes', 'hourly', 'daily', 'monthly', 'yearly']:
        return False, f"Invalid frequency: {frequency}"

    if frequency in ('every_15_minutes', 'hourly'):
        return True, ""

    if frequency in ['daily', 'monthly', 'yearly']:
        time_of_day = schedule_config.get('time_of_day')
        if not time_of_day:
            return False, "time_of_day is required for daily, monthly, and yearly schedules"
        
        try:
            hour, minute = parse_time_of_day(time_of_day)
            if not (0 <= hour < 24 and 0 <= minute < 60):
                return False, "Invalid time_of_day: hour must be 0-23, minute must be 0-59"
        except ValueError as e:
            return False, str(e)
    
    if frequency == 'monthly':
        day_of_month = schedule_config.get('day_of_month')
        if day_of_month is not None:
            if not (1 <= day_of_month <= 31):
                return False, "day_of_month must be between 1 and 31"
    
    if frequency == 'yearly':
        day_of_month = schedule_config.get('day_of_month')
        month = schedule_config.get('month')
        
        if day_of_month is not None and not (1 <= day_of_month <= 31):
            return False, "day_of_month must be between 1 and 31"
        
        if month is not None and not (1 <= month <= 12):
            return False, "month must be between 1 and 12"
    
    return True, ""

