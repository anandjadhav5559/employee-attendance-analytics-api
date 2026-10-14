A REST API built with Python, FastAPI, and MongoDB for employee management, attendance punch-in/punch-out, attendance regularization with audit history, and attendance analytics.

Requirements

Python 3.11+

MongoDB 6.0+

Git

Setup

Create and activate a virtual environment:

python -m venv .venv
.\.venv\Scripts\Activate.ps1

Install dependencies:

pip install -r requirements.txt

Configure the environment variables in a local .env file:

MONGO_URI=mongodb://localhost:27017
MONGO_DB=attendance_db

Do not commit .env or any credentials to the repository.

Start the API from the repository root:

uvicorn app.main:app --port 8000

API Documentation

Once the application is running:

Health check: http://127.0.0.1:8000/health

Interactive API documentation: http://127.0.0.1:8000/docs

Main Features

Employee creation and paginated listing

Attendance punch-in and punch-out

Attendance regularization with an audit trail

Employee monthly attendance analytics

Department summary and attendance trends

Late employee leaderboard

MongoDB indexes and query explain endpoint

Notes

The API follows the supplied OpenAPI contract and attendance business rules. Analytics use MongoDB aggregation pipelines and stored attendance values.

