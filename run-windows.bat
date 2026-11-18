@echo off
start "PlayBook API" cmd /k "cd backend && python -m venv .venv && .venv\Scripts\activate && pip install -r requirements.txt && uvicorn main:app --reload --port 8000"
start "PlayBook Frontend" cmd /k "cd frontend && npm install && npm run dev"
echo Backend and frontend terminals are starting. Open http://localhost:5173 after Vite is ready.
