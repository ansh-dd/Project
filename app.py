import hashlib
import json
import secrets
import sqlite3
from collections import defaultdict
from http import HTTPStatus
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from wsgiref.simple_server import make_server

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / 'timetable.db'
STATIC_DIR = BASE_DIR / 'static'
TEMPLATE_DIR = BASE_DIR / 'templates'
SESSIONS = {}
PRIORITY_ORDER = {'High': 0, 'Medium': 1, 'Low': 2}


def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 120000).hex()
    return f'{salt}${digest}'


def verify_password(password, stored_value):
    if '$' not in stored_value:
        return stored_value == password
    salt, digest = stored_value.split('$', 1)
    check = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 120000).hex()
    return secrets.compare_digest(check, digest)


def db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db_connection()
    cur = conn.cursor()
    cur.executescript(
        '''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL,
            faculty_id INTEGER
        );
        CREATE TABLE IF NOT EXISTS departments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL
        );
        CREATE TABLE IF NOT EXISTS class_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            department_id INTEGER NOT NULL,
            year TEXT NOT NULL,
            semester INTEGER NOT NULL,
            division TEXT NOT NULL,
            student_count INTEGER NOT NULL,
            UNIQUE(department_id, year, division)
        );
        CREATE TABLE IF NOT EXISTS faculty (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            faculty_code TEXT UNIQUE NOT NULL,
            department_id INTEGER NOT NULL,
            max_lectures_day INTEGER NOT NULL,
            max_lectures_week INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS subjects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            code TEXT UNIQUE NOT NULL,
            type TEXT NOT NULL,
            weekly_lectures INTEGER NOT NULL,
            weekly_lab_sessions INTEGER NOT NULL,
            duration INTEGER NOT NULL,
            priority TEXT NOT NULL,
            department_id INTEGER NOT NULL,
            class_group_id INTEGER NOT NULL,
            primary_faculty_id INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS rooms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_code TEXT UNIQUE NOT NULL,
            type TEXT NOT NULL,
            capacity INTEGER NOT NULL,
            department_id INTEGER,
            supported_subjects TEXT,
            equipment_count INTEGER
        );
        CREATE TABLE IF NOT EXISTS time_slots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            day TEXT NOT NULL,
            slot_index INTEGER NOT NULL,
            label TEXT NOT NULL,
            is_break INTEGER NOT NULL DEFAULT 0,
            UNIQUE(day, slot_index)
        );
        CREATE TABLE IF NOT EXISTS faculty_availability (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            faculty_id INTEGER NOT NULL,
            time_slot_id INTEGER NOT NULL,
            status TEXT NOT NULL,
            UNIQUE(faculty_id, time_slot_id)
        );
        CREATE TABLE IF NOT EXISTS timetable_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            class_group_id INTEGER NOT NULL,
            subject_id INTEGER NOT NULL,
            faculty_id INTEGER NOT NULL,
            room_id INTEGER NOT NULL,
            time_slot_id INTEGER NOT NULL,
            version INTEGER NOT NULL DEFAULT 1
        );
        '''
    )
    conn.commit()

    if cur.execute('SELECT COUNT(*) FROM users').fetchone()[0] == 0:
        cur.executemany('INSERT INTO departments(name) VALUES (?)', [('Computer Engineering',), ('Information Technology',)])
        cse_id = cur.execute("SELECT id FROM departments WHERE name='Computer Engineering'").fetchone()[0]
        it_id = cur.execute("SELECT id FROM departments WHERE name='Information Technology'").fetchone()[0]
        cur.executemany(
            'INSERT INTO class_groups(department_id, year, semester, division, student_count) VALUES (?, ?, ?, ?, ?)',
            [(cse_id, 'SE', 3, 'A', 58), (it_id, 'TE', 5, 'A', 52)]
        )
        se_id = cur.execute("SELECT id FROM class_groups WHERE year='SE'").fetchone()[0]
        te_id = cur.execute("SELECT id FROM class_groups WHERE year='TE'").fetchone()[0]
        cur.executemany(
            'INSERT INTO faculty(name, faculty_code, department_id, max_lectures_day, max_lectures_week) VALUES (?, ?, ?, ?, ?)',
            [('Dr. Meera Shah', 'FAC001', cse_id, 4, 16), ('Prof. Arjun Nair', 'FAC002', it_id, 4, 18)]
        )
        f1 = cur.execute("SELECT id FROM faculty WHERE faculty_code='FAC001'").fetchone()[0]
        f2 = cur.execute("SELECT id FROM faculty WHERE faculty_code='FAC002'").fetchone()[0]
        cur.executemany(
            '''INSERT INTO subjects(name, code, type, weekly_lectures, weekly_lab_sessions, duration, priority, department_id, class_group_id, primary_faculty_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            [
                ('Data Structures', 'CSE201', 'Theory', 3, 0, 1, 'High', cse_id, se_id, f1),
                ('Data Structures Lab', 'CSE201L', 'Lab', 0, 1, 2, 'High', cse_id, se_id, f1),
                ('Database Systems', 'IT301', 'Theory', 3, 0, 1, 'High', it_id, te_id, f2),
                ('Database Lab', 'IT301L', 'Lab', 0, 1, 2, 'Medium', it_id, te_id, f2)
            ]
        )
        cur.executemany(
            'INSERT INTO rooms(room_code, type, capacity, department_id, supported_subjects, equipment_count) VALUES (?, ?, ?, ?, ?, ?)',
            [
                ('CR-101', 'Classroom', 60, cse_id, None, None),
                ('CR-201', 'Classroom', 60, it_id, None, None),
                ('LAB-1', 'Lab', 35, None, 'Programming,Data Structures Lab', 35),
                ('LAB-2', 'Lab', 35, None, 'Database Lab', 35),
            ]
        )
        days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']
        labels = ['09:00-10:00', '10:00-11:00', '11:15-12:15', '12:15-13:15', '14:00-15:00', '15:00-16:00']
        for day in days:
            for idx, label in enumerate(labels):
                cur.execute('INSERT INTO time_slots(day, slot_index, label, is_break) VALUES (?, ?, ?, 0)', (day, idx, label))
        slot_rows = cur.execute('SELECT id, day, slot_index FROM time_slots').fetchall()
        for faculty_id in (f1, f2):
            for slot in slot_rows:
                status = 'Preferred' if slot['slot_index'] in (0, 1) else 'Available'
                if slot['day'] == 'Friday' and slot['slot_index'] == 5:
                    status = 'Unavailable'
                cur.execute('INSERT INTO faculty_availability(faculty_id, time_slot_id, status) VALUES (?, ?, ?)', (faculty_id, slot['id'], status))
        cur.execute("INSERT INTO users(username, password_hash, role) VALUES (?, ?, ?)", ('admin', hash_password('admin123'), 'admin'))
        conn.commit()
    conn.close()


def query_all(sql, params=()):
    conn = db_connection()
    rows = [dict(row) for row in conn.execute(sql, params).fetchall()]
    conn.close()
    return rows


def query_one(sql, params=()):
    conn = db_connection()
    row = conn.execute(sql, params).fetchone()
    conn.close()
    return dict(row) if row else None


def execute(sql, params=()):
    conn = db_connection()
    cur = conn.cursor()
    cur.execute(sql, params)
    conn.commit()
    last_id = cur.lastrowid
    conn.close()
    return last_id


def parse_json(environ):
    try:
        size = int(environ.get('CONTENT_LENGTH') or 0)
    except ValueError:
        size = 0
    raw = environ['wsgi.input'].read(size) if size else b'{}'
    return json.loads(raw.decode() or '{}')


def get_session(environ):
    cookie = SimpleCookie(environ.get('HTTP_COOKIE', ''))
    token = cookie.get('session_id')
    return SESSIONS.get(token.value) if token else None


def json_response(start_response, payload, status=200, headers=None):
    body = json.dumps(payload).encode()
    final_headers = [('Content-Type', 'application/json'), ('Content-Length', str(len(body)))]
    if headers:
        final_headers.extend(headers)
    start_response(f'{status} {HTTPStatus(status).phrase}', final_headers)
    return [body]


def text_response(start_response, body, status=200, content_type='text/html; charset=utf-8'):
    data = body.encode()
    start_response(f'{status} {HTTPStatus(status).phrase}', [('Content-Type', content_type), ('Content-Length', str(len(data)))])
    return [data]


def require_auth(environ, start_response, role=None):
    session = get_session(environ)
    if not session:
        return None, json_response(start_response, {'error': 'Authentication required'}, 401)
    if role and session['role'] != role:
        return None, json_response(start_response, {'error': 'Forbidden'}, 403)
    return session, None


def serialize_dashboard():
    return {
        'departments': query_one('SELECT COUNT(*) AS count FROM departments')['count'],
        'faculty': query_one('SELECT COUNT(*) AS count FROM faculty')['count'],
        'rooms': query_one('SELECT COUNT(*) AS count FROM rooms')['count'],
        'subjects': query_one('SELECT COUNT(*) AS count FROM subjects')['count'],
        'status': 'Generated' if query_one('SELECT COUNT(*) AS count FROM timetable_entries')['count'] else 'Not Generated',
    }


def get_structural_conflicts():
    issues = []
    classroom_caps = [row['capacity'] for row in query_all("SELECT capacity FROM rooms WHERE type='Classroom'")]
    max_classroom = max(classroom_caps) if classroom_caps else 0
    for subject in query_all('''SELECT subjects.code, subjects.type, class_groups.year, class_groups.division, class_groups.student_count
                                  FROM subjects JOIN class_groups ON class_groups.id = subjects.class_group_id'''):
        room_type = 'Lab' if subject['type'] == 'Lab' else 'Classroom'
        count = query_one('SELECT COUNT(*) AS count FROM rooms WHERE type=?', (room_type,))['count']
        if count == 0:
            issues.append(f"No {room_type} available for subject {subject['code']}")
        if room_type == 'Classroom' and subject['student_count'] > max_classroom:
            issues.append(f"No classroom can accommodate {subject['year']}-{subject['division']}")
    return issues


def generate_timetable():
    conn = db_connection()
    conn.execute('DELETE FROM timetable_entries')
    subjects = conn.execute('SELECT * FROM subjects').fetchall()
    slots = conn.execute('SELECT * FROM time_slots WHERE is_break=0 ORDER BY CASE day WHEN "Monday" THEN 1 WHEN "Tuesday" THEN 2 WHEN "Wednesday" THEN 3 WHEN "Thursday" THEN 4 WHEN "Friday" THEN 5 ELSE 6 END, slot_index').fetchall()
    rooms = conn.execute('SELECT * FROM rooms').fetchall()
    faculty_records = {row['id']: row for row in conn.execute('SELECT * FROM faculty').fetchall()}
    class_groups = {row['id']: row for row in conn.execute('SELECT * FROM class_groups').fetchall()}
    availability = defaultdict(dict)
    for row in conn.execute('SELECT * FROM faculty_availability').fetchall():
        availability[row['faculty_id']][row['time_slot_id']] = row['status']

    tasks = []
    for subject in subjects:
        for _ in range(subject['weekly_lab_sessions']):
            tasks.append({'subject': subject, 'length': max(subject['duration'], 2), 'kind': 'Lab'})
        for _ in range(subject['weekly_lectures']):
            tasks.append({'subject': subject, 'length': 1, 'kind': 'Theory'})
    tasks.sort(key=lambda item: (PRIORITY_ORDER.get(item['subject']['priority'], 3), 0 if item['kind'] == 'Lab' else 1))

    class_busy, faculty_busy, room_busy = set(), set(), set()
    faculty_daily = defaultdict(lambda: defaultdict(int))
    faculty_weekly = defaultdict(int)
    placements = []

    def candidate_rooms(subject, length):
        needed_type = 'Lab' if subject['type'] == 'Lab' or length > 1 else 'Classroom'
        class_size = class_groups[subject['class_group_id']]['student_count']
        return [room for room in rooms if room['type'] == needed_type and (needed_type == 'Lab' or room['capacity'] >= class_size)]

    def can_place(task, slot_idx, room):
        subject = task['subject']
        faculty = faculty_records[subject['primary_faculty_id']]
        day = slots[slot_idx]['day']
        if faculty_daily[faculty['id']][day] + task['length'] > faculty['max_lectures_day']:
            return False
        if faculty_weekly[faculty['id']] + task['length'] > faculty['max_lectures_week']:
            return False
        if slot_idx + task['length'] > len(slots):
            return False
        for offset in range(task['length']):
            slot = slots[slot_idx + offset]
            if slot['day'] != day:
                return False
            if availability[faculty['id']].get(slot['id']) == 'Unavailable':
                return False
            if (subject['class_group_id'], slot['id']) in class_busy:
                return False
            if (faculty['id'], slot['id']) in faculty_busy:
                return False
            if (room['id'], slot['id']) in room_busy:
                return False
        return True

    def backtrack(index):
        if index == len(tasks):
            return True
        task = tasks[index]
        subject = task['subject']
        faculty_id = subject['primary_faculty_id']
        slot_order = list(range(len(slots)))
        slot_order.sort(key=lambda idx: 0 if availability[faculty_id].get(slots[idx]['id']) == 'Preferred' else 1)
        for slot_idx in slot_order:
            for room in candidate_rooms(subject, task['length']):
                if not can_place(task, slot_idx, room):
                    continue
                staged = []
                for offset in range(task['length']):
                    slot = slots[slot_idx + offset]
                    class_busy.add((subject['class_group_id'], slot['id']))
                    faculty_busy.add((faculty_id, slot['id']))
                    room_busy.add((room['id'], slot['id']))
                    faculty_daily[faculty_id][slot['day']] += 1
                    faculty_weekly[faculty_id] += 1
                    staged.append(slot)
                placements.append((subject, room, staged))
                if backtrack(index + 1):
                    return True
                placements.pop()
                for slot in staged:
                    class_busy.remove((subject['class_group_id'], slot['id']))
                    faculty_busy.remove((faculty_id, slot['id']))
                    room_busy.remove((room['id'], slot['id']))
                    faculty_daily[faculty_id][slot['day']] -= 1
                    faculty_weekly[faculty_id] -= 1
        return False

    if not backtrack(0):
        conn.close()
        return False, 'Unable to generate a conflict-free timetable with the current constraints.'

    for subject, room, staged_slots in placements:
        for slot in staged_slots:
            conn.execute('INSERT INTO timetable_entries(class_group_id, subject_id, faculty_id, room_id, time_slot_id, version) VALUES (?, ?, ?, ?, ?, 1)',
                         (subject['class_group_id'], subject['id'], subject['primary_faculty_id'], room['id'], slot['id']))
    conn.commit()
    count = conn.execute('SELECT COUNT(*) FROM timetable_entries').fetchone()[0]
    conn.close()
    return True, f'Generated {count} timetable entries successfully.'


def serialize_timetable_entries():
    return query_all('''
        SELECT timetable_entries.id,
               departments.name || ' ' || class_groups.year || '-' || class_groups.division AS class_group,
               subjects.name AS subject,
               faculty.name AS faculty,
               rooms.room_code AS room,
               time_slots.day AS day,
               time_slots.label AS slot,
               timetable_entries.version AS version
        FROM timetable_entries
        JOIN class_groups ON class_groups.id = timetable_entries.class_group_id
        JOIN departments ON departments.id = class_groups.department_id
        JOIN subjects ON subjects.id = timetable_entries.subject_id
        JOIN faculty ON faculty.id = timetable_entries.faculty_id
        JOIN rooms ON rooms.id = timetable_entries.room_id
        JOIN time_slots ON time_slots.id = timetable_entries.time_slot_id
        ORDER BY timetable_entries.time_slot_id
    ''')


def app(environ, start_response):
    path = urlparse(environ['PATH_INFO']).path
    method = environ['REQUEST_METHOD']

    if path == '/':
        return text_response(start_response, (TEMPLATE_DIR / 'index.html').read_text())
    if path.startswith('/static/'):
        file_path = BASE_DIR / path.lstrip('/')
        if not file_path.exists():
            return text_response(start_response, 'Not found', 404, 'text/plain')
        content_type = 'text/css' if file_path.suffix == '.css' else 'text/plain'
        return text_response(start_response, file_path.read_text(), 200, content_type)

    if path == '/api/login' and method == 'POST':
        data = parse_json(environ)
        user = query_one('SELECT * FROM users WHERE username=?', (data.get('username', ''),))
        if not user or not verify_password(data.get('password', ''), user['password_hash']):
            return json_response(start_response, {'error': 'Invalid credentials'}, 401)
        token = secrets.token_hex(16)
        SESSIONS[token] = {'user_id': user['id'], 'role': user['role']}
        return json_response(start_response, {'message': 'Login successful', 'role': user['role']}, headers=[('Set-Cookie', f'session_id={token}; Path=/; HttpOnly')])

    if path == '/api/logout' and method == 'POST':
        session = get_session(environ)
        if session:
            cookie = SimpleCookie(environ.get('HTTP_COOKIE', ''))
            token = cookie.get('session_id')
            if token:
                SESSIONS.pop(token.value, None)
        return json_response(start_response, {'message': 'Logged out'})

    if path == '/api/dashboard' and method == 'GET':
        _, error = require_auth(environ, start_response)
        if error:
            return error
        return json_response(start_response, serialize_dashboard())

    admin_paths = {'/api/departments', '/api/classes', '/api/faculty', '/api/subjects', '/api/rooms', '/api/generate-timetable', '/api/reset-timetable', '/api/conflicts'}
    if path in admin_paths:
        _, error = require_auth(environ, start_response, 'admin')
        if error:
            return error

    if path in {'/api/timeslots', '/api/timetable', '/api/export'}:
        _, error = require_auth(environ, start_response)
        if error:
            return error

    if path == '/api/departments':
        if method == 'GET':
            return json_response(start_response, query_all('SELECT id, name FROM departments ORDER BY name'))
        data = parse_json(environ)
        new_id = execute('INSERT INTO departments(name) VALUES (?)', (data['name'],))
        return json_response(start_response, {'id': new_id, 'name': data['name']}, 201)

    if path == '/api/classes':
        if method == 'GET':
            return json_response(start_response, query_all('''SELECT class_groups.id, departments.name AS department, year, semester, division, student_count
                                                              FROM class_groups JOIN departments ON departments.id = class_groups.department_id
                                                              ORDER BY departments.name, year, division'''))
        data = parse_json(environ)
        new_id = execute('INSERT INTO class_groups(department_id, year, semester, division, student_count) VALUES (?, ?, ?, ?, ?)',
                         (data['department_id'], data['year'], data['semester'], data['division'], data['student_count']))
        return json_response(start_response, {'id': new_id}, 201)

    if path == '/api/faculty':
        if method == 'GET':
            return json_response(start_response, query_all('''SELECT faculty.id, faculty.name, faculty_code, departments.name AS department,
                                                              max_lectures_day, max_lectures_week
                                                              FROM faculty JOIN departments ON departments.id = faculty.department_id
                                                              ORDER BY faculty.name'''))
        data = parse_json(environ)
        new_id = execute('INSERT INTO faculty(name, faculty_code, department_id, max_lectures_day, max_lectures_week) VALUES (?, ?, ?, ?, ?)',
                         (data['name'], data['faculty_code'], data['department_id'], data['max_lectures_day'], data['max_lectures_week']))
        return json_response(start_response, {'id': new_id}, 201)

    if path == '/api/subjects':
        if method == 'GET':
            return json_response(start_response, query_all('''SELECT subjects.id, subjects.name, code, type, weekly_lectures, weekly_lab_sessions,
                                                              duration, priority, departments.name AS department,
                                                              class_groups.year || '-' || class_groups.division AS class_group,
                                                              faculty.name AS faculty
                                                              FROM subjects
                                                              JOIN departments ON departments.id = subjects.department_id
                                                              JOIN class_groups ON class_groups.id = subjects.class_group_id
                                                              JOIN faculty ON faculty.id = subjects.primary_faculty_id
                                                              ORDER BY subjects.name'''))
        data = parse_json(environ)
        new_id = execute('''INSERT INTO subjects(name, code, type, weekly_lectures, weekly_lab_sessions, duration, priority, department_id, class_group_id, primary_faculty_id)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                         (data['name'], data['code'], data['type'], data['weekly_lectures'], data['weekly_lab_sessions'], data['duration'], data['priority'], data['department_id'], data['class_group_id'], data['primary_faculty_id']))
        return json_response(start_response, {'id': new_id}, 201)

    if path == '/api/rooms':
        if method == 'GET':
            return json_response(start_response, query_all('SELECT id, room_code, type, capacity, supported_subjects, equipment_count FROM rooms ORDER BY room_code'))
        data = parse_json(environ)
        new_id = execute('INSERT INTO rooms(room_code, type, capacity, department_id, supported_subjects, equipment_count) VALUES (?, ?, ?, ?, ?, ?)',
                         (data['room_code'], data['type'], data['capacity'], data.get('department_id'), data.get('supported_subjects'), data.get('equipment_count')))
        return json_response(start_response, {'id': new_id}, 201)

    if path == '/api/timeslots':
        return json_response(start_response, query_all('SELECT id, day, slot_index, label, is_break FROM time_slots ORDER BY day, slot_index'))

    if path == '/api/generate-timetable' and method == 'POST':
        ok, message = generate_timetable()
        return json_response(start_response, {'message': message}, 200 if ok else 409)

    if path == '/api/reset-timetable' and method == 'POST':
        execute('DELETE FROM timetable_entries')
        return json_response(start_response, {'message': 'Timetable reset complete.'})

    if path == '/api/conflicts':
        return json_response(start_response, {'issues': get_structural_conflicts()})

    if path == '/api/timetable':
        entries = serialize_timetable_entries()
        return json_response(start_response, {'entries': entries})

    if path == '/api/export':
        return json_response(start_response, {'entries': serialize_timetable_entries()})

    return text_response(start_response, 'Not found', 404, 'text/plain')


init_db()


if __name__ == '__main__':
    with make_server('0.0.0.0', 5000, app) as server:
        print('Serving on http://0.0.0.0:5000')
        server.serve_forever()
