# Settlor-HubSpot Integration: Complete Guide & Lessons Learned

**Project:** Independence Settlor Integration  
**Purpose:** Bi-directional data sync between Settlor (Title Insurance TPS) and HubSpot CRM  
**Deployment Platform:** Railway  
**Last Updated:** January 2026

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Architecture Overview](#architecture-overview)
3. [Data Model & Mapping](#data-model--mapping)
4. [API Integration Patterns](#api-integration-patterns)
5. [Complex Field Mappings](#complex-field-mappings)
6. [Deployment Guide](#deployment-guide)
7. [Operational Runbook](#operational-runbook)
8. [Lessons Learned](#lessons-learned)
9. [Troubleshooting Guide](#troubleshooting-guide)
10. [Future Enhancements](#future-enhancements)

---

## Executive Summary

This integration synchronizes data from **Settlor** (a Title Insurance Transaction Processing System) to **HubSpot CRM**, enabling title companies to:

- Track orders/deals in HubSpot
- Associate contacts, companies, and profiles
- Monitor sales rep performance and commissions
- Track co-op (co-operative) sales relationships
- Report on deal status with virtual standup tracking

### Key Statistics

| Metric | Value |
|--------|-------|
| Objects Synced | 4 (Companies, Contacts, Profiles, Deals) |
| Custom Properties Created | 60+ |
| Associations Created | 3 types |
| Sync Frequency | On-demand + Scheduled |
| Average Sync Time | 2-5 minutes (30-day window) |

---

## Architecture Overview

### System Diagram

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                              SETTLOR API                                      │
│    OAuth 2.0 Authentication (client_credentials grant)                        │
│    Endpoints: /individuals, /contacts, /companies, /orders, /step-codes       │
└──────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                         RAILWAY (Flask Application)                           │
│  ┌─────────────────────────────────────────────────────────────────────────┐ │
│  │  app.py          - Flask endpoints, webhook handlers                    │ │
│  │  config.py       - Environment variable management                      │ │
│  │  settlor_client  - Settlor API client with OAuth, pagination            │ │
│  │  hubspot_client  - HubSpot API client with batch upsert, associations   │ │
│  │  mappers.py      - Data transformation logic                            │ │
│  │  sync.py         - Orchestration service                                │ │
│  └─────────────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                              HUBSPOT CRM                                      │
│    Objects: Companies, Contacts, Deals, Custom Object (Profiles)              │
│    API: REST v3 with Batch Upsert, Associations v4                           │
└──────────────────────────────────────────────────────────────────────────────┘
```

### Component Responsibilities

| Component | Responsibility |
|-----------|----------------|
| `app.py` | HTTP endpoints, webhook handling, background thread management |
| `config.py` | Environment variable loading, validation, configuration status |
| `settlor_client.py` | OAuth token management, paginated data fetching, rate limiting |
| `hubspot_client.py` | Batch upsert, ID lookups, property creation, association management |
| `mappers.py` | Data transformation, field mapping, status calculation, email validation |
| `sync.py` | Orchestration of full and partial syncs, relationship building |

---

## Data Model & Mapping

### Entity Mapping

| Settlor Entity | HubSpot Object | ID Property |
|----------------|----------------|-------------|
| Individuals | Contacts | `settlor_individual_id` |
| Contacts | Profiles (Custom Object `2-223523451`) | `settlor_contact_id` |
| Companies | Companies | `settlor_company_id` |
| Orders | Deals | `settlor_order_id` |

### Why This Mapping?

**The naming can be confusing.** Here's the rationale:

1. **Settlor Individuals → HubSpot Contacts**
   - Individuals in Settlor are the actual people (buyers, sellers, etc.)
   - These map naturally to HubSpot Contacts (the standard CRM object)
   - In the HubSpot portal, Contacts are renamed to "Individuals" for clarity

2. **Settlor Contacts → HubSpot Profiles (Custom Object)**
   - Settlor "Contacts" are junction records linking Individuals to Companies with specific roles
   - They contain delivery preferences, sales rep assignments, and relationship context
   - A custom object was needed because HubSpot Contacts don't support this many-to-many relationship

3. **Settlor Orders → HubSpot Deals**
   - Orders are the core transaction records
   - Deals in HubSpot represent revenue opportunities (title orders)
   - In the portal, Deals are renamed to "Orders/Files"

### Association Model

```
                    ┌─────────────────┐
                    │    COMPANY      │
                    │ (settlor_company_id) │
                    └────────┬────────┘
                             │
                   profile_to_company
                             │
                             ▼
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│     CONTACT     │◄───│     PROFILE     │───►│      DEAL       │
│ (individual)    │    │ (settlor_contact_id)│    │ (settlor_order_id) │
└─────────────────┘    └─────────────────┘    └─────────────────┘
                  profile_to_contact      deal_to_profile
```

---

## API Integration Patterns

### Settlor API

#### Authentication

```python
# OAuth 2.0 Client Credentials Flow
response = session.post(
    f"{base_url}/api/v1/oauth/token",
    json={
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "client_credentials"
    }
)
access_token = response.json()["access_token"]
```

**Key Headers Required:**
```python
headers = {
    "Authorization": f"Bearer {access_token}",
    "Content-Type": "application/json",
    "x-settlor-customer-id": customer_id  # Critical! Often overlooked
}
```

#### Pagination Pattern

Settlor uses offset-based pagination:

```python
def fetch_data(endpoint, params):
    all_items = []
    offset = 0
    limit = 100
    
    while True:
        response = session.get(
            f"{base_url}{endpoint}?limit={limit}&offset={offset}&{params}"
        )
        data = response.json()["data"]
        items = data["items"]
        total = data["total"]
        
        all_items.extend(items)
        
        if len(all_items) >= total:
            break
        
        offset += limit
        time.sleep(1)  # Rate limiting
    
    return all_items
```

#### Incremental Sync

Use `ts_modified_after` for efficient syncing:

```python
# Fetch records modified in the last 30 days
ts_modified_after = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%dT00:00:00")
params = {"ts_modified_after": ts_modified_after}
```

### HubSpot API

#### Batch Upsert Pattern

HubSpot's batch upsert is the most efficient way to sync:

```python
# Using a unique ID property for upsert
records = [
    {
        "properties": {"settlor_order_id": "123", "dealname": "Order 123", ...},
        "id": "123",  # The unique ID value
        "idProperty": "settlor_order_id"  # The property name to match on
    }
]

response = session.post(
    "https://api.hubapi.com/crm/v3/objects/deals/batch/upsert",
    json={"inputs": records}
)
```

#### Association Creation (v4 API)

```python
# 1. First, ensure the association definition exists
response = session.post(
    f"{BASE_URL}/crm/v4/associations/{from_type}/{to_type}/labels",
    json={
        "name": "deal_to_profile",
        "label": "Profile",
        "inverseLabel": "Deal"
    }
)
type_id = response.json()["typeId"]

# 2. Then create associations in batch
inputs = [
    {
        "from": {"id": deal_hubspot_id},
        "to": {"id": profile_hubspot_id},
        "types": [{
            "associationCategory": "USER_DEFINED",
            "associationTypeId": type_id
        }]
    }
]

response = session.post(
    f"{BASE_URL}/crm/v4/associations/deal/{profile_object}/batch/create",
    json={"inputs": inputs}
)
```

---

## Complex Field Mappings

### Sales Rep Extraction

Sales reps come from the **commissions/sales_credits table** on each order:

```python
def extract_sales_reps_from_commissions(order):
    commissions = order.get("commissions") or order.get("sales_credits") or []
    
    # Sort by credited status - credited first
    sorted_commissions = sorted(
        commissions,
        key=lambda c: (not c.get("credited", False), c.get("sequence", 999))
    )
    
    # First entry = Primary Sales Rep
    # Second entry = Second Sales Rep
    primary_rep = sorted_commissions[0]["sales_rep"] if len(sorted_commissions) > 0 else None
    second_rep = sorted_commissions[1]["sales_rep"] if len(sorted_commissions) > 1 else None
    
    return primary_rep, second_rep
```

### Co-op Sales Rep Extraction

Co-ops are sales reps associated with customers on an order who are **NOT** in the commissions table:

```python
def extract_coops_from_order(order, commission_rep_ids, contact_lookup):
    coops = []
    
    for person in order.get("people", []):
        contact = person.get("contact", {})
        contact_id = contact.get("id")
        
        # Look up full contact data for sales rep info
        if contact_id in contact_lookup:
            full_contact = contact_lookup[contact_id]
            sales_rep = (
                full_contact.get("sales_rep") or
                full_contact.get("individual", {}).get("sales_rep") or
                full_contact.get("company", {}).get("sales_rep")
            )
            
            if sales_rep and sales_rep["id"] not in commission_rep_ids:
                coops.append({
                    "rep": sales_rep,
                    "customer_name": full_contact.get("individual", {}).get("name")
                })
    
    return coops
```

### Virtual Standup Status

The "Virtual Status" is derived from order history step codes:

```python
def get_virtual_standup_status(order):
    # Cancelled takes precedence
    if order.get("ts_cancel"):
        return "Cancelled"
    
    # Funded = Closed
    if order.get("date_funding"):
        return "Closed"
    
    # Check history for most recent status-type step code
    for entry in reversed(order.get("history", [])):
        step = entry.get("step", {})
        desc = step.get("description", "").lower()
        
        if "cancel" in desc:
            return "Cancelled"
        elif any(kw in desc for kw in ["close", "fund", "record"]):
            return "Closed"
        elif any(kw in desc for kw in ["open", "active", "reopen"]):
            return "Open"
    
    return "Open"  # Default
```

### Date Handling

HubSpot expects dates as **midnight UTC timestamps in milliseconds**:

```python
def to_midnight_utc(date_string):
    if not date_string:
        return None
    
    if 'T' in str(date_string):
        dt = datetime.fromisoformat(str(date_string).replace('Z', '+00:00'))
        dt = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        dt = datetime.strptime(str(date_string)[:10], '%Y-%m-%d')
    
    return int(dt.timestamp() * 1000)
```

### Email Validation

Many Settlor records have invalid emails. Handle gracefully:

```python
def validate_email(email):
    if not email:
        return None
    
    email = str(email).strip().lower()
    
    # Check for invalid patterns
    if any([
        email.startswith("www."),
        "@@" in email,
        " " in email,
        "@" not in email,
        email.count("@") != 1,
        ".." in email
    ]):
        return None
    
    return email

# Generate placeholder email if invalid
if not validate_email(raw_email):
    email = f"individual{settlor_id}@settlor-import.com"
```

---

## Deployment Guide

### Prerequisites

1. **Settlor API Credentials:**
   - Client ID and Secret (OAuth)
   - Customer ID (tenant identifier)

2. **HubSpot Private App:**
   - Access Token with scopes: `crm.objects.contacts`, `crm.objects.companies`, `crm.objects.deals`, `crm.schemas.custom`, `crm.objects.custom`

3. **Railway Account:**
   - Connected to GitHub repository

### Step-by-Step Deployment

#### 1. Prepare Repository

```bash
# Ensure all required files exist
requirements.txt    # Dependencies with versions
Procfile           # gunicorn app:app --bind 0.0.0.0:$PORT
railway.toml       # Railway-specific configuration
runtime.txt        # python-3.11.6
env.example        # Template for environment variables
```

#### 2. Railway Setup

1. Create new project from GitHub repo
2. **Critical:** Add environment variables to the SERVICE, not just the project!

```
SETTLOR_API_BASE_URL=https://api.settlor.com
SETTLOR_API_CLIENT_ID=your_client_id
SETTLOR_API_SECRET=your_client_secret
SETTLOR_CUSTOMER_ID=your_customer_id
HUBSPOT_ACCESS_TOKEN=pat-na2-xxxx
HUBSPOT_CONTACT_PROFILES_OBJECT_ID=2-223523451
HUBSPOT_PROFILES_OBJECT_NAME=2-223523451
DAYS_TO_FETCH=30
API_KEY=your_secure_api_key
LOG_LEVEL=INFO
```

3. Generate public domain: Settings → Networking → Generate Domain

#### 3. Verify Deployment

```bash
# Health check
curl https://your-app.up.railway.app/health

# Test configuration
curl -H "X-API-Key: your_key" https://your-app.up.railway.app/api/test-config

# Trigger sync
curl -X POST -H "X-API-Key: your_key" https://your-app.up.railway.app/api/sync
```

#### 4. HubSpot Workflow Integration (Optional)

Create a workflow to trigger syncs:

1. Workflow trigger: Time-based or event-based
2. Action: Webhook
3. URL: `https://your-app.up.railway.app/webhook/hubspot`
4. Method: POST
5. Payload: `{"sync_type": "full"}`

---

## Operational Runbook

### Available Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/health` | GET | Health check for Railway |
| `/api/status` | GET | Current sync status |
| `/api/test-config` | GET | Test API connections |
| `/api/sync` | POST | Full sync (all objects) |
| `/api/sync/companies` | POST | Sync companies only |
| `/api/sync/contacts` | POST | Sync contacts/individuals only |
| `/api/sync/profiles` | POST | Sync profiles only |
| `/api/sync/deals` | POST | Sync deals/orders only |
| `/webhook/hubspot` | POST | HubSpot workflow trigger |
| `/api/debug/order-sample` | GET | Debug: fetch sample order |
| `/api/debug/step-codes` | GET | Debug: fetch step codes |

### Monitoring

1. **Railway Logs:** Dashboard → Service → Deployments → View Logs
2. **Status Endpoint:** Check `/api/status` for last run results
3. **HubSpot Workflow History:** See webhook responses

### Sync Strategy

```
Recommended: Partial syncs in sequence

1. /api/sync/companies    (fastest, establishes company IDs)
2. /api/sync/contacts     (individuals for contact associations)
3. /api/sync/profiles     (if enabled, links individuals to companies)
4. /api/sync/deals        (orders with all associations)
```

### Handling Large Datasets

For initial full sync of large datasets:

1. Set `FETCH_LIMIT=100` for testing
2. Increase `DAYS_TO_FETCH` gradually
3. Use partial syncs instead of full sync
4. Monitor memory usage in Railway

---

## Lessons Learned

### 1. Settlor API Quirks

#### Customer ID Header is Critical
```python
# Without this header, you'll get 401 or wrong data
headers["x-settlor-customer-id"] = customer_id
```

#### Field Names Vary
The commissions data can be under `commissions` OR `sales_credits` depending on the endpoint:
```python
commissions = order.get("commissions") or order.get("sales_credits") or []
```

#### History Structure
Order history entries have a nested `step` object:
```python
for entry in order.get("history", []):
    step = entry.get("step", {})  # Not entry directly!
    description = step.get("description")
```

### 2. HubSpot API Quirks

#### Custom Object Property Groups
Custom objects require a property group before creating properties:
```python
# Create group first
session.post(
    f"{BASE_URL}/crm/v3/properties/{object_id}/groups",
    json={"name": "custom_info", "label": "Custom Information"}
)

# Then create properties with groupName
payload["groupName"] = "custom_info"
```

#### Batch Upsert ID Format
The `id` field in batch upsert must be the VALUE, not the HubSpot record ID:
```python
{
    "properties": {...},
    "id": "SETTLOR-123",  # The value of idProperty
    "idProperty": "settlor_order_id"  # The property name
}
```

#### Association Type IDs
When creating associations, you need the `typeId` from the association definition, not a generic type:
```python
# Get type ID from creation or lookup
type_id = response.json().get("typeId")

# Use in association creation
"types": [{"associationCategory": "USER_DEFINED", "associationTypeId": type_id}]
```

### 3. Railway Deployment

#### Environment Variables: Service vs Project
**This is the #1 cause of deployment issues!**

- Project variables are shared but must be linked
- Service variables apply directly
- **Always add to SERVICE, then deploy**

#### Use "New Deploy" Not "Redeploy"
After adding/changing environment variables, use "New Deploy" to ensure they're picked up.

#### Timeout Configuration
Default gunicorn timeout is 30s. For sync operations, increase it:
```
gunicorn app:app --timeout 300
```

### 4. Webhook Design

#### HubSpot Timeout = 30 Seconds
HubSpot webhooks timeout after 30 seconds. Use async processing:

```python
@app.route('/webhook/hubspot', methods=['POST'])
def webhook():
    thread = threading.Thread(target=long_running_sync)
    thread.daemon = True
    thread.start()
    return jsonify({"status": "accepted"}), 202  # Return immediately
```

### 5. Data Quality Issues

#### Invalid Emails
Many legacy records have invalid emails. Generate placeholders:
```python
if not validate_email(email):
    email = f"record{id}@import-placeholder.com"
```

#### Null/Missing Nested Objects
Always use safe navigation:
```python
address = order.get("legal", {}).get("address", {}).get("city", "")
```

#### Duplicate Records
Deduplicate by ID before sending to HubSpot:
```python
seen_ids = set()
unique_records = []
for record in records:
    record_id = record["properties"]["settlor_id"]
    if record_id not in seen_ids:
        seen_ids.add(record_id)
        unique_records.append(record)
```

### 6. Performance Optimization

#### Rate Limiting
Add delays between API calls to avoid rate limits:
```python
time.sleep(1)  # Between batches
time.sleep(0.1)  # Between individual calls
```

#### Batch Size
HubSpot batch limit is 100 records. Settlor is 100 per page.
```python
BATCH_SIZE = 100
```

#### Retry Logic
Use requests retry adapter for transient failures:
```python
retry_strategy = Retry(
    total=3,
    backoff_factor=1,
    status_forcelist=[429, 500, 502, 503, 504]
)
```

---

## Troubleshooting Guide

### Issue: "Required environment variable missing"

**Cause:** Variables not added to service  
**Fix:** Railway → Service → Variables → Add all required vars → New Deploy

### Issue: "401 Unauthorized" from Settlor

**Cause:** Missing `x-settlor-customer-id` header or expired token  
**Fix:** Check headers, verify token refresh logic

### Issue: Sync times out

**Cause:** Too much data or timeout too low  
**Fix:** 
- Use partial syncs
- Increase `--timeout` in Procfile
- Set `FETCH_LIMIT` for testing

### Issue: "Property doesn't exist" from HubSpot

**Cause:** Custom properties not created  
**Fix:** Run `/api/test-config` which creates properties, or manually create in HubSpot

### Issue: Associations not created

**Cause:** Missing HubSpot IDs for records  
**Fix:** Ensure companies/contacts sync before deals sync

### Issue: Duplicate records in HubSpot

**Cause:** ID property not set as unique, or sync ran multiple times  
**Fix:** 
- Set `hasUniqueValue: True` on ID properties
- Use batch upsert with idProperty

### Issue: "Email is required" errors

**Cause:** HubSpot Contacts require email  
**Fix:** Generate placeholder emails for invalid/missing emails

### Issue: Dates showing wrong in HubSpot

**Cause:** Not using midnight UTC timestamps  
**Fix:** Use `to_midnight_utc()` function for all date fields

---

## Future Enhancements

### Potential Improvements

1. **Bi-directional Sync**
   - Push HubSpot changes back to Settlor
   - Use HubSpot webhooks for real-time updates

2. **Incremental Associations**
   - Track which associations already exist
   - Only create new associations

3. **Error Recovery**
   - Store failed records for retry
   - Implement dead letter queue

4. **Metrics & Monitoring**
   - Add Prometheus metrics
   - Dashboard for sync health

5. **Parallel Processing**
   - Fetch different entity types in parallel
   - Use async/await for I/O operations

6. **Delta Sync Optimization**
   - Track last successful sync timestamp
   - Only fetch truly modified records

### Known Limitations

1. **Profiles sync disabled by default** - Set `SKIP_PROFILES_SYNC=false` to enable
2. **No delete propagation** - Deleted Settlor records aren't removed from HubSpot
3. **Single-threaded sync** - One sync at a time to avoid conflicts
4. **Memory bound** - Large datasets may exceed Railway memory limits

---

## Quick Reference

### Files Structure
```
app.py              → Flask endpoints
config.py           → Environment variables
settlor_client.py   → Settlor API (OAuth, fetch)
hubspot_client.py   → HubSpot API (upsert, associations)
mappers.py          → Data transformation
sync.py             → Sync orchestration
```

### Key Environment Variables
```
SETTLOR_API_BASE_URL     → https://api.settlor.com
SETTLOR_API_CLIENT_ID    → OAuth client ID
SETTLOR_API_SECRET       → OAuth client secret
SETTLOR_CUSTOMER_ID      → Tenant ID (header)
HUBSPOT_ACCESS_TOKEN     → Private app token
DAYS_TO_FETCH            → 30 (0 = all)
FETCH_LIMIT              → None for production
```

### Common Commands
```bash
# Test locally
python app.py

# Check health
curl https://app.up.railway.app/health

# Trigger sync
curl -X POST -H "X-API-Key: key" https://app.up.railway.app/api/sync

# Check status
curl https://app.up.railway.app/api/status
```

---

*This guide documents the Settlor-HubSpot integration for Independence Title. For API documentation, see `SETTLOR_API_GUIDE.md`.*

