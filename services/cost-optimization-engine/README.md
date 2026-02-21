# Toolkit Cost Optimization Engine

Deprecated standalone location: this service now lives under `production-ready/cost-latency-optimizer/services/cost-optimization-engine`.

Toolkit Cost Optimization Engine is a FastAPI service for tracking cloud spend, analyzing cost trends, and generating optimization recommendations.

## What It Does

- Store cloud account metadata and cost/usage data in PostgreSQL.
- Generate optimization recommendations (right-size, schedule, terminate, reserved instances).
- Provide cost metrics, trends, and anomaly detection APIs.
- Expose Prometheus metrics at `/metrics`.

## Quick Start

```bash
cd enterprise-tools/oss/cost-latency-optimizer/services/cost-optimization-engine
python -m venv .venv
. .venv/Scripts/activate
pip install -e ".[dev]"

# Point to PostgreSQL
set DATABASE_URL=postgresql://user:pass@localhost:5432/cost_optimization_engine

# Start the API
toolkit-cost-optimizer serve --host 0.0.0.0 --port 8005
```

Tables are created on startup via SQLAlchemy metadata.

## Core Endpoints

- `GET /health` health check
- `GET /status` system status summary
- `GET /metrics` Prometheus metrics
- `POST /api/v1/accounts` create cloud account
- `GET /api/v1/accounts` list accounts
- `POST /api/v1/accounts/{account_id}/sync` sync cost data
- `GET /api/v1/accounts/{account_id}/metrics` cost metrics
- `GET /api/v1/accounts/{account_id}/analysis` resource analysis
- `GET /api/v1/accounts/{account_id}/anomalies` anomaly detection
- `GET /api/v1/accounts/{account_id}/trends` cost trends
- `POST /api/v1/accounts/{account_id}/recommendations/generate` generate recommendations
- `GET /api/v1/accounts/{account_id}/recommendations` list recommendations

## Configuration

Common environment variables:

- `DATABASE_URL` (required) PostgreSQL connection string
- `ENVIRONMENT` (default: `development`)
- `LOG_LEVEL` (default: `INFO`)
- `CORS_ORIGINS` (comma-separated)

Optional:

- `SECRET_KEY` and `JWT_SECRET_KEY` are reserved for a future auth layer.
- Provider credentials (AWS/Azure/GCP) are read from the account records.

## Limitations

- PostgreSQL is required (models use UUID/JSONB types).
- AWS cost/usage sync is implemented with boto3; Azure/GCP clients are stubs.
- Recommendations use heuristic logic and simulated savings percentages.
- No authentication layer is enabled in this service.

## Development

```bash
pytest -q
```

## License

MIT. See `LICENSE`.

