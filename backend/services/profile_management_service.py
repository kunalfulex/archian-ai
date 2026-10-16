import os
from datetime import datetime
from typing import Dict, Any, Optional, Tuple
from db.connection import database
from dotenv import load_dotenv

load_dotenv()

class ProfileManagementService:
    
    @staticmethod
    async def get_user_profile(user_id: str) -> Optional[Dict[str, Any]]:
        """Get user profile information"""
        try:
            query = """
                SELECT 
                    id,
                    email,
                    first_name,
                    last_name,
                    company,
                    phone,
                    timezone,
                    is_super_admin,
                    created_at,
                    updated_at
                FROM users 
                WHERE id = :user_id
            """
            
            result = await database.fetch_one(query, {"user_id": user_id})
            
            if result:
                # Convert to dict and handle datetime serialization
                profile = dict(result)
                profile['is_super_admin'] = bool(profile.get('is_super_admin'))
                # Convert UUID to string for JSON serialization
                if 'id' in profile and profile['id']:
                    profile['id'] = str(profile['id'])
                profile['created_at'] = profile['created_at'].isoformat() if profile['created_at'] else None
                profile['updated_at'] = profile['updated_at'].isoformat() if profile['updated_at'] else None
                return profile
            
            return None
            
        except Exception as e:
            print(f"❌ Error getting user profile: {e}")
            return None
    
    @staticmethod
    async def update_user_profile(user_id: str, profile_data: Dict[str, Any]) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """Update user profile information"""
        try:
            # Validate required fields
            required_fields = ['first_name', 'last_name', 'email']
            for field in required_fields:
                if field not in profile_data or not profile_data[field]:
                    return False, f"{field.replace('_', ' ').title()} is required", None
            
            # Validate email format
            email = profile_data['email']
            if '@' not in email or '.' not in email:
                return False, "Invalid email format", None
            
            # Check if email already exists for another user
            email_check_query = """
                SELECT id FROM users 
                WHERE email = :email AND id != :user_id
            """
            existing_user = await database.fetch_one(email_check_query, {
                "email": email,
                "user_id": user_id
            })
            
            if existing_user:
                return False, "Email address already in use by another account", None
            
            # Get old timezone before updating (to check if it changed)
            old_profile = await database.fetch_one(
                "SELECT timezone FROM users WHERE id = :user_id",
                {"user_id": user_id}
            )
            old_timezone = dict(old_profile).get('timezone') if old_profile else None
            new_timezone = profile_data.get('timezone', 'Europe/London|London, UK')
            
            # Update user profile
            update_query = """
                UPDATE users 
                SET 
                    first_name = :first_name,
                    last_name = :last_name,
                    email = :email,
                    company = :company,
                    phone = :phone,
                    timezone = :timezone,
                    updated_at = :updated_at
                WHERE id = :user_id
                RETURNING 
                    id,
                    email,
                    first_name,
                    last_name,
                    company,
                    phone,
                    timezone,
                    created_at,
                    updated_at
            """
            
            update_data = {
                "user_id": user_id,
                "first_name": profile_data.get('first_name', '').strip(),
                "last_name": profile_data.get('last_name', '').strip(),
                "email": profile_data.get('email', '').strip().lower(),
                "company": profile_data.get('company', '').strip(),
                "phone": profile_data.get('phone', '').strip(),
                "timezone": profile_data.get('timezone', 'Europe/London|London, UK'),
                "updated_at": datetime.utcnow()
            }
            
            result = await database.fetch_one(update_query, update_data)
            
            if result:
                # If timezone was updated, update all existing schedules for this user
                # Only update schedules if timezone actually changed
                if old_timezone and old_timezone != new_timezone:
                    
                    # Update all enabled schedules for this user
                    from services.schedule_calculator import calculate_next_run
                    import json
                    
                    # Get all enabled schedules
                    schedules = await database.fetch_all(
                        """
                        SELECT id, schedule_frequency, schedule_config, schedule_timezone
                        FROM migration_flows
                        WHERE user_id = :user_id AND schedule_enabled = TRUE
                        """,
                        {"user_id": user_id}
                    )
                    
                    updated_count = 0
                    for schedule in schedules:
                        schedule_dict = dict(schedule)
                        flow_id = schedule_dict['id']
                        frequency = schedule_dict['schedule_frequency']
                        schedule_config = schedule_dict.get('schedule_config') or {}
                        
                        # Handle JSONB config (might be dict or string)
                        if isinstance(schedule_config, str):
                            try:
                                schedule_config = json.loads(schedule_config)
                            except:
                                schedule_config = {}
                        
                        # Recalculate next_run_at with new timezone
                        try:
                            next_run_at = calculate_next_run(
                                frequency=frequency,
                                schedule_config=schedule_config,
                                timezone=new_timezone,
                                current_time=datetime.utcnow()
                            )
                            
                            # Update schedule with new timezone and recalculated next_run_at
                            await database.execute(
                                """
                                UPDATE migration_flows
                                SET schedule_timezone = :new_timezone,
                                    next_run_at = :next_run_at,
                                    updated_at = NOW()
                                WHERE id = :flow_id AND user_id = :user_id
                                """,
                                {
                                    "flow_id": flow_id,
                                    "user_id": user_id,
                                    "new_timezone": new_timezone,
                                    "next_run_at": next_run_at
                                }
                            )
                            updated_count += 1
                            pass
                        except Exception as e:
                            pass
                
                # Convert to dict and handle datetime serialization
                updated_profile = dict(result)
                # Convert UUID to string for JSON serialization
                if 'id' in updated_profile and updated_profile['id']:
                    updated_profile['id'] = str(updated_profile['id'])
                updated_profile['created_at'] = updated_profile['created_at'].isoformat() if updated_profile['created_at'] else None
                updated_profile['updated_at'] = updated_profile['updated_at'].isoformat() if updated_profile['updated_at'] else None
                
                return True, "Profile updated successfully", updated_profile
            
            return False, "Failed to update profile", None
            
        except Exception as e:
            print(f"❌ Error updating user profile: {e}")
            return False, f"Error updating profile: {str(e)}", None
    
    @staticmethod
    async def get_available_timezones() -> dict:
        """
        Get list of available timezones organized by country.
        Returns a dictionary with countries as keys and lists of timezones as values.
        Format: {"Country": [{"value": "IANA_TIMEZONE|Display Name", "label": "Display Name"}, ...]}
        """
        timezone_list = [
            # North America - Pacific Time (DST: UTC-8/UTC-7)
            "America/Los_Angeles|Los Angeles, USA",
            "America/Vancouver|Vancouver, Canada",
            
            # North America - Mountain Time (DST: UTC-7/UTC-6, except Arizona)
            "America/Denver|Denver, USA",
            "America/Phoenix|Phoenix, USA (No DST)",
            
            # North America - Central Time (DST: UTC-6/UTC-5)
            "America/Chicago|Chicago, USA",
            "America/Mexico_City|Mexico City, Mexico",
            "America/Winnipeg|Winnipeg, Canada",
            
            # North America - Eastern Time (DST: UTC-5/UTC-4)
            "America/New_York|New York, USA",
            "America/Toronto|Toronto, Canada",
            
            # South America
            "America/Bogota|Bogota, Colombia",
            "America/Lima|Lima, Peru",
            "America/Sao_Paulo|São Paulo, Brazil",
            "America/Buenos_Aires|Buenos Aires, Argentina",
            
            # Europe - GMT/BST (DST: UTC+0/UTC+1)
            "Europe/London|London, UK",
            "Europe/Dublin|Dublin, Ireland",
            "Europe/Lisbon|Lisbon, Portugal",
            
            # Europe - Central European (DST: UTC+1/UTC+2)
            "Europe/Paris|Paris, France",
            "Europe/Berlin|Berlin, Germany",
            "Europe/Rome|Rome, Italy",
            "Europe/Madrid|Madrid, Spain",
            "Europe/Amsterdam|Amsterdam, Netherlands",
            "Europe/Brussels|Brussels, Belgium",
            "Europe/Stockholm|Stockholm, Sweden",
            "Europe/Vienna|Vienna, Austria",
            
            # Europe - Eastern European (DST: UTC+2/UTC+3)
            "Europe/Athens|Athens, Greece",
            "Europe/Helsinki|Helsinki, Finland",
            "Africa/Cairo|Cairo, Egypt",
            "Africa/Johannesburg|Johannesburg, South Africa",
            
            # Middle East & Eastern Europe
            "Europe/Moscow|Moscow, Russia",
            "Europe/Istanbul|Istanbul, Turkey",
            "Asia/Dubai|UAE",
            "Asia/Riyadh|Riyadh, Saudi Arabia",
            "Africa/Nairobi|Nairobi, Kenya",
            
            # Central Asia
            "Asia/Baku|Baku, Azerbaijan",
            "Indian/Mauritius|Mauritius",
            
            # South Asia
            "Asia/Karachi|Karachi, Pakistan",
            "Asia/Kolkata|India",
            
            # Southeast Asia
            "Asia/Dhaka|Dhaka, Bangladesh",
            "Asia/Almaty|Almaty, Kazakhstan",
            "Asia/Yangon|Yangon, Myanmar",
            "Asia/Bangkok|Bangkok, Thailand",
            "Asia/Jakarta|Jakarta, Indonesia",
            "Asia/Ho_Chi_Minh|Ho Chi Minh City, Vietnam",
            "Asia/Singapore|Singapore",
            
            # East Asia
            "Asia/Shanghai|China",
            "Asia/Hong_Kong|Hong Kong",
            "Asia/Taipei|Taipei, Taiwan",
            "Asia/Manila|Manila, Philippines",
            "Australia/Perth|Perth, Australia",
            "Asia/Kuala_Lumpur|Kuala Lumpur, Malaysia",
            
            # Japan & Korea
            "Asia/Tokyo|Tokyo, Japan",
            "Asia/Seoul|Seoul, South Korea",
            "Asia/Pyongyang|Pyongyang, North Korea",
            
            # Australia (various DST rules)
            "Australia/Adelaide|Adelaide, Australia",
            "Australia/Darwin|Darwin, Australia",
            "Australia/Sydney|Sydney, Australia",
            "Australia/Melbourne|Melbourne, Australia",
            "Australia/Brisbane|Brisbane, Australia (No DST)",
            "Australia/Canberra|Canberra, Australia",
            "Australia/Hobart|Hobart, Australia",
            
            # Pacific
            "Pacific/Guadalcanal|Solomon Islands",
            "Pacific/Noumea|Noumea, New Caledonia",
            "Pacific/Auckland|New Zealand",
            "Pacific/Fiji|Fiji",
            "Pacific/Honolulu|Honolulu, USA (No DST)"
        ]
        
        # Group timezones by country
        grouped = {}
        for tz_entry in timezone_list:
            if '|' in tz_entry:
                iana_tz, display_name = tz_entry.split('|', 1)
                display_name = display_name.strip()
                
                # Extract country name (last part after comma, or the whole name if no comma)
                if ',' in display_name:
                    country = display_name.split(',')[-1].strip()
                else:
                    country = display_name
                
                # Clean up country name (remove extra info in parentheses)
                if '(' in country:
                    country = country.split('(')[0].strip()
                
                if country not in grouped:
                    grouped[country] = []
                
                grouped[country].append({
                    "value": tz_entry,
                    "label": display_name
                })
        
        # Sort countries and timezones within each country
        sorted_grouped = {}
        for country in sorted(grouped.keys()):
            sorted_grouped[country] = sorted(grouped[country], key=lambda x: x['label'])
        
        return sorted_grouped
    
    @staticmethod
    async def validate_profile_data(profile_data: Dict[str, Any]) -> Tuple[bool, str]:
        """Validate profile data before update"""
        try:
            # Check required fields
            if not profile_data.get('first_name', '').strip():
                return False, "First name is required"
            
            if not profile_data.get('last_name', '').strip():
                return False, "Last name is required"
            
            if not profile_data.get('email', '').strip():
                return False, "Email is required"
            
            # Validate email format
            email = profile_data['email'].strip()
            if '@' not in email or '.' not in email:
                return False, "Invalid email format"
            
            # Validate phone number (basic validation)
            phone = profile_data.get('phone', '').strip()
            if phone and len(phone) < 10:
                return False, "Phone number must be at least 10 digits"
            
            # Validate timezone (supports both IANA format and legacy UTC offset format)
            timezone = profile_data.get('timezone', '')
            if timezone:
                # Check if it's IANA format (contains |) or legacy UTC format
                is_iana_format = '|' in timezone
                is_legacy_format = timezone.startswith('UTC')
                if not (is_iana_format or is_legacy_format):
                    # Try to validate as pure IANA timezone (without display name)
                    try:
                        import pytz
                        pytz.timezone(timezone)
                    except:
                        return False, "Invalid timezone format. Use IANA format (e.g., 'America/Los_Angeles|Los Angeles, USA') or legacy format (e.g., 'UTC-8 (Pacific Time)')"
            
            return True, "Validation passed"
            
        except Exception as e:
            print(f"❌ Error validating profile data: {e}")
            return False, f"Validation error: {str(e)}"
