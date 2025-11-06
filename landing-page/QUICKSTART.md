# Quick Start Guide - SafeWatch AI Landing Page

Get your landing page running with email notifications in 5 minutes!

## Local Development (Without Email)

```bash
cd landing-page
pip install -r requirements.txt
python server.py
```

Visit: http://localhost:8080

> **Note:** Without SendGrid configured, emails won't be sent but the form will still work.

---

## Local Development (With Email Notifications)

### Step 1: Get SendGrid API Key

1. Sign up at https://sendgrid.com (free tier: 100 emails/day)
2. Go to Settings → API Keys
3. Create new API key with "Mail Send" permission
4. Copy the API key

### Step 2: Verify Sender Email

1. In SendGrid, go to Settings → Sender Authentication
2. Click "Verify a Single Sender"
3. Enter your email (e.g., hello@yourdomain.com)
4. Click verification link in your email

### Step 3: Configure Environment Variables

Create `.env` file:

```bash
cd landing-page
cp .env.example .env
```

Edit `.env`:
```bash
SENDGRID_API_KEY=SG.your_actual_api_key_here
FROM_EMAIL=hello@yourdomain.com  # Must match verified sender
ADMIN_EMAIL=admin@yourdomain.com
```

### Step 4: Run with Email Support

```bash
pip install -r requirements.txt
python server.py
```

### Step 5: Test

1. Visit http://localhost:8080
2. Fill out the waitlist form
3. Check your email for welcome message
4. Check admin email for notification

---

## Deploy to Vercel (Production)

### Prerequisites
- GitHub account
- Vercel account (free)
- SendGrid account (free)

### Quick Deploy

```bash
# Install Vercel CLI
npm install -g vercel

# Login
vercel login

# Deploy
cd landing-page
vercel

# Set environment variables
vercel env add SENDGRID_API_KEY
vercel env add FROM_EMAIL
vercel env add ADMIN_EMAIL

# Deploy to production
vercel --prod
```

Your site will be live at: `https://your-project.vercel.app`

> **Full deployment guide:** See [VERCEL_DEPLOY.md](VERCEL_DEPLOY.md)

---

## Troubleshooting

### Emails Not Sending Locally

**Check console output:**
```
⚠️  SendGrid API key not configured
```
→ Set SENDGRID_API_KEY in .env

```
❌ Error sending email: The from address does not match a verified Sender Identity
```
→ Verify your FROM_EMAIL in SendGrid dashboard

### Form Not Working

**CORS Error:**
- Make sure you're accessing via http://localhost:8080, not file://

**API Endpoint Not Found:**
- Check that server.py is running
- Look for "Uvicorn running on http://0.0.0.0:8080"

---

## What Gets Sent

### Welcome Email to User
- Subject: "Welcome to SafeWatch AI - You're on the Waitlist! 🎉"
- Beautiful HTML email with:
  - Confirmation of waitlist signup
  - Early access offer (3 months free)
  - Feature highlights
  - Call-to-action button

### Admin Notification
- Subject: "🎉 New SafeWatch AI Waitlist Signup: [Name]"
- Includes:
  - Name
  - Email
  - Company
  - Industry
  - Timestamp

---

## Testing Email Templates

Want to see what the emails look like? Create a test route:

```python
# Add to server.py
@app.get("/test-email")
async def test_email():
    send_welcome_email("Test User", "your-email@example.com")
    return {"message": "Test email sent"}
```

Visit: http://localhost:8080/test-email

---

## Next Steps

1. **Customize Branding**
   - Edit "SafeWatch AI" in index.html
   - Change color scheme (search for #667eea)
   - Add your logo

2. **Add Custom Domain**
   - Buy domain (Namecheap, Google Domains)
   - Add to Vercel project
   - Update DNS records

3. **Set Up Analytics**
   - Google Analytics
   - Facebook Pixel
   - LinkedIn Insight Tag

4. **Email Campaigns**
   - Create drip campaigns in SendGrid
   - Segment by industry
   - A/B test subject lines

5. **CRM Integration**
   - Export CSV to HubSpot/Salesforce
   - Set up Zapier automation
   - Create deal pipeline

---

## Cost Breakdown

### Free Tier (0-100 signups/day)
- ✅ Vercel: Free (Hobby Plan)
- ✅ SendGrid: Free (100 emails/day)
- ✅ Domain: ~$12/year
- **Total: $12/year**

### Growing (100-500 signups/day)
- Vercel: Free (or $20/mo for Pro)
- SendGrid: $19.95/mo (50k emails/month)
- Domain: $12/year
- **Total: ~$20-40/month**

---

## Support

- **Documentation:** See README.md and VERCEL_DEPLOY.md
- **Logs:** Check console output when running locally
- **Vercel Logs:** `vercel logs --follow`
- **SendGrid Activity:** Check SendGrid dashboard

---

## Pro Tips

1. **Test Before Launch**
   - Use your personal email first
   - Check spam folders
   - Test on mobile devices

2. **Monitor Deliverability**
   - Keep SendGrid dashboard open
   - Check bounce rates
   - Watch for spam complaints

3. **Grow Your List**
   - Share on social media
   - Run ads to landing page
   - Partner with influencers
   - Add to email signature

4. **Engage Your Waitlist**
   - Send weekly updates
   - Share behind-the-scenes
   - Offer exclusive perks
   - Ask for feedback

---

Ready to launch? Follow the steps above and you'll be collecting leads in minutes! 🚀
