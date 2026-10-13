"""FastAPI application entry point."""

try:
    from fastapi import FastAPI
except ImportError:  # Optional until backend dependencies are installed.
    FastAPI = None

app = FastAPI(title="Poltergeist") if FastAPI else None
