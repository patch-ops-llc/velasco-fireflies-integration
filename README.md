# Fireflies-DealCloud Integration

A Railway-deployed Flask integration that syncs Fireflies.ai call transcripts to DealCloud CRM interactions.

## Features

- 🔄 **Automated Sync**: Fetches Fireflies transcripts and creates DealCloud interactions
- 👤 **Contact Management**: Finds existing contacts or creates new ones
- 🏢 **Company Association**: Links interactions to companies and deals
- ⏰ **Scheduled Sync**: Runs automatically on a configurable schedule (default: every 6 hours)
- 📥 **Webhook Support**: Trigger syncs via HTTP webhook (HubSpot/Zapier compatible)
- 🔐 **API Key Protection**: Optional API key for endpoint security

## Architecture

```
┌─────────────────┐     ┌──────────────────────────┐     ┌─────────────────┐
│   Fireflies.ai  │────▶│  Railway Flask Service   │────▶│    DealCloud    │
│   (GraphQL API) │     │                          │     │   (REST API)    │
└─────────────────┘     │  - Webhook Handler       │     └─────────────────┘
                        │  - APScheduler           │
                        │  - Sync Service          │
                        └──────────────────────────┘
```

## Project Structure

```
├── app.py                    # Flask application & endpoints
├── config.py                 # Environment configuration
├── logger.py                 # Enhanced logging with emojis
├── services/
│   ├── fireflies_client.py   # Fireflies GraphQL API client
│   ├── dealcloud_client.py   # DealCloud REST API with OAuth
│   └── sync_service.py       # Main sync orchestration
├── requirements.txt          # Python dependencies
├── Procfile                  # Gunicorn command for Railway
├── railway.toml              # Railway configuration
├── runtime.txt               # Python version
└── env.example               # Environment variables template
```

## Deployment to Railway

### 1. Prerequisites

- GitHub repository with this code
- Railway account
- Fireflies API key
- DealCloud API credentials (client_id, api_key)

### 2. Deploy

1. Create new project in Railway from GitHub repo
2. Add environment variables to the **SERVICE** (not just project!):

```
FIREFLIES_API_KEY=your-fireflies-key
DEALCLOUD_CLIENT_ID=your-client-id
DEALCLOUD_API_KEY=your-api-key
```

3. Generate a public domain: Settings → Networking → Generate Domain

### 3. Verify

```bash
# Health check
curl https://your-app.up.railway.app/health

# Test connections
curl -H "X-API-Key: your-key" https://your-app.up.railway.app/api/test-config
```

## API Endpoints

### Health & Status

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Railway health check |
| `/api/status` | GET | Full system status |
| `/api/test-config` | GET | Test API connections |

### Sync

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/webhook/hubspot` | POST | Async webhook trigger (returns 202) |
| `/api/sync` | POST | Manual async sync |
| `/api/sync/blocking` | POST | Manual sync (waits for completion) |
| `/api/sync/transcript/<id>` | POST | Sync single transcript |

### Scheduler

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/scheduler/status` | GET | Scheduler status |
| `/api/scheduler/enable` | POST | Enable scheduled sync |
| `/api/scheduler/disable` | POST | Disable scheduled sync |
| `/api/scheduler/toggle` | POST | Toggle scheduler |

### Admin/Debug

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/admin/test-fireflies` | GET | Test Fireflies API |
| `/api/admin/test-dealcloud` | GET | Test DealCloud API |
| `/api/admin/search-contacts?email=x` | GET | Search contacts |
| `/api/admin/clear-cache` | POST | Clear search cache |

## Configuration

### Required Environment Variables

| Variable | Description |
|----------|-------------|
| `FIREFLIES_API_KEY` | Fireflies API key |
| `DEALCLOUD_CLIENT_ID` | DealCloud OAuth client ID |
| `DEALCLOUD_API_KEY` | DealCloud OAuth client secret |

### Optional Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DEALCLOUD_BASE_URL` | `https://valescoind.dealcloud.com` | DealCloud instance URL |
| `TRANSCRIPT_LIMIT` | `10` | Max transcripts to fetch |
| `RATE_LIMIT_DELAY` | `0.3` | Delay between API calls (seconds) |
| `CRON_INTERVAL_MINUTES` | `360` | Scheduler interval (6 hours) |
| `API_KEY` | None | Protect endpoints with API key |
| `DEBUG` | `false` | Enable debug mode |

## Webhook Integration

### HubSpot Workflow

Create a workflow that POSTs to your Railway webhook:

```json
POST https://your-app.up.railway.app/webhook/hubspot
Content-Type: application/json
X-API-Key: your-api-key

{}
```

### Zapier

Use Zapier's webhook action to POST to `/webhook/hubspot` on a schedule or trigger.

## Local Development

1. Clone the repository
2. Create a virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate  # or venv\Scripts\activate on Windows
   ```
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
4. Copy and configure environment:
   ```bash
   cp env.example .env
   # Edit .env with your API keys
   ```
5. Run the app:
   ```bash
   python app.py
   ```

## Sync Behavior

1. **Fetch Transcripts**: Gets latest transcripts from Fireflies
2. **Filter External**: Only processes calls with external participants
3. **Find Contacts**: Searches DealCloud by email
4. **Create Contacts**: Creates missing contacts (requires company)
5. **Find Deals**: Looks up deals associated with the company
6. **Create Interaction**: Creates DealCloud interaction with:
   - Subject: `Call: {transcript title}`
   - Content: Summary + Notes + Action Items (no full transcript)
   - Links: Contacts, Company, Deals

## Lessons Applied from Guides

- ✅ No hardcoded secrets (environment variables)
- ✅ OAuth token management with auto-refresh
- ✅ Rate limiting with configurable delays
- ✅ Retry logic with exponential backoff
- ✅ Async webhook processing (30s timeout compliance)
- ✅ Background thread for long operations
- ✅ Scheduled sync with APScheduler
- ✅ Health check endpoint for Railway
- ✅ Caching for repeated lookups
- ✅ Comprehensive logging with emoji indicators
- ✅ API key protection for endpoints
- ✅ Gunicorn with proper timeout settings

## Troubleshooting

### "Required environment variable not set"
Variables must be added to the Railway **SERVICE**, not just the project.

### Webhook timeout errors
Ensure the webhook returns 202 immediately - sync runs in background.

### "No company found"
Contacts require a company in DealCloud. If no existing contacts found, new ones can't be created.

### Rate limiting (429 errors)
Increase `RATE_LIMIT_DELAY` or reduce `TRANSCRIPT_LIMIT`.

---

*Based on best practices from Settlor-HubSpot and HubSpot-QuantumReverse integration guides.*

