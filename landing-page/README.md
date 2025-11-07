# SafeWatch AI - Landing Page

A professional landing page with waitlist functionality for the SafeWatch AI safety monitoring SaaS platform.

## Features

- **Modern, Responsive Design** - Works perfectly on desktop, tablet, and mobile
- **Waitlist Form** - Collect leads with email validation and duplicate prevention
- **Feature Showcase** - Highlight key safety monitoring capabilities
- **Pricing Tiers** - Display Starter, Pro, and Enterprise plans
- **Real-time Submission** - AJAX form submission with instant feedback
- **Data Export** - Submissions saved in both JSON and CSV formats
- **Analytics Ready** - Google Analytics event tracking hooks included

## Quick Start

### 1. Install Dependencies

```bash
cd landing-page
pip install -r requirements.txt
```

### 2. Run the Server

```bash
python server.py
```

The landing page will be available at: **http://localhost:8080**

### 3. Test the Waitlist Form

1. Navigate to http://localhost:8080
2. Scroll to the "Join the Waitlist" section
3. Fill out the form and submit
4. Check `waitlist_submissions.json` and `waitlist_submissions.csv` for the data

## Project Structure

```
landing-page/
├── index.html                      # Main landing page
├── server.py                       # FastAPI backend server
├── requirements.txt                # Python dependencies
├── README.md                       # This file
├── static/
│   ├── css/                       # Custom CSS (if needed)
│   ├── js/
│   │   └── app.js                 # JavaScript for form handling
│   └── images/                    # Logo and images
├── waitlist_submissions.json       # JSON format submissions (auto-created)
└── waitlist_submissions.csv        # CSV format submissions (auto-created)
```

## API Endpoints

### POST /api/waitlist

Submit a new waitlist entry.

**Request Body:**
```json
{
  "name": "John Doe",
  "email": "john@example.com",
  "company": "Acme Corp",
  "industry": "warehouse",
  "timestamp": "2024-01-15T10:30:00Z"
}
```

**Response:**
```json
{
  "success": true,
  "message": "Successfully joined the waitlist!",
  "email": "john@example.com"
}
```

### GET /api/waitlist/stats

Get waitlist statistics (for admin use).

**Response:**
```json
{
  "total_submissions": 42,
  "industries": {
    "warehouse": 15,
    "manufacturing": 12,
    "construction": 8,
    "other": 7
  },
  "latest_submission": "2024-01-15T10:30:00Z"
}
```

### GET /health

Health check endpoint.

**Response:**
```json
{
  "status": "healthy",
  "timestamp": "2024-01-15T10:30:00Z"
}
```

## Customization

### Change Branding

1. **Logo/Name**: Edit the `SafeWatch AI` text in `index.html`
2. **Colors**: Modify the gradient colors in the `<style>` section:
   ```css
   .gradient-bg {
       background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
   }
   ```

### Modify Pricing

Edit the pricing cards in `index.html` around line 400-550.

### Add Google Analytics

Add this snippet to the `<head>` section of `index.html`:

```html
<!-- Google Analytics -->
<script async src="https://www.googletagmanager.com/gtag/js?id=GA_MEASUREMENT_ID"></script>
<script>
  window.dataLayer = window.dataLayer || [];
  function gtag(){dataLayer.push(arguments);}
  gtag('js', new Date());
  gtag('config', 'GA_MEASUREMENT_ID');
</script>
```

### Email Notifications

To send email notifications when someone joins the waitlist, you can integrate services like:

- **SendGrid** - Add to `server.py`:
```python
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

def send_welcome_email(email, name):
    message = Mail(
        from_email='hello@safewatch-ai.com',
        to_emails=email,
        subject='Welcome to SafeWatch AI!',
        html_content=f'<strong>Hi {name}!</strong> Thanks for joining...'
    )
    sg = SendGridAPIClient(os.environ.get('SENDGRID_API_KEY'))
    sg.send(message)
```

- **Mailchimp** - Sync submissions to a mailing list
- **AWS SES** - Use Amazon's email service

## Deployment

### Deploy to Production

#### Option 1: Traditional Server (Ubuntu/Debian)

```bash
# Install dependencies
sudo apt update
sudo apt install python3-pip nginx

# Clone your repo
cd /var/www
git clone your-repo-url safewatch-landing

# Install Python packages
cd safewatch-landing/landing-page
pip3 install -r requirements.txt

# Run with systemd
sudo nano /etc/systemd/system/safewatch-landing.service
```

**systemd service file:**
```ini
[Unit]
Description=SafeWatch AI Landing Page
After=network.target

[Service]
User=www-data
WorkingDirectory=/var/www/safewatch-landing/landing-page
ExecStart=/usr/bin/python3 server.py
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
# Start service
sudo systemctl enable safewatch-landing
sudo systemctl start safewatch-landing

# Configure nginx reverse proxy
sudo nano /etc/nginx/sites-available/safewatch
```

**nginx config:**
```nginx
server {
    listen 80;
    server_name safewatch-ai.com www.safewatch-ai.com;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

#### Option 2: Docker

Create `Dockerfile`:
```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8080

CMD ["python", "server.py"]
```

Build and run:
```bash
docker build -t safewatch-landing .
docker run -p 8080:8080 -v $(pwd)/waitlist_submissions.json:/app/waitlist_submissions.json safewatch-landing
```

#### Option 3: Cloud Platforms

**Heroku:**
```bash
# Add Procfile
echo "web: python server.py" > Procfile

# Deploy
heroku create safewatch-landing
git push heroku main
```

**AWS Elastic Beanstalk:**
```bash
eb init -p python-3.11 safewatch-landing
eb create safewatch-prod
eb deploy
```

**Google Cloud Run:**
```bash
gcloud run deploy safewatch-landing \
  --source . \
  --platform managed \
  --region us-central1 \
  --allow-unauthenticated
```

## Domain Setup

1. Purchase domain from Namecheap, GoDaddy, or Google Domains
2. Point A record to your server IP:
   ```
   Type: A
   Host: @
   Value: YOUR_SERVER_IP
   ```
3. Add www subdomain:
   ```
   Type: CNAME
   Host: www
   Value: safewatch-ai.com
   ```

## SSL Certificate (HTTPS)

Use Let's Encrypt for free SSL:

```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d safewatch-ai.com -d www.safewatch-ai.com
```

## Marketing Integrations

### Facebook Pixel

Add to `<head>`:
```html
<script>
  !function(f,b,e,v,n,t,s)
  {if(f.fbq)return;n=f.fbq=function(){n.callMethod?
  n.callMethod.apply(n,arguments):n.queue.push(arguments)};
  if(!f._fbq)f._fbq=n;n.push=n;n.loaded=!0;n.version='2.0';
  n.queue=[];t=b.createElement(e);t.async=!0;
  t.src=v;s=b.getElementsByTagName(e)[0];
  s.parentNode.insertBefore(t,s)}(window, document,'script',
  'https://connect.facebook.net/en_US/fbevents.js');
  fbq('init', 'YOUR_PIXEL_ID');
  fbq('track', 'PageView');
</script>
```

### LinkedIn Insight Tag

Similar process - add LinkedIn's tracking script.

## Performance Optimization

1. **Enable Gzip Compression** in nginx
2. **Use a CDN** like Cloudflare for static assets
3. **Optimize Images** - Use WebP format, lazy loading
4. **Minify CSS/JS** - Use build tools in production

## Security Best Practices

1. **Rate Limiting** - Prevent spam submissions:
```python
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter

@app.post("/api/waitlist")
@limiter.limit("5/minute")  # Max 5 submissions per minute
async def join_waitlist(...):
    ...
```

2. **CORS** - Configure allowed origins
3. **HTTPS Only** - Redirect HTTP to HTTPS
4. **Input Validation** - Already implemented with Pydantic
5. **Captcha** - Add reCAPTCHA to prevent bots

## Monitoring

Track key metrics:
- Number of daily signups
- Conversion rate (visitors → signups)
- Industry distribution
- Bounce rate
- Time on page

Use tools like:
- Google Analytics
- Hotjar (heatmaps)
- Plausible (privacy-friendly analytics)

## Next Steps

1. **A/B Testing** - Test different headlines, CTAs, pricing
2. **Email Automation** - Set up drip campaigns for waitlist
3. **Landing Page Variants** - Create industry-specific pages
4. **Blog** - Add content marketing section
5. **Demo Videos** - Embed product demos
6. **Customer Testimonials** - Add social proof
7. **Live Chat** - Integrate Intercom or Drift

## Support

For issues or questions, check:
- Server logs: `journalctl -u safewatch-landing -f`
- Submission data: `cat waitlist_submissions.json`
- API health: `curl http://localhost:8080/health`

## License

This landing page is part of the SafeWatch AI platform, built on top of NVIDIA's Video Search and Summarization blueprint.
