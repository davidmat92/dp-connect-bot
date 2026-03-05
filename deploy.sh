#!/bin/bash
# ============================================================
# DP Connect Bot – One-Command Deploy
# ============================================================
# Usage: ./deploy.sh
#
# Does: git push → PythonAnywhere git pull → reload webapp
# ============================================================

set -e

# --- Config ---
PA_USER="dpconnect"
PA_DOMAIN="bot-dpconnect.pythonanywhere.com"
PA_API="https://www.pythonanywhere.com/api/v0/user/${PA_USER}"

# API Token from environment or .env file
if [ -z "$PA_API_TOKEN" ]; then
    # Try loading from .env.deploy
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

# Step 2: Git pull on PythonAnywhere via console API
echo ""
echo "📥 Git pull auf PythonAnywhere..."

# Create a bash console
CONSOLE_ID=$(curl -s -X POST "${PA_API}/consoles/" \
    -H "Authorization: ${AUTH}" \
    -H "Content-Type: application/json" \
    -d '{"executable": "bash", "arguments": "", "working_directory": "/home/dpconnect/dp-connect-bot"}' \
    | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null)

if [ -z "$CONSOLE_ID" ]; then
    echo "⚠️  Console erstellen fehlgeschlagen. Versuche Reload ohne git pull..."
else
    # Send git pull command
    curl -s -X POST "${PA_API}/consoles/${CONSOLE_ID}/send_input/" \
        -H "Authorization: ${AUTH}" \
        -H "Content-Type: application/json" \
        -d '{"input": "cd /home/dpconnect/dp-connect-bot && git pull origin main\n"}' > /dev/null

    # Wait for git pull to complete
    sleep 5

    # Kill the console
    curl -s -X DELETE "${PA_API}/consoles/${CONSOLE_ID}/" \
        -H "Authorization: ${AUTH}" > /dev/null 2>&1

    echo "✅ Git pull erledigt"
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
else
    echo "⚠️  Health Check Response: $HEALTH"
fi

echo ""
echo "🎉 Deploy fertig!"
