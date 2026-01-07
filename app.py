# app.py
# Flask application for Fireflies-DealCloud Integration
# Railway deployment with webhook handlers and scheduled sync

import threading
import atexit
from datetime import datetime
from functools import wraps
from flask import Flask, request, jsonify

from config import config
from logger import logger
from services.fireflies_client import fireflies_client
from services.dealcloud_client import dealcloud_client
from services.sync_service import sync_service

# APScheduler for cron jobs
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger


# Initialize Flask app
app = Flask(__name__)

# Track startup time for uptime calculation
START_TIME = datetime.now()

# Track last sync status
sync_status = {
    "last_run": None,
    "last_status": None,
    "is_running": False,
    "last_result": None
}

# Initialize scheduler
scheduler = BackgroundScheduler()
scheduler_enabled = True


# ==================== Authentication ====================

def require_api_key(f):
    """Decorator to require API key for protected endpoints"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Skip auth if no API key configured
        if not config.API_KEY:
            return f(*args, **kwargs)
        
        # Check header
        api_key = request.headers.get("X-API-Key")
        if api_key != config.API_KEY:
            return jsonify({"error": "Unauthorized"}), 401
        
        return f(*args, **kwargs)
    return decorated_function


# ==================== Health & Status Endpoints ====================

@app.route("/", methods=["GET"])
def root():
    """Root endpoint with basic info"""
    return jsonify({
        "service": "Fireflies-DealCloud Integration",
        "status": "running",
        "environment": config.ENVIRONMENT,
        "endpoints": {
            "health": "/health",
            "status": "/api/status",
            "sync": "/api/sync",
            "webhook": "/webhook/hubspot"
        }
    })


@app.route("/health", methods=["GET"])
def health():
    """Health check for Railway"""
    uptime = (datetime.now() - START_TIME).total_seconds()
    return jsonify({
        "status": "healthy",
        "environment": config.ENVIRONMENT,
        "uptime_seconds": round(uptime, 0)
    })


@app.route("/api/status", methods=["GET"])
def status():
    """Full system status"""
    uptime = (datetime.now() - START_TIME).total_seconds()
    return jsonify({
        "status": "running",
        "uptime_seconds": round(uptime, 0),
        "config": config.get_status(),
        "sync_status": sync_status,
        "scheduler": {
            "enabled": scheduler_enabled,
            "running": scheduler.running,
            "interval_minutes": config.CRON_INTERVAL_MINUTES
        }
    })


@app.route("/api/test-config", methods=["GET"])
@require_api_key
def test_config():
    """Test API connections"""
    logger.config("Testing API connections...")
    
    results = {
        "fireflies": fireflies_client.test_connection(),
        "dealcloud": dealcloud_client.test_connection()
    }
    
    all_connected = all(r.get("status") == "connected" for r in results.values())
    
    return jsonify({
        "status": "all_connected" if all_connected else "partial",
        "connections": results
    })


# ==================== Webhook Endpoints ====================

@app.route("/webhook/hubspot", methods=["POST"])
def hubspot_webhook():
    """
    Main webhook endpoint for HubSpot/Zapier triggers.
    Processes requests asynchronously to avoid timeout.
    
    IMPORTANT: Returns 202 Accepted immediately,
    then processes in background thread.
    """
    data = request.json or {}
    
    logger.incoming(f"Webhook received: {data}")
    
    # Start background processing
    thread = threading.Thread(
        target=run_sync_background,
        daemon=True
    )
    thread.start()
    
    return jsonify({
        "status": "accepted",
        "message": "Sync started in background"
    }), 202


@app.route("/webhook/hubspot/test", methods=["GET"])
def webhook_test():
    """Test webhook reachability"""
    return jsonify({
        "status": "ok",
        "message": "Webhook endpoint is reachable"
    })


# ==================== Sync Endpoints ====================

@app.route("/api/sync", methods=["POST"])
@require_api_key
def trigger_sync():
    """
    Trigger full sync (async).
    Use for manual testing or scheduled triggers.
    """
    if sync_status["is_running"]:
        return jsonify({
            "status": "already_running",
            "message": "A sync is already in progress"
        }), 409
    
    # Start background processing
    thread = threading.Thread(
        target=run_sync_background,
        daemon=True
    )
    thread.start()
    
    return jsonify({
        "status": "accepted",
        "message": "Sync started in background"
    }), 202


@app.route("/api/sync/blocking", methods=["POST"])
@require_api_key
def trigger_sync_blocking():
    """
    Trigger full sync (blocking/synchronous).
    Use for testing - waits for completion.
    """
    if sync_status["is_running"]:
        return jsonify({
            "status": "already_running",
            "message": "A sync is already in progress"
        }), 409
    
    result = run_sync()
    return jsonify(result)


@app.route("/api/sync/transcript/<transcript_id>", methods=["POST"])
@require_api_key
def sync_transcript(transcript_id: str):
    """Sync a single transcript by ID"""
    logger.sync(f"Manual sync requested for transcript: {transcript_id}")
    
    result = sync_service.sync_transcript(transcript_id)
    
    return jsonify(result)


# ==================== Admin/Debug Endpoints ====================

@app.route("/api/admin/test-fireflies", methods=["GET"])
@require_api_key
def test_fireflies():
    """Test Fireflies API and fetch sample transcript"""
    logger.debug("Testing Fireflies API...")
    
    transcripts = fireflies_client.fetch_transcripts(limit=1)
    
    if transcripts:
        return jsonify({
            "status": "ok",
            "sample_transcript": {
                "id": transcripts[0].get("id"),
                "title": transcripts[0].get("title"),
                "date": transcripts[0].get("date"),
                "participants": transcripts[0].get("participants")
            }
        })
    else:
        return jsonify({
            "status": "error",
            "message": "No transcripts found or API error"
        }), 500


@app.route("/api/admin/test-dealcloud", methods=["GET"])
@require_api_key
def test_dealcloud():
    """Test DealCloud API connection"""
    logger.debug("Testing DealCloud API...")
    
    result = dealcloud_client.test_connection()
    
    return jsonify({
        "status": result.get("status"),
        "details": result
    })


@app.route("/api/admin/search-contacts", methods=["GET"])
@require_api_key
def search_contacts():
    """Search contacts by email (debug endpoint)"""
    email = request.args.get("email")
    
    if not email:
        return jsonify({"error": "email parameter required"}), 400
    
    contacts = dealcloud_client.search_contacts_by_email([email])
    
    return jsonify({
        "email": email,
        "found": len(contacts),
        "contacts": contacts
    })


@app.route("/api/admin/clear-cache", methods=["POST"])
@require_api_key
def clear_cache():
    """Clear DealCloud search cache"""
    dealcloud_client.clear_cache()
    return jsonify({"status": "cache_cleared"})


# ==================== Scheduler Endpoints ====================

@app.route("/api/scheduler/status", methods=["GET"])
@require_api_key
def scheduler_status():
    """Get scheduler status"""
    return jsonify({
        "enabled": scheduler_enabled,
        "running": scheduler.running,
        "interval_minutes": config.CRON_INTERVAL_MINUTES,
        "next_run": str(scheduler.get_jobs()[0].next_run_time) if scheduler.get_jobs() else None
    })


@app.route("/api/scheduler/enable", methods=["POST"])
@require_api_key
def scheduler_enable():
    """Enable scheduled sync"""
    global scheduler_enabled
    scheduler_enabled = True
    
    if not scheduler.running:
        scheduler.start()
    
    logger.scheduled("Scheduler enabled")
    return jsonify({"status": "enabled"})


@app.route("/api/scheduler/disable", methods=["POST"])
@require_api_key
def scheduler_disable():
    """Disable scheduled sync"""
    global scheduler_enabled
    scheduler_enabled = False
    
    logger.scheduled("Scheduler disabled")
    return jsonify({"status": "disabled"})


@app.route("/api/scheduler/toggle", methods=["POST"])
@require_api_key
def scheduler_toggle():
    """Toggle scheduler on/off"""
    global scheduler_enabled
    scheduler_enabled = not scheduler_enabled
    
    status = "enabled" if scheduler_enabled else "disabled"
    logger.scheduled(f"Scheduler {status}")
    
    return jsonify({"status": status})


# ==================== Background Processing ====================

def run_sync() -> dict:
    """Run sync and update status"""
    global sync_status
    
    sync_status["is_running"] = True
    sync_status["last_run"] = datetime.now().isoformat()
    
    try:
        result = sync_service.sync_all()
        sync_status["last_status"] = "success"
        sync_status["last_result"] = result
        return result
        
    except Exception as e:
        logger.error(f"Sync failed: {str(e)}", e)
        sync_status["last_status"] = "error"
        sync_status["last_result"] = {"error": str(e)}
        return {"success": False, "error": str(e)}
        
    finally:
        sync_status["is_running"] = False


def run_sync_background():
    """Run sync in background thread"""
    with app.app_context():
        run_sync()


def scheduled_sync():
    """Scheduled sync job (called by APScheduler)"""
    global scheduler_enabled
    
    if not scheduler_enabled:
        logger.scheduled("Scheduled sync skipped (disabled)")
        return
    
    logger.scheduled("Starting scheduled sync...")
    
    with app.app_context():
        run_sync()


# ==================== Scheduler Setup ====================

def setup_scheduler():
    """Configure and start the scheduler"""
    # Add sync job
    scheduler.add_job(
        func=scheduled_sync,
        trigger=IntervalTrigger(minutes=config.CRON_INTERVAL_MINUTES),
        id="fireflies_sync",
        name="Fireflies to DealCloud Sync",
        replace_existing=True
    )
    
    scheduler.start()
    logger.scheduled(f"Scheduler started (interval: {config.CRON_INTERVAL_MINUTES} minutes)")


# ==================== Application Startup ====================

def startup():
    """Application startup tasks"""
    logger.separator("=", 60)
    logger.config("FIREFLIES-DEALCLOUD INTEGRATION")
    logger.config(f"Environment: {config.ENVIRONMENT}")
    logger.config(f"Debug: {config.DEBUG}")
    logger.separator("=", 60)
    
    # Test connections on startup
    logger.config("Testing API connections...")
    
    ff_status = fireflies_client.test_connection()
    dc_status = dealcloud_client.test_connection()
    
    if ff_status.get("status") == "connected":
        logger.success("Fireflies API: Connected")
    else:
        logger.error(f"Fireflies API: {ff_status.get('error', 'Failed')}")
    
    if dc_status.get("status") == "connected":
        logger.success("DealCloud API: Connected")
    else:
        logger.error(f"DealCloud API: {dc_status.get('error', 'Failed')}")
    
    # Start scheduler
    setup_scheduler()
    
    logger.separator("=", 60)
    logger.success("Application started successfully")
    logger.separator("=", 60)


# Cleanup on shutdown
atexit.register(lambda: scheduler.shutdown(wait=False))


# ==================== Main Entry Point ====================

if __name__ == "__main__":
    import os
    
    startup()
    
    port = int(os.getenv("PORT", 5000))
    debug = config.DEBUG
    
    logger.config(f"Starting Flask server on port {port}")
    app.run(host="0.0.0.0", port=port, debug=debug)

