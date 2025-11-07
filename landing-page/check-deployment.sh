#!/bin/bash

# SafeWatch AI - Pre-Deployment Validation Script
# Run this before deploying to Vercel

echo "🔍 SafeWatch AI - Pre-Deployment Validation"
echo "==========================================="
echo ""

ERRORS=0
WARNINGS=0

# Check if we're in the right directory
if [ ! -f "index.html" ]; then
    echo "❌ ERROR: index.html not found"
    echo "   Please run this from the landing-page directory"
    ERRORS=$((ERRORS + 1))
else
    echo "✅ index.html found"
fi

# Check for required files
echo ""
echo "📁 Checking required files..."
REQUIRED_FILES=(
    "index.html"
    "vercel.json"
    "requirements-vercel.txt"
    "api/index.py"
    "static/js/app.js"
)

for file in "${REQUIRED_FILES[@]}"; do
    if [ -f "$file" ]; then
        echo "  ✅ $file"
    else
        echo "  ❌ $file - MISSING"
        ERRORS=$((ERRORS + 1))
    fi
done

# Check API handler
echo ""
echo "🔌 Checking API handler..."
if grep -q "FastAPI" api/index.py; then
    echo "  ✅ FastAPI found in api/index.py"
else
    echo "  ❌ FastAPI not found in api/index.py"
    ERRORS=$((ERRORS + 1))
fi

if grep -q "Mangum" api/index.py; then
    echo "  ✅ Mangum (Vercel adapter) found"
else
    echo "  ⚠️  Mangum not found - may cause issues on Vercel"
    WARNINGS=$((WARNINGS + 1))
fi

# Check environment variables documentation
echo ""
echo "📝 Checking environment variables..."
if [ -f ".env.example" ]; then
    echo "  ✅ .env.example exists"
    if grep -q "SENDGRID_API_KEY" .env.example; then
        echo "  ✅ SENDGRID_API_KEY documented"
    fi
    if grep -q "FROM_EMAIL" .env.example; then
        echo "  ✅ FROM_EMAIL documented"
    fi
    if grep -q "ADMIN_EMAIL" .env.example; then
        echo "  ✅ ADMIN_EMAIL documented"
    fi
else
    echo "  ⚠️  .env.example not found"
    WARNINGS=$((WARNINGS + 1))
fi

# Check git status
echo ""
echo "🔧 Checking git status..."
if git rev-parse --git-dir > /dev/null 2>&1; then
    echo "  ✅ Git repository detected"

    # Check if there are uncommitted changes
    if [ -n "$(git status --porcelain)" ]; then
        echo "  ⚠️  You have uncommitted changes"
        echo "     Run: git add . && git commit -m 'Ready for deployment'"
        WARNINGS=$((WARNINGS + 1))
    else
        echo "  ✅ No uncommitted changes"
    fi

    # Check if pushed to remote
    LOCAL=$(git rev-parse @)
    REMOTE=$(git rev-parse @{u} 2>/dev/null)
    if [ $? -eq 0 ]; then
        if [ "$LOCAL" = "$REMOTE" ]; then
            echo "  ✅ Local branch is up to date with remote"
        else
            echo "  ⚠️  Local branch is ahead of remote"
            echo "     Run: git push"
            WARNINGS=$((WARNINGS + 1))
        fi
    else
        echo "  ⚠️  No remote branch configured"
        WARNINGS=$((WARNINGS + 1))
    fi
else
    echo "  ❌ Not a git repository"
    ERRORS=$((ERRORS + 1))
fi

# Check Node.js and Vercel CLI
echo ""
echo "🛠️  Checking deployment tools..."
if command -v node > /dev/null 2>&1; then
    echo "  ✅ Node.js installed ($(node --version))"
else
    echo "  ⚠️  Node.js not found (optional for CLI deployment)"
    WARNINGS=$((WARNINGS + 1))
fi

if command -v vercel > /dev/null 2>&1; then
    echo "  ✅ Vercel CLI installed ($(vercel --version | head -1))"
else
    echo "  ⚠️  Vercel CLI not found"
    echo "     Install: npm install -g vercel"
    echo "     (Not required if deploying via dashboard)"
    WARNINGS=$((WARNINGS + 1))
fi

# Check Python dependencies
echo ""
echo "🐍 Checking Python dependencies..."
if [ -f "requirements-vercel.txt" ]; then
    DEPS=$(cat requirements-vercel.txt | grep -v "^#" | grep -v "^$" | wc -l)
    echo "  ✅ $DEPS dependencies listed in requirements-vercel.txt"

    # Check for key dependencies
    if grep -q "fastapi" requirements-vercel.txt; then
        echo "  ✅ fastapi"
    fi
    if grep -q "mangum" requirements-vercel.txt; then
        echo "  ✅ mangum"
    fi
    if grep -q "sendgrid" requirements-vercel.txt; then
        echo "  ✅ sendgrid"
    fi
fi

# Check file sizes
echo ""
echo "📦 Checking file sizes..."
if command -v du > /dev/null 2>&1; then
    INDEX_SIZE=$(du -k index.html | cut -f1)
    if [ $INDEX_SIZE -gt 1000 ]; then
        echo "  ⚠️  index.html is large (${INDEX_SIZE}KB)"
        echo "     Consider optimizing images and assets"
        WARNINGS=$((WARNINGS + 1))
    else
        echo "  ✅ index.html size OK (${INDEX_SIZE}KB)"
    fi
fi

# Summary
echo ""
echo "=========================================="
echo "📊 VALIDATION SUMMARY"
echo "=========================================="

if [ $ERRORS -eq 0 ] && [ $WARNINGS -eq 0 ]; then
    echo ""
    echo "🎉 ALL CHECKS PASSED!"
    echo ""
    echo "Your project is ready to deploy to Vercel!"
    echo ""
    echo "Next steps:"
    echo "1. Visit: https://vercel.com/new"
    echo "2. Import your GitHub repository"
    echo "3. Set Root Directory to: landing-page"
    echo "4. Add environment variables (see .env.example)"
    echo "5. Click Deploy!"
    echo ""
    echo "OR using CLI:"
    echo "  $ vercel login"
    echo "  $ vercel"
    echo "  $ vercel --prod"
    echo ""
    exit 0
elif [ $ERRORS -eq 0 ]; then
    echo ""
    echo "⚠️  $WARNINGS WARNING(S) FOUND"
    echo ""
    echo "Your project can still be deployed, but you should"
    echo "address the warnings above for best results."
    echo ""
    exit 0
else
    echo ""
    echo "❌ $ERRORS ERROR(S) FOUND"
    if [ $WARNINGS -gt 0 ]; then
        echo "⚠️  $WARNINGS WARNING(S) FOUND"
    fi
    echo ""
    echo "Please fix the errors above before deploying."
    echo ""
    exit 1
fi
