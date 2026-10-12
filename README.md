# Employee Attendance & Analytics API

A FastAPI and MongoDB API for employee records, attendance punch-in/punch-out, audited corrections, and attendance analytics.

## Requirements
- Python 3.11 or newer
- MongoDB 6.0 or newer (local MongoDB or MongoDB Atlas)

## Setup

Create and activate a virtual environment, then install dependencies.

Windows PowerShell (from the `candidate_kit` project root):

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Configure the database through environment variables `MONGO_URI` and `MONGO_DB`. For local development, a `.env` file can be used, but **do not commit it**. Environment variables should take precedence over values loaded from `.env`.

Example `.env` for local use (replace with your own local values; never commit credentials):

```dotenv
MONGO_URI=mongodb://localhost:27017
MONGO_DB=attendance_db
```

Start the application from the project root:

```powershell
python -m uvicorn app.main:app --reload --port 8000
```

- Health check: `GET /health`
- Interactive API docs: `http://127.0.0.1:8000/docs`

## Main API areas
- Employee creation and listing
- Attendance punch-in and punch-out
- Attendance listing and manual correction with audit history
- Monthly employee analytics
- Department summary and daily trend
- Late-arrival leaderboard
- Admin Explain endpoint for query-plan inspection

Use `openapi.yaml` from the starter kit as the API contract.

## Before submitting
1. Run the app against a MongoDB 6+ instance.
2. Verify the response shapes and validation behaviour against `openapi.yaml`.
3. Check aggregation and Explain endpoints against representative records, including weekends, employees without attendance logs, and tied leaderboard scores.
4. Confirm `.env`, credentials, virtual environments, database dumps, and other secrets are not tracked by Git.
5. Add the repository's final test results and accurately disclose any remaining limitations here.

## Known verification limitation
The source file has been checked for Python syntax and the health endpoint has been tested in the local setup. A syntax check and health check do not prove full contract compliance; run the full endpoint/aggregation verification against your own MongoDB instance before submitting.
