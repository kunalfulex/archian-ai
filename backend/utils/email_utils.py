# src/utils/email_utils.py

import os
from typing import Optional

import httpx
from dotenv import load_dotenv

from utils.url_defaults import DEFAULT_FRONTEND_URL

load_dotenv()

POSTMARK_TOKEN = os.getenv("POSTMARK_API_TOKEN")
SMTP_SENDER = os.getenv("SMTP_SENDER", "login@yourdomain.com")  # fallback


def _support_email() -> str:
    """Operator-configured support address (SUPPORT_EMAIL or CONTACT_TO_EMAIL)."""
    return (
        (os.getenv("SUPPORT_EMAIL") or "").strip()
        or (os.getenv("CONTACT_TO_EMAIL") or "").strip()
    )


def _support_mailto_html() -> str:
    email = _support_email()
    if not email:
        return ""
    return (
        f'<p>Need help? Contact us at '
        f'<a href="mailto:{email}">{email}</a></p>'
    )


def _support_mailto_text() -> str:
    email = _support_email()
    if not email:
        return ""
    return f"Need help? Contact us at {email}"


# User-facing sign-off for product completion emails (signup, migration, connector ready).
EMAIL_SIGNATURE = "Archian AI Agent"
EMAIL_SIGNOFF_LINES = f"Thank you,\n{EMAIL_SIGNATURE}"
EMAIL_SIGNOFF_HTML = f"<p class=\"team\">— {EMAIL_SIGNATURE}</p>"

async def send_verification_email(to_email: str, code: str):
    payload = {
        "From": SMTP_SENDER,
        "To": to_email,
        "Subject": "Your Archian AI Login Code",
        "TextBody": f"Your Archian AI login code is: {code}",
    }
    headers = {
        "X-Postmark-Server-Token": POSTMARK_TOKEN,
        "Content-Type": "application/json"
    }
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.postmarkapp.com/email",
            headers=headers,
            json=payload
        )
        if response.status_code != 200:
            raise Exception(f"Postmark error: {response.text}")



async def send_forgot_password_email(to_email: str, reset_code: str):
    """Send password reset email to users"""
    payload = {
        "From": SMTP_SENDER,
        "To": to_email,
        "Subject": "Reset Your Archian AI Password",
        "TextBody": f"Your Archian AI password reset code is: {reset_code}",
    }
    headers = {
        "X-Postmark-Server-Token": POSTMARK_TOKEN,
        "Content-Type": "application/json"
    }
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.postmarkapp.com/email",
            headers=headers,
            json=payload
        )
        if response.status_code != 200:
            raise Exception(f"Postmark error: {response.text}")



async def send_welcome_email(to_email: str, first_name: str):
    """Send welcome email to new users"""
    # Use first name if provided, otherwise use a generic greeting
    greeting = f"Hi {first_name}!" if first_name else "Hi there!"
    
    # Get frontend URL from environment
    frontend_url = os.getenv('FRONTEND_URL', DEFAULT_FRONTEND_URL)
    
    # HTML email template
    html_body = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Welcome to Archian AI</title>
        <style>
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
                line-height: 1.6;
                color: #333;
                margin: 0;
                padding: 0;
                background-color: #f8fafc;
            }}
            .container {{
                max-width: 600px;
                margin: 0 auto;
                background-color: #ffffff;
                border-radius: 12px;
                overflow: hidden;
                box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
            }}
            .header {{
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                padding: 40px 30px;
                text-align: center;
                color: white;
            }}
            .logo {{
                width: 40px;
                height: 40px;
                margin: 0 auto 20px;
                display: flex;
                align-items: center;
                justify-content: center;
                background-color: white;
                border-radius: 50%;
                padding: 5px;
            }}
            .header h1 {{
                margin: 0;
                font-size: 28px;
                font-weight: 700;
            }}
            .header p {{
                margin: 10px 0 0;
                font-size: 16px;
                opacity: 0.9;
            }}
            .content {{
                padding: 40px 30px;
            }}
            .greeting {{
                font-size: 20px;
                font-weight: 600;
                color: #2d3748;
                margin-bottom: 20px;
            }}
            .welcome-message {{
                font-size: 18px;
                color: #4a5568;
                margin-bottom: 30px;
                text-align: center;
                padding: 20px;
                background-color: #f0fff4;
                border-radius: 8px;
                border-left: 4px solid #48bb78;
            }}
            .features {{
                margin: 30px 0;
            }}
            .features h3 {{
                font-size: 18px;
                font-weight: 600;
                color: #2d3748;
                margin-bottom: 15px;
                text-align: center;
            }}
            .features ul {{
                list-style: none;
                padding: 0;
                margin: 0;
            }}
            .features li {{
                padding: 10px 0;
                border-bottom: 1px solid #e2e8f0;
                font-size: 16px;
                color: #4a5568;
            }}
            .features li:before {{
                content: "✓";
                color: #48bb78;
                font-weight: bold;
                margin-right: 10px;
            }}
            .cta-button {{
                display: inline-block;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white!important;
                padding: 14px 28px;
                text-decoration: none;
                border-radius: 8px;
                font-weight: 600;
                font-size: 16px;
                margin: 20px 0;
                text-align: center;
                transition: transform 0.2s;
            }}
            .cta-button:hover {{
                transform: translateY(-2px);
            }}
            .support {{
                background-color: #f7fafc;
                padding: 20px;
                border-radius: 8px;
                margin-top: 30px;
                text-align: center;
            }}
            .support p {{
                margin: 0;
                font-size: 14px;
                color: #4a5568;
            }}
            .support a {{
                color: #667eea;
                text-decoration: none;
                font-weight: 600;
            }}
            .footer {{
                background-color: #2d3748;
                color: white;
                padding: 30px;
                text-align: center;
            }}
            .footer p {{
                margin: 0;
                font-size: 14px;
                opacity: 0.8;
            }}
            .footer .team {{
                font-weight: 600;
                margin-top: 10px;
            }}
            @media (max-width: 600px) {{
                .container {{
                    margin: 0;
                    border-radius: 0;
                }}
                .header, .content, .footer {{
                    padding: 20px;
                }}
                .header h1 {{
                    font-size: 24px;
                }}
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <div class="logo">
                    <img src="{frontend_url}/symbol.png" style="width: 40px; height: 40px;" alt="Archian AI">
                </div>
                <h1>Welcome to Archian AI!</h1>
                <p>Your data pipeline journey starts here</p>
            </div>
            
            <div class="content">
                <div class="greeting">{greeting}</div>
                
                <div class="welcome-message">
                    Welcome to Archian AI! We're excited to have you on board and help you build and run data pipelines across your connected systems.
                </div>
                
                <div class="features">
                    <h3>What you can do with Archian AI:</h3>
                    <ul>
                        <li>Upload and extract data from various file formats</li>
                        <li>Use our AI-powered field mapping to streamline your pipelines</li>
                        <li>Review and customize field mappings as needed</li>
                        <li>Run your pipeline — extract, transform, map, and migrate — with real-time progress</li>
                    </ul>
                </div>
                
                <div style="text-align: center;">
                    <a href="{frontend_url}/dashboard" class="cta-button">Get Started</a>
                </div>
                
                <div class="support">
                    {_support_mailto_html() or "<p>Need help? Reply to this email or contact your administrator.</p>"}
                </div>
            </div>
            
            <div class="footer">
                <p>Thank you for choosing Archian AI for your data pipeline needs.</p>
                <p class="team">— {EMAIL_SIGNATURE}</p>
            </div>
        </div>
    </body>
    </html>
    """
    
    # Plain text fallback
    text_body = f"""
{greeting}

Welcome to Archian AI! We're excited to have you on board and help you build and run data pipelines across your connected systems.

What you can do with Archian AI:
• Upload and extract data from various file formats
• Use our AI-powered field mapping to streamline your pipelines
• Review and customize field mappings as needed
• Run your pipeline — extract, transform, map, and migrate — with real-time progress

Get started: {frontend_url}/dashboard

{_support_mailto_text() or "Need help? Reply to this email or contact your administrator."}

Happy building!
{EMAIL_SIGNATURE}
    """
    
    payload = {
        "From": SMTP_SENDER,
        "To": to_email,
        "Subject": "🎉 Welcome to Archian AI - Let's Get Started!",
        "HtmlBody": html_body,
        "TextBody": text_body,
    }
    headers = {
        "X-Postmark-Server-Token": POSTMARK_TOKEN,
        "Content-Type": "application/json"
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.postmarkapp.com/email",
            headers=headers,
            json=payload
        )
        if response.status_code != 200:
            raise Exception(f"Postmark error: {response.text}")



async def send_categorization_complete_email(to_email: str):
    payload = {
        "From": SMTP_SENDER,
        "To": to_email,
        "Subject": "✅ Archian AI Successfully Connected to Your Salesforce Org",
        "TextBody": (
            "Hi there,\n\n"
            "Great news! Archian AI has successfully connected to your Salesforce org.\n"
            "You can now start migrating your data to Salesforce through Archian AI.\n\n"
            "Thank you for using Archian AI!"
        ),
    }
    headers = {
        "X-Postmark-Server-Token": POSTMARK_TOKEN,
        "Content-Type": "application/json"
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.postmarkapp.com/email",
            headers=headers,
            json=payload
        )
        if response.status_code != 200:
            raise Exception(f"Postmark error: {response.text}")


async def send_connector_agent_ready_email(
    to_email: str,
    connector_name: str,
    connector_type: str,
    *,
    needs_connector_setup: bool = False,
    setup_connections_path: Optional[str] = None,
):
    """Notify user that metadata sync, entity labeling, and connector profiling are complete."""
    import os

    from services.labeling.connector_setup import (
        build_setup_email_paragraph,
        build_setup_email_paragraph_html,
    )

    type_label = {
        "salesforce": "Salesforce org",
        "sql": "database",
        "google_sheets": "Google Sheets connection",
    }.get(connector_type, "connection")

    subject = f"✅ {connector_name} is connected — Archian AI is ready"
    body = (
        f"Hi there,\n\n"
        f"Great news — Archian AI has successfully connected to your {type_label} "
        f"\"{connector_name}\".\n\n"
        f"We've read the architecture of your application and mapped how your data is organized. "
        f"Your Archian AI agent is ready to help you extract data, build pipelines, migrate records, "
        f"and make your data work more easily.\n\n"
    )
    html_body = (
        f"<p>Hi there,</p>"
        f"<p>Great news — Archian AI has successfully connected to your {type_label} "
        f"\"{connector_name}\".</p>"
        f"<p>We've read the architecture of your application and mapped how your data is organized. "
        f"Your Archian AI agent is ready to help you extract data, build pipelines, migrate records, "
        f"and make your data work more easily.</p>"
    )
    if needs_connector_setup and connector_type == "salesforce" and setup_connections_path:
        frontend_url = os.getenv("FRONTEND_URL", DEFAULT_FRONTEND_URL).rstrip("/")
        setup_url = f"{frontend_url}{setup_connections_path}"
        body += build_setup_email_paragraph(setup_url=setup_url) + "\n"
        html_body += build_setup_email_paragraph_html(setup_url=setup_url)
    body += (
        f"Log in to Archian AI and describe what you want to do — we'll take it from there.\n\n"
        f"{EMAIL_SIGNOFF_LINES}"
    )
    html_body += (
        f"<p>Log in to Archian AI and describe what you want to do — we'll take it from there.</p>"
        f"{EMAIL_SIGNOFF_HTML}"
    )
    payload = {
        "From": SMTP_SENDER,
        "To": to_email,
        "Subject": subject,
        "TextBody": body,
        "HtmlBody": html_body,
    }
    headers = {
        "X-Postmark-Server-Token": POSTMARK_TOKEN,
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.postmarkapp.com/email",
            headers=headers,
            json=payload,
        )
        if response.status_code != 200:
            raise Exception(f"Postmark error: {response.text}")


async def send_database_sync_complete_email(to_email: str, connector_name: str, processed_tables: int, error: str = None):
    """Send email notification when database sync is complete"""
    if error:
        subject = f"Database Sync Failed - {connector_name}"
        body = f"""
        Hi there,
        
        We encountered an error while syncing your database "{connector_name}":
        
        Error: {error}
        
        Please check your database connection settings and try again.
        
        Best regards,
        Archian AI Team
        """
    else:
        subject = f"Database Sync Complete - {connector_name}"
        body = f"""
        Hi there,
        
        Great news! Archian AI has successfully connected to your database "{connector_name}".
        
        We've analyzed {processed_tables} tables and you can now extract and migrate data using simple natural language prompts.
        
        Examples:
        - "Extract customer data and migrate to Salesforce"
        - "Get all product information"
        - "Show me user accounts created last month"
        
        Log in to your Archian AI dashboard to get started!
        
        Best regards,
        Archian AI Team
        """
    
    payload = {
        "From": SMTP_SENDER,
        "To": to_email,
        "Subject": subject,
        "TextBody": body,
    }
    headers = {
        "X-Postmark-Server-Token": POSTMARK_TOKEN,
        "Content-Type": "application/json"
    }
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.postmarkapp.com/email",
            headers=headers,
            json=payload
        )
        if response.status_code != 200:
            print(f"Failed to send database sync email: {response.text}")


async def send_spreadsheet_sync_complete_email(
    to_email: str,
    spreadsheet_name: str,
    processed_sheets: int,
    error: str = None,
    *,
    reconnect_required: bool = False,
):
    """Send email notification when Google Spreadsheet sync is complete."""
    if error:
        if reconnect_required:
            subject = f"Reconnect Google Sheets — {spreadsheet_name}"
            body = f"""
        Hi there,

        We could not refresh access to your spreadsheet "{spreadsheet_name}" because your Google account connection has expired or was revoked.

        {error}

        After you reconnect, you can run Re-sync again from the Connections page.

        Best regards,
        Archian AI Team
        """
        else:
            subject = f"Spreadsheet Sync Failed - {spreadsheet_name}"
            body = f"""
        Hi there,
        
        We encountered an error while analyzing your spreadsheet "{spreadsheet_name}":
        
        {error}
        
        If the problem continues, try reconnecting Google Sheets on the Connections page or check that the spreadsheet is accessible.
        
        Best regards,
        Archian AI Team
        """
    else:
        subject = f"Spreadsheet Ready - {spreadsheet_name}"
        body = f"""
        Hi there,
        
        Great news! We've successfully analyzed your Google Spreadsheet "{spreadsheet_name}".
        
        ✅ Analyzed {processed_sheets} sheet(s)
        ✅ Ready for your data pipeline
        
        You can now:
        - Extract data from this spreadsheet to migrate to Salesforce
        - Use it as a destination for your data migrations
        - Ask questions like "migrate contacts from my database to this spreadsheet"
        
        Examples:
        - "Extract data from {spreadsheet_name} and migrate to Salesforce"
        - "Get contact information from the Contacts sheet"
        - "Migrate customer data to {spreadsheet_name}"
        
        Log in to your Archian AI dashboard to get started!
        
        Best regards,
        Archian AI Team
        """
    
    payload = {
        "From": SMTP_SENDER,
        "To": to_email,
        "Subject": subject,
        "TextBody": body,
    }
    headers = {
        "X-Postmark-Server-Token": POSTMARK_TOKEN,
        "Content-Type": "application/json"
    }
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.postmarkapp.com/email",
            headers=headers,
            json=payload
        )
        if response.status_code != 200:
            print(f"Failed to send database sync email: {response.text}")


async def send_export_ready_email(to_email: str, download_url: str, start_date: str, end_date: str):
    """Send email when migration data export is ready with download link"""
    subject = "Your migration data export is ready"
    text_body = f"""
    Hi there,

    Your migration data export ({start_date} to {end_date}) is ready.

    Download your file here (link expires after use):
    {download_url}

    If you did not request this export, you can ignore this email.

    Thank you,
    {EMAIL_SIGNATURE}
    """
    html_body = f"""
    <p>Hi there,</p>
    <p>Your migration data export ({start_date} to {end_date}) is ready.</p>
    <p><a href="{download_url}" style="color: #2563eb; text-decoration: underline;">Click here to download your file</a> (link expires after use)</p>
    <p>If you did not request this export, you can ignore this email.</p>
    {EMAIL_SIGNOFF_HTML}
    """
    payload = {
        "From": SMTP_SENDER,
        "To": to_email,
        "Subject": subject,
        "TextBody": text_body,
        "HtmlBody": html_body,
    }
    headers = {
        "X-Postmark-Server-Token": POSTMARK_TOKEN,
        "Content-Type": "application/json"
    }
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.postmarkapp.com/email",
            headers=headers,
            json=payload
        )
        if response.status_code != 200:
            print(f"Failed to send export ready email: {response.text}")


async def send_migration_export_ready_email(to_email: str, download_url: str, export_description: str):
    """Send email when migration results export is ready with download link (batch or Success/Failed page)."""
    subject = "Your migration export is ready"
    text_body = f"""
Hi there,

Your migration export ({export_description}) is ready.

Download your file here (link expires after first use):
{download_url}

If you did not request this export, you can ignore this email.

{EMAIL_SIGNOFF_LINES}
"""
    html_body = f"""
<p>Hi there,</p>
<p>Your migration export ({export_description}) is ready.</p>
<p><a href="{download_url}" style="color: #2563eb; text-decoration: underline;">Click here to download your file</a> (link expires after first use)</p>
<p>If you did not request this export, you can ignore this email.</p>
{EMAIL_SIGNOFF_HTML}
"""
    payload = {
        "From": SMTP_SENDER,
        "To": to_email,
        "Subject": subject,
        "TextBody": text_body,
        "HtmlBody": html_body,
    }
    headers = {
        "X-Postmark-Server-Token": POSTMARK_TOKEN,
        "Content-Type": "application/json"
    }
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.postmarkapp.com/email",
            headers=headers,
            json=payload
        )
        if response.status_code != 200:
            print(f"Failed to send migration export ready email: {response.text}")


async def send_migration_complete_email(
    to_email: str, 
    flow_id: str, 
    document_type: str, 
    record_count: int, 
    successful_count: int,
    skipped_count: int = 0,
    failed_count: int = 0,
    sync_outcome: str = None,
    database=None
):
    # Fetch flow details from database to get source and destination information
    flow_name = "Unknown Flow"
    source_label = "file"
    destination_label = "Salesforce"
    source_name = None  # Actual source name (e.g., "My Database", "users table")
    destination_name = None  # Actual destination name (e.g., "Production Org", "Dev Org 2")
    
    try:
        flow_result = await database.fetch_one(
            """
            SELECT 
                mf.migration_name,
                mf.destination_type,
                mf.org_id,
                es.extracted_schema_type,
                es.connector_id,
                es.table_name
            FROM migration_flows mf
            LEFT JOIN extracted_schemas es ON mf.id = es.migration_flow_id
            WHERE mf.id = :flow_id
            LIMIT 1
            """,
            {"flow_id": flow_id}
        )
        
        if flow_result:
            flow_data = dict(flow_result)
            flow_name = flow_data.get("migration_name", "Unknown Flow")
            source_type = (flow_data.get("extracted_schema_type") or "file").lower()
            destination_type = (flow_data.get("destination_type") or "salesforce").lower()
            
            # Get source name
            if source_type == "sql":
                # Try to get connector name and table name
                connector_id = flow_data.get("connector_id")
                table_name = flow_data.get("table_name")
                if connector_id:
                    try:
                        connector_result = await database.fetch_one(
                            "SELECT name FROM database_connectors WHERE id = :connector_id",
                            {"connector_id": connector_id}
                        )
                        if connector_result:
                            connector_name = dict(connector_result).get("name")
                            if table_name:
                                source_name = f"{connector_name} • {table_name}"
                            else:
                                source_name = connector_name
                    except:
                        pass
                if not source_name and table_name:
                    source_name = f"SQL database • {table_name}"
                if not source_name:
                    source_name = "SQL database"
            elif source_type == "gsheets":
                # Try to get spreadsheet name
                connector_id = flow_data.get("connector_id")
                if connector_id:
                    try:
                        connector_result = await database.fetch_one(
                            "SELECT name FROM gsheets_connectors WHERE id = :connector_id",
                            {"connector_id": connector_id}
                        )
                        if connector_result:
                            source_name = dict(connector_result).get("name")
                    except:
                        pass
                if not source_name:
                    source_name = "Google Sheets"
            elif source_type == "salesforce":
                connector_id = flow_data.get("connector_id")
                table_name_sf = flow_data.get("table_name")
                if connector_id:
                    try:
                        org_result = await database.fetch_one(
                            """
                            SELECT st.org_display_name
                            FROM salesforce_tokens st
                            INNER JOIN migration_flows mf ON mf.user_id = st.user_id
                            WHERE mf.id = CAST(:flow_id AS uuid)
                              AND st.id::text = :tid
                            LIMIT 1
                            """,
                            {"flow_id": flow_id, "tid": str(connector_id).strip()},
                        )
                        if org_result:
                            od = dict(org_result).get("org_display_name")
                            if od and table_name_sf:
                                source_name = f"{od} • {table_name_sf}"
                            elif od:
                                source_name = od
                            elif table_name_sf:
                                source_name = f"Salesforce • {table_name_sf}"
                    except Exception:
                        pass
                if not source_name:
                    if table_name_sf:
                        source_name = f"Salesforce • {table_name_sf}"
                    else:
                        source_name = "Salesforce"
            elif source_type == "file":
                source_name = "your file"
            else:
                source_name = f"{source_type}" if source_type else "your file"
            
            # Get destination name
            if destination_type == "salesforce":
                org_id = flow_data.get("org_id")
                if org_id:
                    try:
                        org_result = await database.fetch_one(
                            """
                            SELECT org_display_name 
                            FROM salesforce_tokens 
                            WHERE org_id = :org_id 
                            ORDER BY is_default DESC NULLS LAST, issued_at DESC 
                            LIMIT 1
                            """,
                            {"org_id": org_id}
                        )
                        if org_result:
                            destination_name = dict(org_result).get("org_display_name")
                    except:
                        pass
                if not destination_name:
                    destination_name = "Salesforce"
            elif destination_type == "google_sheets":
                destination_name = "Google Sheets"
            elif destination_type == "sql":
                destination_name = "SQL database"
            else:
                destination_name = "Salesforce"
            
            # Format labels for display
            source_labels = {
                "file": "file",
                "sql": "SQL database",
                "gsheets": "Google Sheets",
                "salesforce": "Salesforce"
            }
            
            destination_labels = {
                "salesforce": "Salesforce",
                "google_sheets": "Google Sheets",
                "sql": "SQL database"
            }
            
            source_label = source_labels.get(source_type, source_type)
            destination_label = destination_labels.get(destination_type, destination_type)
            
    except Exception as e:
        import traceback
        print(f"⚠️ Error fetching flow details for email: {e}")
        print(f"   Flow ID: {flow_id}")
        print(f"   Traceback: {traceback.format_exc()}")
    
    # Use actual names if available, otherwise use type labels
    source_display = source_name if source_name else source_label
    destination_display = destination_name if destination_name else destination_label
    
    # Determine subject based on results
    normalized_outcome = (sync_outcome or "").strip().lower()
    is_no_changes = (
        normalized_outcome == "no_changes"
        or (
            not normalized_outcome
            and int(successful_count or 0) == 0
            and int(failed_count or 0) == 0
            and int(skipped_count or 0) == 0
        )
    )

    if is_no_changes:
        subject = f"✅ Data Sync Complete: No New or Updated Records Found - {flow_name}"
    elif failed_count > 0 or skipped_count > 0:
        if skipped_count > 0 and failed_count > 0:
            subject = f"⚠️ Data Sync Complete: {successful_count} Synced, {skipped_count} Skipped, {failed_count} Failed - {flow_name}"
        elif skipped_count > 0:
            subject = f"✅ Data Sync Complete: {successful_count} Synced, {skipped_count} Skipped - {flow_name}"
        else:
            subject = f"⚠️ Data Sync Complete: {successful_count} Synced, {failed_count} Failed - {flow_name}"
    else:
        subject = f"✅ Data Sync Complete: All {successful_count} Records Synced - {flow_name}"
    
    # Build email body
    body_lines = [
        "Hi there,",
        "",
        f"Your data has been synced from {source_display} to {destination_display}!",
        "",
        "📊 Sync Details:",
        f"• Flow Name: {flow_name}",
        f"• Flow ID: {flow_id}",
        f"• Document Type: {document_type}",
        f"• Source: {source_display}",
        f"• Destination: {destination_display}",
        f"• Total Records: {record_count}",
        f"• Successfully Synced: {successful_count}",
    ]
    
    if skipped_count > 0:
        body_lines.append(f"• Skipped: {skipped_count} (records with missing required fields)")
    
    if failed_count > 0:
        body_lines.append(f"• Failed: {failed_count} (records that could not be synced)")
    
    body_lines.extend(
        [
            "",
            f"Your data has been synced from {source_display} to {destination_display} using the saved field mappings from your flow \"{flow_name}\".",
            "",
        ]
    )
    
    # Add appropriate message based on results
    if is_no_changes:
        body_lines.append("ℹ️ Sync completed successfully, but no new or updated records were found in this run.")
        body_lines.append("")
    elif failed_count > 0 or skipped_count > 0:
        messages = []
        if skipped_count > 0:
            messages.append(f"{skipped_count} record{'s were' if skipped_count > 1 else ' was'} skipped because required fields were missing.")
        if failed_count > 0:
            messages.append(f"{failed_count} record{'s failed' if failed_count > 1 else ' failed'} to sync. You can review the failed records in your Archian AI dashboard for details.")
        body_lines.append(f"⚠️  Note: {' '.join(messages)}")
        body_lines.append("")
    else:
        body_lines.append("✅ All records were successfully synced!")
        body_lines.append("")
    
    body_lines.extend(["", EMAIL_SIGNOFF_LINES])
    
    payload = {
        "From": SMTP_SENDER,
        "To": to_email,
        "Subject": subject,
        "TextBody": "\n".join(body_lines),
    }
    headers = {
        "X-Postmark-Server-Token": POSTMARK_TOKEN,
        "Content-Type": "application/json"
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.postmarkapp.com/email",
            headers=headers,
            json=payload
        )
        if response.status_code != 200:
            raise Exception(f"Postmark error: {response.text}")



async def send_subscription_change_email(to_email: str, first_name: str, old_plan: str, new_plan: str, plan_details: dict):
    """Send subscription change email to users when their plan is updated"""
    # Use first name if provided, otherwise use a generic greeting
    greeting = f"Hi {first_name}!" if first_name else "Hi there!"
    
    # Get frontend URL from environment
    frontend_url = os.getenv('FRONTEND_URL', DEFAULT_FRONTEND_URL)
    
    # Format plan details for display
    credits_per_month = plan_details.get('credits_per_month', 0)
    max_file_size = plan_details.get('max_file_size_mb', 0)
    price_monthly = plan_details.get('price_monthly', 0)
    price_yearly = plan_details.get('price_yearly', 0)
    features = plan_details.get('features', '')
    
    # Format limits for display
    credits_limit = "Unlimited" if credits_per_month == -1 else f"{credits_per_month:,}"
    file_size_limit = "Unlimited" if max_file_size == -1 else f"{max_file_size}MB"
    
    # Format pricing
    monthly_price = f"${price_monthly:.2f}/month" if price_monthly > 0 else "Free"
    yearly_price = f"${price_yearly:.2f}/year" if price_yearly > 0 else "Free"
    
    # Determine change type
    if old_plan == "free" and new_plan != "free":
        change_type = "upgrade"
        change_message = "Congratulations on upgrading your Archian AI plan!"
    elif old_plan != "free" and new_plan == "free":
        change_type = "downgrade"
        change_message = "Your Archian AI plan has been updated."
    else:
        change_type = "change"
        change_message = "Your Archian AI subscription has been updated."
    
    # HTML email template - Compact and Professional
    html_body = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Subscription Updated - Archian AI</title>
        <style>
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                line-height: 1.5;
                color: #1a202c;
                margin: 0;
                padding: 0;
                background-color: #f7fafc;
            }}
            .container {{
                max-width: 500px;
                margin: 20px auto;
                background-color: #ffffff;
                border-radius: 8px;
                overflow: hidden;
                box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
            }}
            .header {{
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                padding: 24px 20px;
                text-align: center;
                color: white;
            }}
            .logo {{
                width: 40px;
                height: 40px;
                margin: 0 auto 16px;
                display: block;
                background-color: white;
                border-radius: 50%;
                padding: 5px;
            }}
            .header h1 {{
                margin: 0;
                font-size: 22px;
                font-weight: 600;
            }}
            .header p {{
                margin: 8px 0 0;
                font-size: 14px;
                opacity: 0.9;
            }}
            .content {{
                padding: 24px 20px;
            }}
            .greeting {{
                font-size: 18px;
                font-weight: 600;
                color: #2d3748;
                margin-bottom: 16px;
            }}
            .change-message {{
                font-size: 14px;
                color: #4a5568;
                margin-bottom: 20px;
                padding: 12px 16px;
                background-color: #f0fff4;
                border-radius: 6px;
                border-left: 3px solid #48bb78;
            }}
            .plan-card {{
                background-color: #f8fafc;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
                padding: 20px;
                margin-bottom: 20px;
                text-align: center;
            }}
            .plan-title {{
                font-size: 12px;
                font-weight: 600;
                margin-bottom: 8px;
                text-transform: uppercase;
                letter-spacing: 0.5px;
                color: #48bb78;
            }}
            .plan-name {{
                font-size: 20px;
                font-weight: 700;
                margin-bottom: 8px;
                color: #2d3748;
            }}
            .plan-price {{
                font-size: 16px;
                font-weight: 600;
                color: #4a5568;
                margin-bottom: 12px;
            }}
            .plan-limits {{
                font-size: 13px;
                color: #718096;
                line-height: 1.4;
            }}
            .features-section {{
                margin: 20px 0;
            }}
            .features-title {{
                font-size: 14px;
                font-weight: 600;
                color: #2d3748;
                margin-bottom: 12px;
                text-align: center;
            }}
            .features-list {{
                background-color: #f7fafc;
                padding: 16px;
                border-radius: 6px;
                font-size: 13px;
                color: #4a5568;
            }}
            .features-list ul {{
                margin: 0;
                padding-left: 16px;
                text-align: left;
            }}
            .features-list li {{
                margin-bottom: 6px;
                position: relative;
            }}
            .features-list li:before {{
                content: "✓";
                color: #48bb78;
                font-weight: bold;
                position: absolute;
                left: -16px;
            }}
            .cta-button {{
                display: inline-block;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
                padding: 12px 24px;
                text-decoration: none;
                border-radius: 6px;
                font-weight: 600;
                font-size: 14px;
                margin: 16px 0;
                text-align: center;
            }}
            .support {{
                background-color: #f7fafc;
                padding: 16px;
                border-radius: 6px;
                margin-top: 20px;
                text-align: center;
                border-top: 1px solid #e2e8f0;
            }}
            .support p {{
                margin: 0;
                font-size: 13px;
                color: #718096;
            }}
            .support a {{
                color: #667eea;
                text-decoration: none;
                font-weight: 600;
            }}
            .footer {{
                background-color: #2d3748;
                color: white;
                padding: 20px;
                text-align: center;
            }}
            .footer p {{
                margin: 0;
                font-size: 12px;
                opacity: 0.8;
            }}
            .footer .team {{
                font-weight: 600;
                margin-top: 8px;
            }}
            @media (max-width: 600px) {{
                .container {{
                    margin: 10px;
                    border-radius: 6px;
                }}
                .header, .content, .footer {{
                    padding: 16px;
                }}
                .header h1 {{
                    font-size: 20px;
                }}
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <img src="{frontend_url}/symbol.png" alt="Archian AI" class="logo">
                <h1>Subscription Updated</h1>
                <p>Your Archian AI plan has been changed</p>
            </div>
            
            <div class="content" style="border: 0.5px solid grey;">
                <div class="greeting">{greeting}</div>
                
                <div class="change-message">
                    {change_message}
                </div>
                
                <div class="plan-card">
                    <div class="plan-title">Your New Plan</div>
                    <div class="plan-name">{new_plan.title()}</div>
                    <div class="plan-price">{monthly_price}</div>
                    <div class="plan-limits">
                        <div>Credits/month: {credits_limit}</div>
                        <div>File Size: {file_size_limit}</div>
                    </div>
                </div>
                
                {f'''
                <div class="features-section">
                    <div class="features-title">What's Included</div>
                    <div class="features-list">
                        {f'<ul style="margin: 0; padding-left: 16px; text-align: left;">' + ''.join([f'<li style="margin-bottom: 6px; color: #4a5568;">{str(f).strip()}</li>' for f in (features if isinstance(features, list) else [f.strip() for f in str(features).replace('[', '').replace(']', '').replace("'", '').split(',') if f.strip()]) if f and str(f).strip()]) + '</ul>' if features else 'No features listed'}
                    </div>
                </div>
                ''' if features else ''}
                
                <div style="text-align: center;">
                    <a href="{frontend_url}/dashboard" class="cta-button" style="color: #fff">Access Dashboard</a>
                </div>
                
                <div class="support">
                    {_support_mailto_html().replace("Need help?", "Questions?") or "<p>Questions? Contact your administrator.</p>"}
                </div>
            </div>
            
            <div class="footer">
                <p>Thank you for using Archian AI</p>
                <p class="team">— {EMAIL_SIGNATURE}</p>
            </div>
        </div>
    </body>
    </html>
    """
    
    # Plain text fallback
    text_body = f"""
{greeting}

{change_message}

Your subscription has been updated from {old_plan.title()} to {new_plan.title()}.

New Plan Details:
• Plan: {new_plan.title()}
• Price: {monthly_price}
• Credits per month: {credits_limit}
• File size limit: {file_size_limit}

{features if features else ''}

Access your dashboard: {frontend_url}/dashboard

{_support_mailto_text().replace("Need help?", "Questions?") or "Questions? Contact your administrator."}

The Archian AI Team
    """
    
    payload = {
        "From": SMTP_SENDER,
        "To": to_email,
        "Subject": "Subscription Updated - Archian AI",
        "HtmlBody": html_body,
        "TextBody": text_body,
    }
    headers = {
        "X-Postmark-Server-Token": POSTMARK_TOKEN,
        "Content-Type": "application/json"
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.postmarkapp.com/email",
            headers=headers,
            json=payload
        )
        if response.status_code != 200:
            raise Exception(f"Postmark error: {response.text}")


async def send_payment_failed_email(
    to_email: str,
    first_name: Optional[str],
    portal_url: str,
):
    """Notify user that a subscription payment failed and link to billing portal."""
    greeting = f"Hi {first_name}!" if first_name else "Hi there!"
    frontend_url = os.getenv("FRONTEND_URL", DEFAULT_FRONTEND_URL)
    billing_url = portal_url or f"{frontend_url}/settings"

    html_body = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head><meta charset="UTF-8"><title>Payment Failed - Archian AI</title></head>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; color: #1a202c; background: #f7fafc; margin: 0; padding: 20px;">
        <div style="max-width: 500px; margin: 0 auto; background: #fff; border-radius: 8px; padding: 24px; box-shadow: 0 2px 8px rgba(0,0,0,0.1);">
            <h1 style="font-size: 22px; margin: 0 0 16px;">Payment failed</h1>
            <p style="font-size: 15px; line-height: 1.5;">{greeting}</p>
            <p style="font-size: 15px; line-height: 1.5;">We couldn't process your latest Archian AI subscription payment. Your plan stays active while we retry billing, but please update your payment method to avoid interruption.</p>
            <p style="margin: 24px 0;"><a href="{billing_url}" style="display: inline-block; background: #667eea; color: #fff; text-decoration: none; padding: 12px 20px; border-radius: 6px; font-weight: 600;">Update payment method</a></p>
            {EMAIL_SIGNOFF_HTML}
        </div>
    </body>
    </html>
    """

    text_body = f"""{greeting}

We couldn't process your latest Archian AI subscription payment. Your plan stays active while we retry billing.

Update your payment method: {billing_url}

{EMAIL_SIGNOFF_LINES}
"""

    payload = {
        "From": SMTP_SENDER,
        "To": to_email,
        "Subject": "Action required: subscription payment failed - Archian AI",
        "HtmlBody": html_body,
        "TextBody": text_body,
    }
    headers = {
        "X-Postmark-Server-Token": POSTMARK_TOKEN,
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.postmarkapp.com/email",
            headers=headers,
            json=payload,
        )
        if response.status_code != 200:
            raise Exception(f"Postmark error: {response.text}")


async def send_subscription_lapsed_email(
    to_email: str,
    first_name: Optional[str],
    old_plan: str,
    plan_details: dict,
):
    """Notify user their paid subscription ended and they are on the free plan."""
    greeting = f"Hi {first_name}!" if first_name else "Hi there!"
    frontend_url = os.getenv("FRONTEND_URL", DEFAULT_FRONTEND_URL)
    credit_balance = plan_details.get("credit_balance")
    balance_line = (
        f"Your credit balance is now {int(credit_balance):,} credits."
        if credit_balance is not None
        else "Your paid-plan credits have been adjusted to your free plan allowance."
    )

    html_body = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head><meta charset="UTF-8"><title>Subscription ended - Archian AI</title></head>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; color: #1a202c; background: #f7fafc; margin: 0; padding: 20px;">
        <div style="max-width: 500px; margin: 0 auto; background: #fff; border-radius: 8px; padding: 24px; box-shadow: 0 2px 8px rgba(0,0,0,0.1);">
            <h1 style="font-size: 22px; margin: 0 0 16px;">Your subscription has ended</h1>
            <p style="font-size: 15px; line-height: 1.5;">{greeting}</p>
            <p style="font-size: 15px; line-height: 1.5;">Your {old_plan.title()} plan subscription has ended. Your account is now on the Free plan.</p>
            <p style="font-size: 15px; line-height: 1.5;">{balance_line} Free plan credits do not expire.</p>
            <p style="margin: 24px 0;"><a href="{frontend_url}/plans" style="display: inline-block; background: #667eea; color: #fff; text-decoration: none; padding: 12px 20px; border-radius: 6px; font-weight: 600;">View plans</a></p>
            {EMAIL_SIGNOFF_HTML}
        </div>
    </body>
    </html>
    """

    text_body = f"""{greeting}

Your {old_plan.title()} plan subscription has ended. Your account is now on the Free plan.
{balance_line} Free plan credits do not expire.

Resubscribe anytime: {frontend_url}/plans

{EMAIL_SIGNOFF_LINES}
"""

    payload = {
        "From": SMTP_SENDER,
        "To": to_email,
        "Subject": "Your Archian AI subscription has ended",
        "HtmlBody": html_body,
        "TextBody": text_body,
    }
    headers = {
        "X-Postmark-Server-Token": POSTMARK_TOKEN,
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.postmarkapp.com/email",
            headers=headers,
            json=payload,
        )
        if response.status_code != 200:
            raise Exception(f"Postmark error: {response.text}")


async def send_contact_form_email(form_type: str, form_data: dict):
    """Send contact form submission email to CONTACT_TO_EMAIL / SUPPORT_EMAIL."""
    
    # Format form type for display
    form_type_display = {
        "newsletter": "Newsletter Signup",
        "sales": "Sales Inquiry", 
        "contact": "Contact Form"
    }.get(form_type, form_type.title())
    
    # Generate dynamic form fields HTML
    form_fields_html = ""
    form_fields_text = ""
    
    for key, value in form_data.items():
        if value is not None and str(value).strip():  # Skip empty values
            # Format key for display (replace underscores with spaces, capitalize)
            display_key = key.replace('_', ' ').title()
            
            # HTML field
            form_fields_html += f"""
                    <div class="field">
                        <span class="field-label">{display_key}:</span>
                        <div class="field-value">{str(value)}</div>
                    </div>
            """
            
            # Text field
            form_fields_text += f"{display_key}: {str(value)}\n"
    
    # Get name and email for subject line (if available)
    name = form_data.get('name', form_data.get('full_name', 'Unknown'))
    email = form_data.get('email', 'No email provided')
    
    # HTML email template
    html_body = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>New {form_type_display} Submission - Archian AI</title>
        <style>
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                line-height: 1.6;
                color: #1a202c;
                margin: 0;
                padding: 0;
                background-color: #f7fafc;
            }}
            .container {{
                max-width: 600px;
                margin: 20px auto;
                background-color: #ffffff;
                border-radius: 8px;
                overflow: hidden;
                box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
            }}
            .header {{
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                padding: 24px 20px;
                text-align: center;
                color: white;
            }}
            .header h1 {{
                margin: 0;
                font-size: 22px;
                font-weight: 600;
            }}
            .content {{
                padding: 24px 20px;
            }}
            .form-info {{
                background-color: #f8fafc;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
                padding: 20px;
                margin-bottom: 20px;
            }}
            .form-info h3 {{
                margin: 0 0 16px 0;
                font-size: 18px;
                font-weight: 600;
                color: #2d3748;
            }}
            .field {{
                margin-bottom: 12px;
            }}
            .field-label {{
                font-weight: 600;
                color: #4a5568;
                font-size: 14px;
                margin-bottom: 4px;
                display: block;
            }}
            .field-value {{
                color: #2d3748;
                font-size: 14px;
                padding: 8px 12px;
                background-color: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 4px;
                white-space: pre-wrap;
                word-break: break-word;
            }}
            .footer {{
                background-color: #2d3748;
                color: white;
                padding: 20px;
                text-align: center;
            }}
            .footer p {{
                margin: 0;
                font-size: 12px;
                opacity: 0.8;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>New {form_type_display} Submission</h1>
            </div>
            
            <div class="content">
                <div class="form-info">
                    <h3>Form Data</h3>
                    {form_fields_html}
                </div>
            </div>
            
            <div class="footer">
                <p>This email was sent from the Archian AI website contact form</p>
            </div>
        </div>
    </body>
    </html>
    """
    
    # Plain text fallback
    text_body = f"""
New {form_type_display} Submission

Form Data:
{form_fields_text}
---
This email was sent from the Archian AI website contact form
    """
    
    contact_to = _support_email()
    if not contact_to:
        raise ValueError(
            "CONTACT_TO_EMAIL or SUPPORT_EMAIL must be set to receive contact form submissions"
        )

    payload = {
        "From": SMTP_SENDER,
        "To": contact_to,
        "Subject": f"New {form_type_display} Submission from {name}",
        "HtmlBody": html_body,
        "TextBody": text_body,
    }
    headers = {
        "X-Postmark-Server-Token": POSTMARK_TOKEN,
        "Content-Type": "application/json"
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.postmarkapp.com/email",
            headers=headers,
            json=payload
        )
        if response.status_code != 200:
            raise Exception(f"Postmark error: {response.text}")
