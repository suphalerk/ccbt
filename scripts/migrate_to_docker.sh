#!/bin/bash
# Migrate from nohup background processes to Docker containers
set -e
cd "$(dirname "$0")/.."

echo "=== CCBT: Migrate to Docker ==="

# Step 1: Graceful stop all nohup bots
echo "Step 1: Stopping nohup bots..."
pkill -f "Python.*main.py" 2>/dev/null || true
sleep 5

REMAINING=$(pgrep -f "main.py" 2>/dev/null | wc -l | tr -d ' ')
if [ "$REMAINING" -gt 0 ]; then
    echo "  Force killing $REMAINING remaining processes..."
    pkill -9 -f "Python.*main.py" 2>/dev/null || true
    sleep 2
fi
echo "  All nohup bots stopped."

# Step 2: Build Docker image
echo "Step 2: Building Docker image..."
docker compose build

# Step 3: Start all containers
echo "Step 3: Starting 47 bot containers + dashboard..."
docker compose up -d

# Step 4: Verify
echo "Step 4: Verifying..."
sleep 10
RUNNING=$(docker compose ps --format json 2>/dev/null | grep -c '"running"' || docker compose ps | grep -c "Up")
TOTAL=$(docker compose ps --format json 2>/dev/null | wc -l || docker compose ps | tail -n +2 | wc -l)

echo ""
echo "========================================="
echo "  $RUNNING / $TOTAL containers running"
echo "  Dashboard: http://localhost:8501"
echo "  Logs: docker compose logs -f bot-btc"
echo "========================================="
