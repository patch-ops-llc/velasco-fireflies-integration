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
    
    Query params:
        limit: Number of transcripts to fetch (default: 10, max: 500)
    """
    if sync_status["is_running"]:
        return jsonify({
            "status": "already_running",
            "message": "A sync is already in progress"
        }), 409
    
    # Get optional limit parameter
    limit = request.args.get("limit", type=int) or config.TRANSCRIPT_LIMIT
    limit = min(limit, 500)  # Cap at 500 for safety
    
    # Start background processing
    thread = threading.Thread(
        target=run_sync_background,
        args=(limit,),
        daemon=True
    )
    thread.start()
    
    return jsonify({
        "status": "accepted",
        "message": f"Sync started in background (limit: {limit} transcripts)"
    }), 202


@app.route("/api/sync/blocking", methods=["POST"])
@require_api_key
def trigger_sync_blocking():
    """
    Trigger full sync (blocking/synchronous).
    Use for testing - waits for completion.
    
    Query params:
        limit: Number of transcripts to fetch (default: 10, max: 500)
    """
    if sync_status["is_running"]:
        return jsonify({
            "status": "already_running",
            "message": "A sync is already in progress"
        }), 409
    
    # Get optional limit parameter
    limit = request.args.get("limit", type=int) or config.TRANSCRIPT_LIMIT
    limit = min(limit, 500)  # Cap at 500 for safety
    
    result = run_sync(limit)
    return jsonify(result)


@app.route("/api/sync/backfill", methods=["POST"])
@require_api_key
def trigger_backfill():
    """
    Backfill sync - fetches more historical transcripts.
    Use to catch up on older calls that weren't synced.
    
    Query params:
        limit: Number of transcripts to fetch (default: 100, max: 500)
    """
    if sync_status["is_running"]:
        return jsonify({
            "status": "already_running",
            "message": "A sync is already in progress"
        }), 409
    
    # Default to 100 for backfill
    limit = request.args.get("limit", type=int) or 100
    limit = min(limit, 500)  # Cap at 500 for safety
    
    logger.sync(f"Starting backfill sync with limit: {limit}")
    
    # Start background processing
    thread = threading.Thread(
        target=run_sync_background,
        args=(limit,),
        daemon=True
    )
    thread.start()
    
    return jsonify({
        "status": "accepted",
        "message": f"Backfill sync started (fetching last {limit} transcripts)"
    }), 202


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


@app.route("/api/admin/debug-transcript/<transcript_id>", methods=["GET"])
@require_api_key
def debug_transcript(transcript_id: str):
    """
    Debug a specific transcript - shows what would happen during sync
    without actually creating the interaction.
    """
    logger.debug(f"Debug request for transcript: {transcript_id}")
    
    # Fetch transcript
    transcript = fireflies_client.fetch_transcript_by_id(transcript_id)
    
    if not transcript:
        return jsonify({
            "error": f"Transcript not found: {transcript_id}"
        }), 404
    
    title = transcript.get("title", "Untitled")
    
    # Extract project name
    project_name = sync_service.extract_project_name(title)
    
    # Search for deals by name
    deals_by_name = []
    if project_name:
        deals_by_name = dealcloud_client.search_deals_by_name(project_name)
    
    # Get participants
    all_participants = transcript.get("participants") or []
    all_emails = [p for p in all_participants if p and "@" in p]
    internal_domains = sync_service.internal_domains
    external_emails = [e for e in all_emails if not sync_service.is_internal_email(e)]
    
    # Search for contacts
    contacts = dealcloud_client.search_contacts_by_email(external_emails) if external_emails else []
    
    # Get summary info
    summary = transcript.get("summary")
    summary_info = {}
    if summary:
        summary_info = {
            "has_overview": bool(summary.get("overview")),
            "overview_length": len(summary.get("overview", "") or ""),
            "has_shorthand_bullet": bool(summary.get("shorthand_bullet")),
            "shorthand_bullet_length": len(summary.get("shorthand_bullet", "") or ""),
            "has_outline": bool(summary.get("outline")),
            "outline_length": len(summary.get("outline", "") or ""),
            "has_action_items": bool(summary.get("action_items")),
            "action_items_count": len(summary.get("action_items", []) or []),
            "has_keywords": bool(summary.get("keywords")),
            "keywords": summary.get("keywords", [])
        }
    
    # Format content
    formatted_content = sync_service.format_content(summary)
    
    return jsonify({
        "transcript": {
            "id": transcript_id,
            "title": title,
            "date": transcript.get("date"),
            "duration": transcript.get("duration"),
            "participants": all_participants
        },
        "analysis": {
            "extracted_project_name": project_name,
            "external_emails": external_emails,
            "internal_domains_configured": internal_domains
        },
        "dealcloud_matches": {
            "deals_by_project_name": [
                {
                    "id": d.get("EntryId"),
                    "name": d.get("DealName"),
                    "company": d.get("Company")
                } for d in deals_by_name[:5]
            ],
            "contacts_found": [
                {
                    "id": c.get("EntryId"),
                    "email": c.get("Email"),
                    "name": c.get("FullName"),
                    "company": c.get("Company")
                } for c in contacts[:5]
            ]
        },
        "summary_analysis": summary_info,
        "formatted_content_length": len(formatted_content),
        "formatted_content_preview": formatted_content[:500] if formatted_content else None
    })


@app.route("/api/admin/search-deal", methods=["GET"])
@require_api_key
def search_deal():
    """Search deals by name (debug endpoint)"""
    name = request.args.get("name")
    
    if not name:
        return jsonify({"error": "name parameter required"}), 400
    
    deals = dealcloud_client.search_deals_by_name(name)
    
    return jsonify({
        "search_term": name,
        "found": len(deals),
        "deals": [
            {
                "id": d.get("EntryId"),
                "name": d.get("DealName"),
                "company": d.get("Company")
            } for d in deals[:10]
        ]
    })


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

def run_sync(limit: int = None) -> dict:
    """Run sync and update status"""
    global sync_status
    
    limit = limit or config.TRANSCRIPT_LIMIT
    
    sync_status["is_running"] = True
    sync_status["last_run"] = datetime.now().isoformat()
    
    try:
        result = sync_service.sync_all(limit=limit)
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


def run_sync_background(limit: int = None):
    """Run sync in background thread"""
    with app.app_context():
        run_sync(limit)


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

