# 🚀 Deploy SafeWatch AI Landing Page to Vercel NOW

**Estimated Time: 10-15 minutes**

Follow this guide to get your landing page live on the internet!

---

## ✅ Pre-Deployment Checklist

Before you start, make sure you have:

- [ ] GitHub account (free)
- [ ] Vercel account (free) - Sign up at https://vercel.com
- [ ] Email address for SendGrid (free tier: 100 emails/day)
- [ ] This repository pushed to GitHub

---

## 🎯 OPTION 1: Deploy via Vercel Dashboard (RECOMMENDED - 10 minutes)

This is the **easiest and fastest** method!

### Step 1: Create Vercel Account (2 minutes)

1. Go to https://vercel.com
2. Click **"Sign Up"**
3. Choose **"Continue with GitHub"**
4. Authorize Vercel to access your GitHub account

### Step 2: Import Your Project (1 minute)

1. After logging in, you'll see the Vercel dashboard
2. Click **"Add New..."** → **"Project"**
3. You'll see a list of your GitHub repositories
4. Find **"Peter07odia/video-search-and-summarization"**
5. Click **"Import"**

### Step 3: Configure Build Settings (2 minutes)

On the configuration screen, enter:

```
Framework Preset: Other
Root Directory: landing-page
Build Command: (leave empty)
Output Directory: (leave empty)
Install Command: pip install -r requirements-vercel.txt
```

**Important:** Make sure "Root Directory" is set to `landing-page`!

### Step 4: Add Environment Variables (3 minutes)

Click **"Environment Variables"** section and add these three variables:

#### Variable 1: SENDGRID_API_KEY
```
Key: SENDGRID_API_KEY
Value: [We'll get this in Step 5 - leave blank for now]
Environments: ✅ Production ✅ Preview ✅ Development
```

#### Variable 2: FROM_EMAIL
```
Key: FROM_EMAIL
Value: hello@safewatch-ai.com (or your email)
Environments: ✅ Production ✅ Preview ✅ Development
```

#### Variable 3: ADMIN_EMAIL
```
Key: ADMIN_EMAIL
Value: your-email@example.com (where you want notifications)
Environments: ✅ Production ✅ Preview ✅ Development
```

**Note:** You can add SENDGRID_API_KEY later. Skip it for now if you want to deploy first!

### Step 5: Deploy! (2 minutes)

1. Click **"Deploy"**
2. Wait for the build to complete (usually 1-2 minutes)
3. You'll see: ✅ **"Congratulations! Your project has been deployed."**
4. Click on the deployment URL (e.g., `https://safewatch-landing-xyz.vercel.app`)
5. **Your landing page is now LIVE!** 🎉

---

## 📧 Set Up Email Notifications (5-10 minutes)

Now let's enable email notifications so users get welcome emails!

### Step 1: Create SendGrid Account (3 minutes)

1. Go to https://sendgrid.com
2. Click **"Start for Free"**
3. Fill out the form:
   - Email: your-email@example.com
   - Password: (create strong password)
   - Company: SafeWatch AI (or your company)
4. Verify your email address

### Step 2: Get API Key (2 minutes)

1. After logging in, go to **Settings** → **API Keys**
2. Click **"Create API Key"**
3. Name: `safewatch-landing-page`
4. Permission: Select **"Restricted Access"**
5. Enable only: **Mail Send** → **Mail Send** (turn on)
6. Click **"Create & View"**
7. **COPY THE API KEY** (you won't see it again!)
   - It looks like: `SG.xxxxxxxxxxxxxx...`

### Step 3: Verify Sender Email (2 minutes)

1. In SendGrid, go to **Settings** → **Sender Authentication**
2. Click **"Verify a Single Sender"**
3. Fill out the form:
   - From Name: SafeWatch AI
   - From Email: hello@yourdomain.com (must match your FROM_EMAIL in Vercel)
   - Reply To: Same as From Email
   - Address, City, etc.: Your information
4. Click **"Create"**
5. **Check your email** and click the verification link
6. ✅ Your sender email is now verified!

### Step 4: Add API Key to Vercel (1 minute)

1. Go back to Vercel dashboard
2. Click on your project
3. Go to **Settings** → **Environment Variables**
4. Find `SENDGRID_API_KEY`
5. Click **"Edit"**
6. Paste your SendGrid API key
7. Click **"Save"**
8. Click **"Redeploy"** to apply changes

---

## 🧪 Test Your Deployment

### Test 1: Visit Your Site

1. Go to your Vercel URL (e.g., `https://safewatch-landing-xyz.vercel.app`)
2. You should see the beautiful landing page!
3. Scroll through and check:
   - ✅ Hero section loads
   - ✅ Features section displays
   - ✅ Pricing cards show correctly
   - ✅ Waitlist form is visible

### Test 2: Submit Waitlist Form

1. Scroll to **"Join the Waitlist"** section
2. Fill out the form:
   - Name: Test User
   - Email: **your-email@example.com** (use YOUR real email)
   - Company: Test Company
   - Industry: Warehouse
3. Click **"Join the Waitlist"**
4. You should see: ✅ **"Success! You've been added to the waitlist."**

### Test 3: Check Your Email

Within 1-2 minutes, you should receive:

1. **Welcome Email** at the email you entered
   - Subject: "Welcome to SafeWatch AI - You're on the Waitlist! 🎉"
   - Beautiful HTML email with features and CTA

2. **Admin Notification** at your ADMIN_EMAIL
   - Subject: "🎉 New SafeWatch AI Waitlist Signup: Test User"
   - Shows all form details

**If you don't see emails:**
- Check your spam folder
- Verify SendGrid API key is correct
- Make sure FROM_EMAIL is verified in SendGrid
- Check Vercel function logs (Settings → Functions → View Logs)

---

## 🎨 Customize Your Deployment

### Change the Domain

Your default URL is something like `safewatch-landing-xyz.vercel.app`. To use a custom domain:

1. Buy a domain (Namecheap, Google Domains, etc.)
2. In Vercel, go to **Settings** → **Domains**
3. Click **"Add"**
4. Enter your domain: `safewatch-ai.com`
5. Follow the DNS configuration instructions
6. Wait ~1 hour for propagation
7. ✅ Your site is now at your custom domain!

### Update Branding

1. Clone your repo locally
2. Edit `landing-page/index.html`:
   - Change "SafeWatch AI" to your product name
   - Update colors (search for `#667eea` and `#764ba2`)
   - Add your logo image
3. Commit and push to GitHub
4. Vercel will **automatically redeploy** (takes ~1 minute)

---

## 📊 Monitor Your Landing Page

### View Analytics

1. In Vercel, go to **Analytics** tab
2. See:
   - Page views
   - Unique visitors
   - Real User Metrics (RUM)
   - Web Vitals scores

### Check Email Stats

1. In SendGrid, go to **Dashboard**
2. See:
   - Emails sent
   - Delivery rate
   - Bounce rate
   - Open rate (if tracking enabled)

### View Function Logs

1. In Vercel, go to your project
2. Click **"Deployments"**
3. Click on latest deployment
4. Click **"Functions"**
5. View logs to see:
   - ✅ Email sent successfully
   - ❌ Any errors

---

## 🔧 Troubleshooting

### Problem: Build Failed

**Error:** `Could not find requirements-vercel.txt`

**Solution:**
1. Make sure Root Directory is set to `landing-page`
2. Check file exists in repo: `landing-page/requirements-vercel.txt`

### Problem: Page Shows 404

**Solution:**
1. Check that `index.html` exists in `landing-page/` directory
2. Verify Root Directory setting
3. Try redeploying

### Problem: Emails Not Sending

**Error:** "SendGrid API key not configured"

**Solution:**
1. Make sure you added SENDGRID_API_KEY in environment variables
2. Click "Redeploy" after adding env vars
3. Check function logs for specific error

**Error:** "From address does not match verified sender"

**Solution:**
1. Make sure FROM_EMAIL matches exactly what you verified in SendGrid
2. Check SendGrid → Sender Authentication → should show verified

### Problem: API Endpoint Returns 500

**Solution:**
1. Check function logs in Vercel
2. Make sure all dependencies in `requirements-vercel.txt` installed correctly
3. Verify environment variables are set

---

## 🎉 Success Checklist

After completing this guide, you should have:

- [x] Landing page deployed and live on Vercel
- [x] SendGrid account created and configured
- [x] API key added to Vercel
- [x] Sender email verified
- [x] Test submission completed successfully
- [x] Welcome email received
- [x] Admin notification received
- [x] Domain configured (optional)
- [x] Analytics enabled

---

## 📈 Next Steps

### Grow Your Waitlist

1. **Share on Social Media**
   - Post on LinkedIn, Twitter, Facebook
   - Use hashtags: #SafetyTech #AI #Workplace

2. **Run Ads**
   - Google Ads: Target "workplace safety software"
   - Facebook Ads: Target facility managers, safety officers
   - LinkedIn Ads: Target warehouse operations

3. **Content Marketing**
   - Write blog posts about workplace safety
   - Create case studies
   - Share on Reddit, Hacker News

### Engage Your Waitlist

1. **Weekly Updates**
   - Send progress updates via SendGrid
   - Share behind-the-scenes
   - Ask for feedback

2. **Exclusive Perks**
   - Early access to beta
   - 3 months free (already mentioned)
   - Lifetime discount for first 100 users

3. **Build Community**
   - Create Slack/Discord for beta testers
   - Host webinars
   - Q&A sessions

---

## 🆘 Need Help?

### Check Logs
```bash
# If using CLI
vercel logs --follow
```

### Documentation
- Vercel Docs: https://vercel.com/docs
- SendGrid Docs: https://docs.sendgrid.com

### Support
- Vercel Support: support@vercel.com
- SendGrid Support: https://support.sendgrid.com

---

## 🎊 Congratulations!

Your SafeWatch AI landing page is now live and collecting leads!

**Share your URL:** https://your-project.vercel.app

Time to start marketing and growing your waitlist! 🚀

---

**Deployment Date:** [Add date when completed]
**Deployment URL:** [Add your Vercel URL]
**Custom Domain:** [Add if configured]
**First Signup:** [Celebrate when you get your first real signup!]
