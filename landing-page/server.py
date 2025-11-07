"""
SafeWatch AI Landing Page Server
Simple FastAPI server to handle waitlist form submissions
"""

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, EmailStr, field_validator
from typing import Optional
import json
import os
from datetime import datetime
import csv

app = FastAPI(title="SafeWatch AI Landing Page")

# Data storage file
WAITLIST_FILE = "waitlist_submissions.json"
CSV_FILE = "waitlist_submissions.csv"

# Email configuration
SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY", "")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@safewatch-ai.com")
FROM_EMAIL = os.environ.get("FROM_EMAIL", "hello@safewatch-ai.com")


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


def save_to_json(submission: dict):
    """Save submission to JSON file"""
    submissions = []

    # Load existing submissions
    if os.path.exists(WAITLIST_FILE):
        try:
            with open(WAITLIST_FILE, 'r') as f:
                submissions = json.load(f)
        except json.JSONDecodeError:
            submissions = []

    # Add new submission
    submissions.append(submission)

    # Save back to file
    with open(WAITLIST_FILE, 'w') as f:
        json.dump(submissions, f, indent=2)


def save_to_csv(submission: dict):
    """Save submission to CSV file"""
    file_exists = os.path.exists(CSV_FILE)

    with open(CSV_FILE, 'a', newline='') as f:
        fieldnames = ['timestamp', 'name', 'email', 'company', 'industry']
        writer = csv.DictWriter(f, fieldnames=fieldnames)

        # Write header if file is new
        if not file_exists:
            writer.writeheader()

        writer.writerow(submission)


def send_email(to_email: str, subject: str, html_content: str):
    """Send email using SendGrid"""
    if not SENDGRID_API_KEY:
        print("⚠️  SendGrid API key not configured. Set SENDGRID_API_KEY environment variable to enable emails.")
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
        print(f"✅ Email sent to {to_email} - Status: {response.status_code}")
        return response.status_code in [200, 201, 202]

    except ImportError:
        print("⚠️  SendGrid not installed. Run: pip install sendgrid")
        return False
    except Exception as e:
        print(f"❌ Error sending email: {e}")
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

    send_email(email, subject, html_content)


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

    send_email(ADMIN_EMAIL, subject, html_content)


@app.post("/api/waitlist")
async def join_waitlist(submission: WaitlistSubmission):
    """
    Handle waitlist form submissions
    """
    try:
        # Convert to dict
        submission_dict = submission.model_dump()

        # Check for duplicate email
        if os.path.exists(WAITLIST_FILE):
            with open(WAITLIST_FILE, 'r') as f:
                try:
                    existing = json.load(f)
                    emails = [s['email'] for s in existing]
                    if submission.email in emails:
                        raise HTTPException(
                            status_code=400,
                            detail="This email is already on the waitlist"
                        )
                except json.JSONDecodeError:
                    pass

        # Save to both JSON and CSV
        save_to_json(submission_dict)
        save_to_csv(submission_dict)

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
        raise HTTPException(
            status_code=500,
            detail=f"Failed to process submission: {str(e)}"
        )


@app.get("/api/waitlist/stats")
async def get_waitlist_stats():
    """
    Get basic waitlist statistics (for admin use)
    """
    if not os.path.exists(WAITLIST_FILE):
        return {
            "total_submissions": 0,
            "industries": {}
        }

    try:
        with open(WAITLIST_FILE, 'r') as f:
            submissions = json.load(f)

        # Count by industry
        industries = {}
        for sub in submissions:
            industry = sub.get('industry', 'Not specified')
            if not industry:
                industry = 'Not specified'
            industries[industry] = industries.get(industry, 0) + 1

        return {
            "total_submissions": len(submissions),
            "industries": industries,
            "latest_submission": submissions[-1]['timestamp'] if submissions else None
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve stats: {str(e)}"
        )


# Serve static files
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def read_root():
    """Serve the landing page"""
    return FileResponse("index.html")


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}


if __name__ == "__main__":
    import uvicorn

    print("=" * 60)
    print("SafeWatch AI Landing Page Server")
    print("=" * 60)
    print(f"Server starting at: http://localhost:8080")
    print(f"Waitlist data will be saved to: {WAITLIST_FILE}")
    print("=" * 60)

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8080,
        log_level="info"
    )
