# AI-powered College Timetable Management System

A Python standard-library timetable management web app with:

- secure admin login using PBKDF2-hashed passwords and cookie-backed sessions
- department, class, faculty, subject, room, and timeslot APIs
- a backtracking-based timetable generation engine with hard and soft constraints
- a responsive HTML/CSS dashboard for generating and reviewing timetables

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Login with seeded credentials:

- username: `admin`
- password: `admin123`

## Run tests

```bash
python3 -m unittest discover -s tests
```
