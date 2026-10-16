"""Public contact-form endpoint."""

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from utils.email_utils import send_contact_form_email

router = APIRouter()


# Contact form models - Accept any data format
class ContactFormData(BaseModel):
    # Accept any fields dynamically
    class Config:
        extra = "allow"  # Allow any additional fields


# Contact form endpoint - OPEN (no authentication required)
@router.post("/contact-form")
async def submit_contact_form(form_data: ContactFormData):
    """
    Open endpoint to receive form submissions from archian.ai / marketing sites
    Accepts any form data format and sends everything to email
    """
    try:
        # Convert form data to dict
        form_dict = form_data.dict()
        
        # Determine form type from common field names
        form_type = "contact"  # default
        if "type" in form_dict:
            form_type = form_dict["type"]
        elif "form_type" in form_dict:
            form_type = form_dict["form_type"]
        elif "newsletter" in str(form_dict).lower() or "subscribe" in str(form_dict).lower():
            form_type = "newsletter"
        elif "sales" in str(form_dict).lower() or "purchase" in str(form_dict).lower():
            form_type = "sales"
        
        # Send email to founders@archian.ai with all form data
        await send_contact_form_email(
            form_type=form_type,
            form_data=form_dict
        )
        
        return JSONResponse(
            content={
                "status": "success",
                "message": "Thank you for your submission! We'll get back to you soon.",
                "form_type": form_type
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Contact form submission error: {e}")
        raise HTTPException(
            status_code=500, 
            detail="Failed to submit form. Please try again later."
        )
