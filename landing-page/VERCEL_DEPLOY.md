# Deploying SafeWatch AI Landing Page to Vercel

This guide will walk you through deploying the SafeWatch AI landing page to Vercel with email notifications enabled.

## Prerequisites

1. **Vercel Account** - Sign up at https://vercel.com
2. **GitHub Repository** - Your code should be in a GitHub repo
3. **SendGrid Account** - Sign up at https://sendgrid.com (free tier available)
4. **Domain** (Optional) - Custom domain for your landing page

---

## Step 1: Set Up SendGrid

### 1.1 Create SendGrid Account

1. Go to https://sendgrid.com and sign up
2. Verify your email address
3. Complete the sender verification process

### 1.2 Create API Key

1. Go to Settings → API Keys
2. Click "Create API Key"
3. Name it: `safewatch-landing-page`
4. Select "Full Access" or "Restricted Access" with Mail Send permissions
5. Copy the API key (you won't see it again!)

### 1.3 Verify Sender Identity

**Option A: Single Sender Verification (Easiest)**
1. Go to Settings → Sender Authentication → Single Sender Verification
2. Add your email (e.g., hello@yourdomain.com)
3. Verify via email link

**Option B: Domain Authentication (Recommended for Production)**
1. Go to Settings → Sender Authentication → Domain Authentication
2. Add your domain
3. Add DNS records to your domain registrar
4. Wait for verification (can take up to 48 hours)

---

## Step 2: Prepare Your Repository

### 2.1 Update Configuration

Make sure your `landing-page` directory has these files:

```
landing-page/
├── index.html
├── vercel.json
├── requirements-vercel.txt
├── api/
│   └── index.py
└── static/
    └── js/
        └── app.js
```

### 2.2 Create .env.example

Create a file showing what environment variables are needed:

```bash
# landing-page/.env.example
SENDGRID_API_KEY=your_sendgrid_api_key_here
FROM_EMAIL=hello@yourdomain.com
ADMIN_EMAIL=admin@yourdomain.com
```

### 2.3 Update vercel.json (if needed)

If you want to use a different requirements file:

```json
{
  "version": 2,
  "builds": [
    {
      "src": "api/index.py",
      "use": "@vercel/python",
      "config": {
        "maxLambdaSize": "15mb"
      }
    }
  ],
  "routes": [
    {
      "src": "/api/(.*)",
      "dest": "api/index.py"
    },
    {
      "src": "/static/(.*)",
      "dest": "/static/$1"
    },
    {
      "src": "/(.*)",
      "dest": "/index.html"
    }
  ]
}
```

---

## Step 3: Deploy to Vercel

### Option A: Deploy via Vercel CLI (Recommended)

#### 3.1 Install Vercel CLI

```bash
npm install -g vercel
```

#### 3.2 Login to Vercel

```bash
vercel login
```

#### 3.3 Deploy from landing-page directory

```bash
cd landing-page
vercel
```

Follow the prompts:
- Set up and deploy? **Y**
- Which scope? Select your account
- Link to existing project? **N**
- What's your project's name? `safewatch-landing`
- In which directory is your code located? `./`

#### 3.4 Set Environment Variables

```bash
vercel env add SENDGRID_API_KEY
# Paste your SendGrid API key when prompted

vercel env add FROM_EMAIL
# Enter: hello@yourdomain.com

vercel env add ADMIN_EMAIL
# Enter: admin@yourdomain.com
```

Make sure to add them for all environments (Production, Preview, Development).

#### 3.5 Deploy to Production

```bash
vercel --prod
```

---

### Option B: Deploy via Vercel Dashboard

#### 3.1 Import Project

1. Go to https://vercel.com/dashboard
2. Click "Add New..." → "Project"
3. Import your GitHub repository
4. Select the repository

#### 3.2 Configure Project

- **Framework Preset:** Other
- **Root Directory:** `landing-page`
- **Build Command:** (leave empty)
- **Output Directory:** (leave empty)
- **Install Command:** `pip install -r requirements-vercel.txt`

#### 3.3 Add Environment Variables

Click "Environment Variables" and add:

| Key | Value | Environments |
|-----|-------|--------------|
| `SENDGRID_API_KEY` | Your SendGrid API key | Production, Preview, Development |
| `FROM_EMAIL` | hello@yourdomain.com | Production, Preview, Development |
| `ADMIN_EMAIL` | admin@yourdomain.com | Production, Preview, Development |

#### 3.4 Deploy

Click "Deploy" and wait for deployment to complete (usually 1-2 minutes).

---

## Step 4: Test Your Deployment

### 4.1 Visit Your Site

Vercel will give you a URL like: `https://safewatch-landing.vercel.app`

### 4.2 Test the Waitlist Form

1. Fill out the waitlist form
2. Submit
3. Check for:
   - Success message on the page
   - Welcome email in your inbox
   - Admin notification email

### 4.3 Check Logs

View deployment logs in Vercel dashboard:
1. Go to your project
2. Click "Deployments"
3. Click on latest deployment
4. View "Function Logs" to see email sending status

---

## Step 5: Add Custom Domain

### 5.1 Add Domain in Vercel

1. Go to your project settings
2. Click "Domains"
3. Add your domain (e.g., `safewatch-ai.com`)

### 5.2 Update DNS Records

Vercel will provide DNS records. Add to your domain registrar:

**For root domain (safewatch-ai.com):**
```
Type: A
Name: @
Value: 76.76.21.21
```

**For www subdomain:**
```
Type: CNAME
Name: www
Value: cname.vercel-dns.com
```

### 5.3 Update SendGrid Sender

If using domain authentication, make sure your FROM_EMAIL matches your verified domain.

---

## Step 6: Monitor and Optimize

### 6.1 Set Up Analytics

Add Google Analytics to `index.html`:

```html
<!-- Add to <head> section -->
<script async src="https://www.googletagmanager.com/gtag/js?id=GA_MEASUREMENT_ID"></script>
<script>
  window.dataLayer = window.dataLayer || [];
  function gtag(){dataLayer.push(arguments);}
  gtag('js', new Date());
  gtag('config', 'GA_MEASUREMENT_ID');
</script>
```

### 6.2 Monitor Email Deliverability

Check SendGrid dashboard for:
- Email delivery rates
- Bounce rates
- Open rates (if tracking is enabled)

### 6.3 Set Up Vercel Analytics

Enable Vercel Analytics in your project settings for:
- Page views
- Real User Metrics (RUM)
- Web Vitals

---

## Troubleshooting

### Emails Not Sending

**Check 1: API Key**
```bash
# View logs in Vercel
vercel logs safewatch-landing
```

Look for:
- ⚠️ SendGrid API key not configured
- ❌ Error sending email

**Check 2: Sender Verification**
- Make sure FROM_EMAIL is verified in SendGrid
- Check SendGrid dashboard for blocked emails

**Check 3: Environment Variables**
```bash
# List environment variables
vercel env ls
```

Make sure all three variables are set for Production.

### Function Timeout

If function times out, increase timeout in `vercel.json`:

```json
{
  "functions": {
    "api/index.py": {
      "maxDuration": 30
    }
  }
}
```

### CORS Issues

If you get CORS errors, update `api/index.py`:

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://yourdomain.com"],  # Specify your domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

### Build Failures

**Issue:** Requirements not installing

**Solution:** Create `requirements-vercel.txt` with exact versions:
```
fastapi==0.104.1
pydantic[email]==2.5.0
mangum==0.17.0
sendgrid==6.11.0
```

---

## Advanced Configuration

### Add Database (Vercel KV)

For production, use Vercel KV for data storage:

1. Enable Vercel KV in your project
2. Update `api/index.py`:

```python
from vercel_kv import KV

kv = KV()

@app.post("/api/waitlist")
async def join_waitlist(submission: WaitlistSubmission):
    # Store in KV
    await kv.set(f"waitlist:{submission.email}", submission.dict())
    await kv.sadd("waitlist:all", submission.email)
```

### Add Rate Limiting

Prevent spam with rate limiting:

```python
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

@app.post("/api/waitlist")
@limiter.limit("5/minute")
async def join_waitlist(submission: WaitlistSubmission):
    # ... existing code
```

### Add reCAPTCHA

1. Get reCAPTCHA keys from Google
2. Add to `index.html`:

```html
<script src="https://www.google.com/recaptcha/api.js"></script>
<div class="g-recaptcha" data-sitekey="YOUR_SITE_KEY"></div>
```

3. Verify in `api/index.py`:

```python
import requests

def verify_recaptcha(token):
    response = requests.post(
        'https://www.google.com/recaptcha/api/siteverify',
        data={
            'secret': RECAPTCHA_SECRET,
            'response': token
        }
    )
    return response.json()['success']
```

---

## Production Checklist

Before going live, verify:

- [ ] SendGrid API key configured
- [ ] Sender email verified in SendGrid
- [ ] Admin email set correctly
- [ ] Custom domain added and DNS configured
- [ ] SSL certificate active (automatic with Vercel)
- [ ] Test form submission works
- [ ] Welcome email received
- [ ] Admin notification received
- [ ] Google Analytics tracking code added
- [ ] Privacy policy page created
- [ ] Terms of service page created
- [ ] reCAPTCHA enabled (optional but recommended)
- [ ] Rate limiting configured
- [ ] Error monitoring set up (Sentry, LogRocket, etc.)

---

## Costs

**Vercel:**
- Hobby Plan: Free (100GB bandwidth, 100 builds/day)
- Pro Plan: $20/month (1TB bandwidth, unlimited builds)

**SendGrid:**
- Free Plan: 100 emails/day
- Essentials: $19.95/month (50,000 emails/month)
- Pro: $89.95/month (100,000 emails/month)

For a waitlist landing page, free tiers should be sufficient initially.

---

## Next Steps

1. **A/B Testing:** Create multiple landing page variants
2. **Email Sequences:** Set up drip campaigns in SendGrid
3. **CRM Integration:** Connect to HubSpot, Salesforce, etc.
4. **Chatbot:** Add Intercom or Drift for live chat
5. **Demo Videos:** Embed Loom or YouTube demos
6. **Blog:** Add content marketing section
7. **Case Studies:** Create customer success stories

---

## Support

- **Vercel Docs:** https://vercel.com/docs
- **SendGrid Docs:** https://docs.sendgrid.com
- **FastAPI Docs:** https://fastapi.tiangolo.com

For issues with this deployment, check the logs:
```bash
vercel logs safewatch-landing --follow
```
