import json
import unittest
from wsgiref.util import setup_testing_defaults

from app import app, DB_PATH, init_db, query_one


class TimetableAppTestCase(unittest.TestCase):
    def setUp(self):
        init_db()

    def wsgi_request(self, path='/', method='GET', body=None, cookie=None):
        environ = {}
        setup_testing_defaults(environ)
        environ['PATH_INFO'] = path
        environ['REQUEST_METHOD'] = method
        payload = json.dumps(body or {}).encode()
        environ['CONTENT_LENGTH'] = str(len(payload))
        environ['wsgi.input'] = __import__('io').BytesIO(payload)
        if cookie:
            environ['HTTP_COOKIE'] = cookie
        captured = {}

        def start_response(status, headers):
            captured['status'] = status
            captured['headers'] = headers

        response = b''.join(app(environ, start_response))
        return captured, response

    def login(self):
        captured, response = self.wsgi_request('/api/login', 'POST', {'username': 'admin', 'password': 'admin123'})
        self.assertTrue(captured['status'].startswith('200'))
        cookie = next(value for key, value in captured['headers'] if key == 'Set-Cookie')
        return cookie.split(';', 1)[0]

    def test_dashboard_requires_auth(self):
        captured, _ = self.wsgi_request('/api/dashboard')
        self.assertTrue(captured['status'].startswith('401'))

    def test_generate_timetable(self):
        cookie = self.login()
        captured, response = self.wsgi_request('/api/generate-timetable', 'POST', {}, cookie)
        self.assertTrue(captured['status'].startswith('200'))
        data = json.loads(response.decode())
        self.assertIn('Generated', data['message'])
        total = query_one('SELECT COUNT(*) AS count FROM timetable_entries')['count']
        self.assertGreater(total, 0)


if __name__ == '__main__':
    unittest.main()
