"""
SafeWatch AI Landing Page - Vercel Serverless API
"""

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr, field_validator
from typing import Optional
import os
import json
from datetime import datetime
import boto3
from botocore.exceptions import ClientError

app = FastAPI(title="SafeWatch AI API")

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Environment variables
SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY", "")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@safewatch-ai.com")
FROM_EMAIL = os.environ.get("FROM_EMAIL", "hello@safewatch-ai.com")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")

# Use Vercel KV or DynamoDB for data storage in production
# For now, we'll use a simple JSON approach with proper error handling


class WaitlistSubmission(BaseModel):
    """Waitlist form submission model"""
    name: str
    email: EmailStr
    company: Optional[str] = None
    industry: Optional[str] = None
    timestamp: str

    @field_validator('name')
    @classmethod
    def name_not_empty(cls, v):
        if not v or v.strip() == "":
            raise ValueError('Name cannot be empty')
        return v.strip()

    @field_validator('company', 'industry')
    @classmethod
    def clean_optional_fields(cls, v):
        if v:
            return v.strip()
        return v


def send_email_sendgrid(to_email: str, subject: str, html_content: str):
    """Send email using SendGrid"""
    if not SENDGRID_API_KEY:
        print("SendGrid API key not configured, skipping email")
        return False

    try:
        import sendgrid
        from sendgrid.helpers.mail import Mail, Email, To, Content

        sg = sendgrid.SendGridAPIClient(api_key=SENDGRID_API_KEY)

        message = Mail(
            from_email=Email(FROM_EMAIL),
            to_emails=To(to_email),
            subject=subject,
            html_content=Content("text/html", html_content)
        )

        response = sg.send(message)
        return response.status_code in [200, 201, 202]

    except Exception as e:
        print(f"Error sending email via SendGrid: {e}")
        return False


def send_email_ses(to_email: str, subject: str, html_content: str):
    """Send email using AWS SES (fallback option)"""
    try:
        ses_client = boto3.client('ses', region_name=AWS_REGION)

        response = ses_client.send_email(
            Source=FROM_EMAIL,
            Destination={'ToAddresses': [to_email]},
            Message={
                'Subject': {'Data': subject},
                'Body': {'Html': {'Data': html_content}}
            }
        )
        return True
    except Exception as e:
        print(f"Error sending email via SES: {e}")
        return False


def send_welcome_email(name: str, email: str):
    """Send welcome email to new waitlist member"""
    subject = "Welcome to SafeWatch AI - You're on the Waitlist! 🎉"

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{
                font-family: 'Arial', sans-serif;
                line-height: 1.6;
                color: #333;
                max-width: 600px;
                margin: 0 auto;
                padding: 20px;
            }}
            .header {{
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
                padding: 30px;
                text-align: center;
                border-radius: 10px 10px 0 0;
            }}
            .content {{
                background: #f9f9f9;
                padding: 30px;
                border-radius: 0 0 10px 10px;
            }}
            .button {{
                display: inline-block;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
                padding: 15px 30px;
                text-decoration: none;
                border-radius: 5px;
                margin: 20px 0;
            }}
            .features {{
                background: white;
                padding: 20px;
                margin: 20px 0;
                border-radius: 5px;
                border-left: 4px solid #667eea;
            }}
            .footer {{
                text-align: center;
                color: #666;
                font-size: 12px;
                margin-top: 30px;
                padding-top: 20px;
                border-top: 1px solid #ddd;
            }}
        </style>
    </head>
    <body>
        <div class="header">
            <h1>🛡️ Welcome to SafeWatch AI!</h1>
        </div>
        <div class="content">
            <p>Hi {name},</p>

            <p>Thank you for joining the SafeWatch AI waitlist! We're excited to have you as one of our early adopters.</p>

            <div class="features">
                <h3>What's Next?</h3>
                <ul>
                    <li>✅ You're confirmed on our priority waitlist</li>
                    <li>🎁 Early access members get <strong>3 months free</strong></li>
                    <li>📧 We'll email you when we're ready to onboard</li>
                    <li>💬 You'll get exclusive updates on our progress</li>
                </ul>
            </div>

            <p><strong>Why SafeWatch AI?</strong></p>
            <ul>
                <li>🤖 AI-powered workplace safety monitoring</li>
                <li>⚡ Real-time PPE compliance detection</li>
                <li>📊 Automated OSHA reporting</li>
                <li>🎯 98% accuracy in hazard detection</li>
                <li>📱 Instant alerts via SMS, email, or webhook</li>
            </ul>

            <p>In the meantime, have questions? Just reply to this email - we'd love to hear from you!</p>

            <center>
                <a href="https://safewatch-ai.com" class="button">Learn More About SafeWatch AI</a>
            </center>

            <p>Best regards,<br>
            The SafeWatch AI Team</p>
        </div>

        <div class="footer">
            <p>SafeWatch AI - AI-Powered Workplace Safety Monitoring</p>
            <p>You're receiving this because you signed up at safewatch-ai.com</p>
        </div>
    </body>
    </html>
    """

    # Try SendGrid first, fallback to SES
    if not send_email_sendgrid(email, subject, html_content):
        send_email_ses(email, subject, html_content)


def send_admin_notification(submission: dict):
    """Send notification to admin about new signup"""
    subject = f"🎉 New SafeWatch AI Waitlist Signup: {submission['name']}"

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{
                font-family: 'Arial', sans-serif;
                line-height: 1.6;
                color: #333;
                max-width: 600px;
                margin: 0 auto;
                padding: 20px;
            }}
            .card {{
                background: #f9f9f9;
                padding: 20px;
                border-radius: 10px;
                border-left: 4px solid #667eea;
            }}
            .detail {{
                margin: 10px 0;
                padding: 10px;
                background: white;
                border-radius: 5px;
            }}
            .label {{
                font-weight: bold;
                color: #667eea;
            }}
        </style>
    </head>
    <body>
        <h2>New Waitlist Signup</h2>

        <div class="card">
            <div class="detail">
                <span class="label">Name:</span> {submission['name']}
            </div>
            <div class="detail">
                <span class="label">Email:</span> {submission['email']}
            </div>
            <div class="detail">
                <span class="label">Company:</span> {submission.get('company', 'Not provided')}
            </div>
            <div class="detail">
                <span class="label">Industry:</span> {submission.get('industry', 'Not specified')}
            </div>
            <div class="detail">
                <span class="label">Timestamp:</span> {submission['timestamp']}
            </div>
        </div>

        <p style="margin-top: 20px; color: #666; font-size: 14px;">
            This is an automated notification from SafeWatch AI landing page.
        </p>
    </body>
    </html>
    """

    if not send_email_sendgrid(ADMIN_EMAIL, subject, html_content):
        send_email_ses(ADMIN_EMAIL, subject, html_content)


# Simple in-memory storage for Vercel (for demo)
# In production, use Vercel KV, Redis, or database
_waitlist_cache = []


@app.post("/api/waitlist")
async def join_waitlist(submission: WaitlistSubmission):
    """
    Handle waitlist form submissions
    """
    try:
        submission_dict = submission.model_dump()

        # Check for duplicate email (in-memory for demo)
        if any(s['email'] == submission.email for s in _waitlist_cache):
            raise HTTPException(
                status_code=400,
                detail="This email is already on the waitlist"
            )

        # Store submission (in production, use proper database)
        _waitlist_cache.append(submission_dict)

        # Send welcome email to user
        try:
            send_welcome_email(submission.name, submission.email)
        except Exception as e:
            print(f"Failed to send welcome email: {e}")

        # Send notification to admin
        try:
            send_admin_notification(submission_dict)
        except Exception as e:
            print(f"Failed to send admin notification: {e}")

        return {
            "success": True,
            "message": "Successfully joined the waitlist! Check your email for confirmation.",
            "email": submission.email
        }

    except HTTPException:
        raise
    except Exception as e:
        print(f"Error processing submission: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to process submission: {str(e)}"
        )


@app.get("/api/waitlist/stats")
async def get_waitlist_stats():
    """
    Get basic waitlist statistics
    """
    try:
        # Count by industry
        industries = {}
        for sub in _waitlist_cache:
            industry = sub.get('industry', 'Not specified')
            if not industry:
                industry = 'Not specified'
            industries[industry] = industries.get(industry, 0) + 1

        return {
            "total_submissions": len(_waitlist_cache),
            "industries": industries,
            "latest_submission": _waitlist_cache[-1]['timestamp'] if _waitlist_cache else None
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve stats: {str(e)}"
        )


@app.get("/api/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "environment": "vercel",
        "email_configured": bool(SENDGRID_API_KEY)
    }


# Vercel serverless handler
from mangum import Mangum
handler = Mangum(app)
