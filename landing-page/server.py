"""
SafeWatch AI Landing Page Server
Simple FastAPI server to handle waitlist form submissions
"""

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, EmailStr, validator
from typing import Optional
import json
import os
from datetime import datetime
import csv

app = FastAPI(title="SafeWatch AI Landing Page")

# Data storage file
WAITLIST_FILE = "waitlist_submissions.json"
CSV_FILE = "waitlist_submissions.csv"


class WaitlistSubmission(BaseModel):
    """Waitlist form submission model"""
    name: str
    email: EmailStr
    company: Optional[str] = None
    industry: Optional[str] = None
    timestamp: str

    @validator('name')
    def name_not_empty(cls, v):
        if not v or v.strip() == "":
            raise ValueError('Name cannot be empty')
        return v.strip()

    @validator('company', 'industry')
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


@app.post("/api/waitlist")
async def join_waitlist(submission: WaitlistSubmission):
    """
    Handle waitlist form submissions
    """
    try:
        # Convert to dict
        submission_dict = submission.dict()

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

        return {
            "success": True,
            "message": "Successfully joined the waitlist!",
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
