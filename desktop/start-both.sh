#!/bin/bash

# ═══════════════════════════════════════════════════════════
# START SCALP TERMINAL WITH MOBILE ACCESS
# ═══════════════════════════════════════════════════════════
#
# This script starts both:
# 1. Tauri desktop app (with backend)
# 2. React dev server for mobile access
#
# Usage: ./start-both.sh
#
# ═══════════════════════════════════════════════════════════

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🚀 Starting Scalp Terminal with Mobile Access"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Get the directory where this script is located
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Start React dev server in background
echo "📱 Starting mobile dev server on port 3000..."
cd "$DIR" && npm run dev > /tmp/scalp-mobile.log 2>&1 &
DEV_PID=$!

echo "   PID: $DEV_PID"
echo "   Logs: /tmp/scalp-mobile.log"
echo ""

# Wait for dev server to start
echo "⏳ Waiting for dev server to start..."
sleep 5

# Check if dev server started successfully
if ps -p $DEV_PID > /dev/null; then
    echo "✅ Mobile dev server started successfully!"
    echo ""
    
    # Get Tailscale IP if available
    if command -v tailscale &> /dev/null; then
        TAILSCALE_IP=$(tailscale ip -4 2>/dev/null)
        if [ ! -z "$TAILSCALE_IP" ]; then
            echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            echo "📱 MOBILE ACCESS READY"
            echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            echo ""
            echo "   On iPhone/Android, open Safari/Chrome to:"
            echo "   🌐 http://$TAILSCALE_IP:3000"
            echo ""
            echo "   (Make sure Tailscale is connected on both devices)"
            echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            echo ""
        fi
    fi
    
    # Start Tauri app (foreground)
    echo "🖥️  Starting Tauri desktop app..."
    echo ""
    cd "$DIR" && npm run tauri dev
    
else
    echo "❌ Mobile dev server failed to start!"
    echo "   Check logs at: /tmp/scalp-mobile.log"
    exit 1
fi

# When Tauri closes, cleanup
echo ""
echo "🧹 Cleaning up..."
kill $DEV_PID 2>/dev/null
echo "✅ Done!"