# Valorant Shop Checker

Check your Valorant daily store from any device, without launching the game.

A web application that lets Valorant players view their personalized daily shop rotation, featured bundles, and wallet balance from a browser. Built with a Python/FastAPI backend that handles Riot's OAuth authentication flow, and a React/TypeScript frontend with a Valorant-themed UI.

> **Disclaimer:** This application is not endorsed by Riot Games. Your Riot credentials are transmitted directly to Riot's authentication servers over HTTPS and are never stored by this application.

## Features

- **Daily Store** — View your 4 daily rotating skin offers with tier indicators and VP prices
- **Featured Bundles** — See current bundles with item breakdowns and discount pricing
- **Wallet Balance** — Check your VP and Radianite Points
- **Auto-Refresh** — Countdown timer with automatic store refresh on rotation
- **Secure Auth** — OAuth redirect flow; your password never touches this app

## Project Structure

```
valorant-shop-checker/
├── backend/            # Python/FastAPI API server
│   ├── app/
│   │   ├── routers/    # API route handlers (auth, store)
│   │   ├── services/   # Riot API integration, asset cache
│   │   └── models/     # Pydantic/dataclass models
│   ├── Dockerfile
│   └── pyproject.toml
├── frontend/           # React/TypeScript/Vite UI
│   ├── src/
│   │   ├── pages/      # LoginPage, ShopPage
│   │   ├── components/ # SkinCard, BundleCard, etc.
│   │   ├── api/        # API client
│   │   └── context/    # Auth state management
│   └── package.json
└── README.md
```

## Local Development Setup

### Prerequisites

- Python 3.12+
- Node.js 18+
- Port 80 available (required for Riot's OAuth redirect)

### Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate    # Linux/macOS
# .venv\Scripts\activate     # Windows

pip install -e .
cp .env.example .env         # then edit with your values
uvicorn app.main:app --reload
```

The API server runs at `http://localhost:8000`.

> **Note:** The backend starts an HTTP server on port 80 to handle Riot's OAuth redirect. On Linux/macOS this may require elevated privileges. On Windows, ensure port 80 is not in use by another service.

### Frontend

```bash
cd frontend
npm install
npm run dev
```

The dev server runs at `http://localhost:5173` and proxies `/api` requests to the backend.

### Running with Docker (backend only)

```bash
cd backend
docker build -t valorant-shop-backend .
docker run -p 8000:8000 -p 80:80 --env-file .env valorant-shop-backend
```

## Environment Variables

### Backend (`backend/.env`)

| Variable | Description | Default |
|---|---|---|
| `ENCRYPTION_KEY` | Fernet key for session encryption. Generate with: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` | _(empty)_ |
| `ALLOWED_ORIGINS` | Comma-separated CORS origins | `http://localhost:5173,http://localhost:3000,http://localhost` |
| `ENVIRONMENT` | `development` or `production` | `development` |
| `API_URL` | Backend URL used by OAuth redirect page | `http://localhost:8000` |

### Frontend

| Variable | Description | Default |
|---|---|---|
| `VITE_API_URL` | Backend API base URL (only needed if not using Vite proxy) | _(empty — uses Vite proxy)_ |

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/health` | Health check |
| `POST` | `/api/auth/start` | Start OAuth flow, returns Riot auth URL |
| `POST` | `/api/auth/callback` | Receives tokens from OAuth redirect |
| `GET` | `/api/auth/poll` | Poll for auth completion |
| `GET` | `/api/auth/session` | Check if session is valid |
| `POST` | `/api/auth/logout` | Clear session |
| `GET` | `/api/store/daily` | Get daily store offers |
| `GET` | `/api/store/bundle` | Get featured bundles |
| `GET` | `/api/store/wallet` | Get VP and Radianite balance |

## Tech Stack

- **Backend:** Python 3.12, FastAPI, httpx, uvicorn
- **Frontend:** React 19, TypeScript, Vite, Tailwind CSS 4
- **Auth:** Riot Games OAuth 2.0 implicit flow
- **Data:** valorant-api.com (skin/bundle asset data)
