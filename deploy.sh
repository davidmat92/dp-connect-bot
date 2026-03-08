#!/bin/bash
# ============================================================
# DP Connect Bot – One-Command Deploy
# ============================================================
# Usage: ./deploy.sh
#
# Does: git push → upload changed files via PA Files API → reload webapp
# ============================================================

set -e

# --- Config ---
PA_USER="dpconnect"
PA_DOMAIN="bot-dpconnect.pythonanywhere.com"
PA_API="https://www.pythonanywhere.com/api/v0/user/${PA_USER}"
PA_REMOTE_DIR="/home/dpconnect/dp-connect-bot"

# Bot source directories to sync
BOT_DIRS="dp_connect_bot"

# API Token from environment or .env file
if [ -z "$PA_API_TOKEN" ]; then
    SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
    if [ -f "$SCRIPT_DIR/.env.deploy" ]; then
        source "$SCRIPT_DIR/.env.deploy"
    fi
fi

if [ -z "$PA_API_TOKEN" ]; then
    echo "❌ PA_API_TOKEN nicht gesetzt!"
    echo ""
    echo "Entweder:"
    echo "  export PA_API_TOKEN=dein_token"
    echo "  oder erstelle .env.deploy mit: PA_API_TOKEN=dein_token"
    echo ""
    echo "Token findest du unter: https://www.pythonanywhere.com/account/#api_token"
    exit 1
fi

AUTH="Token ${PA_API_TOKEN}"

echo "🚀 DP Connect Bot Deploy"
echo "========================"

# Step 1: Git push
echo ""
echo "📤 Git push..."
git push origin main
echo "✅ Code gepusht"

# Step 2: Upload changed files via PA Files API
echo ""
echo "📦 Dateien hochladen..."

# Get list of files changed since last deployed commit
# We store the last deployed commit in .last_deploy
LAST_DEPLOY=""
if [ -f ".last_deploy" ]; then
    LAST_DEPLOY=$(cat .last_deploy)
fi

if [ -n "$LAST_DEPLOY" ]; then
    # Get changed files since last deploy
    CHANGED=$(git diff --name-only "$LAST_DEPLOY" HEAD -- "$BOT_DIRS" 2>/dev/null || echo "")
    NEW_FILES=$(git diff --name-only --diff-filter=A "$LAST_DEPLOY" HEAD -- "$BOT_DIRS" 2>/dev/null || echo "")
else
    # First deploy or missing marker – upload all bot files
    CHANGED=$(git ls-files "$BOT_DIRS")
fi

if [ -z "$CHANGED" ]; then
    echo "   Keine Änderungen zu deployen"
else
    FAIL=0
    COUNT=0
    while IFS= read -r file; do
        [ -z "$file" ] && continue
        [ ! -f "$file" ] && continue
        printf "   📄 %-60s" "$file"
        HTTP=$(curl -s -w "%{http_code}" -o /dev/null -X POST \
            "${PA_API}/files/path${PA_REMOTE_DIR}/${file}" \
            -H "Authorization: ${AUTH}" \
            -F "content=@${file}")
        if [ "$HTTP" = "200" ] || [ "$HTTP" = "201" ]; then
            echo "✅"
            COUNT=$((COUNT + 1))
        else
            echo "❌ ($HTTP)"
            FAIL=$((FAIL + 1))
        fi
    done <<< "$CHANGED"
    echo "   ${COUNT} Dateien hochgeladen"
    if [ "$FAIL" -gt 0 ]; then
        echo "   ⚠️  ${FAIL} Dateien fehlgeschlagen!"
    fi
fi

# Step 3: Reload webapp
echo ""
echo "🔄 Webapp reloaden..."
RELOAD_RESULT=$(curl -s -w "\n%{http_code}" -X POST "${PA_API}/webapps/${PA_DOMAIN}/reload/" \
    -H "Authorization: ${AUTH}")

HTTP_CODE=$(echo "$RELOAD_RESULT" | tail -1)

if [ "$HTTP_CODE" = "200" ]; then
    echo "✅ Webapp reloaded!"
else
    echo "⚠️  Reload Status: ${HTTP_CODE}"
    echo "   (Evtl. manuell reloaden: PythonAnywhere → Web → Reload)"
fi

# Step 4: Health check
echo ""
echo "🏥 Health Check..."
sleep 3
HEALTH=$(curl -s "https://${PA_DOMAIN}/health" 2>/dev/null)
if echo "$HEALTH" | grep -q "ok"; then
    echo "✅ Bot läuft! $HEALTH"
    # Save current commit as last deployed
    git rev-parse HEAD > .last_deploy
else
    echo "⚠️  Health Check Response: $HEALTH"
fi

echo ""
echo "🎉 Deploy fertig!"
