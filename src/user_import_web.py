"""Login-bound import HTTP boundary. Only digests live between requests."""

import hashlib
import secrets
import threading
import time
from contextlib import contextmanager

from flask import Response, jsonify, request, session

from .database import hash_password
from .user_import import ImportProblem, MAX_BYTES, RULES, parse_users, template_bytes

PREFIX = '/api/users/import'
TTL = 300


class UserImport:
    def __init__(self, web):
        self.web = web
        self.previews = {}
        self.slots = threading.BoundedSemaphore(2)
        web.app.add_url_rule(PREFIX + '/<action>', 'user_import', self.dispatch, methods=['POST'])

    def revoke(self, login_id):
        # Always called while the WebManager auth lock is held.
        self.previews.pop(login_id, None)

    @contextmanager
    def auth_guard(self):
        if not self.web._admin_lock.acquire(timeout=2):
            raise ImportProblem('import_busy', 503)
        try:
            yield
        finally:
            self.web._admin_lock.release()

    def prune(self):
        with self.auth_guard():
            now = time.monotonic()
            for login_id, preview in list(self.previews.items()):
                grant = self.web._admin_logins.get(login_id)
                if preview['deadline'] <= now or not grant or grant['expires_at'] <= now:
                    self.revoke(login_id)

    @contextmanager
    def commit_guard(self, epoch=None, deadline=None):
        with self.auth_guard():
            if not self.web._is_admin():
                raise ImportProblem('unauthorized', 401)
            grant = self.web._admin_logins[session['admin_login_id']]
            if epoch is not None and grant.get('import_epoch', 0) != epoch:
                raise ImportProblem('preview_expired', 409)
            if deadline is not None and time.monotonic() >= deadline:
                raise ImportProblem('preview_expired', 409)
            yield

    def read_body(self):
        # request.stream respects WSGI framing. The actual read is bounded even
        # when Content-Length is absent or untrusted; never access request.files.
        if request.mimetype != 'application/octet-stream':
            raise ImportProblem('content_type', 415)
        body = request.stream.read(MAX_BYTES + 1)
        if len(body) > MAX_BYTES or (request.content_length or 0) > MAX_BYTES:
            raise ImportProblem('file_size', 413)
        return body

    def dispatch(self, action):
        login_id = None
        acquired = False
        committed = False
        raw = private = hashed = None
        try:
            self.prune()
            with self.auth_guard():
                if not self.web._is_admin():
                    raise ImportProblem('unauthorized', 401)
                login_id = session.get('admin_login_id')
                grant = self.web._admin_logins[login_id]
                if (request.headers.get('Origin') != request.host_url.rstrip('/')
                        or request.headers.get('X-Import-Request') != '1'):
                    raise ImportProblem('request_forbidden', 403)
                csrf = grant.setdefault('import_csrf', secrets.token_urlsafe(32))
                if action == 'context':
                    return self.response({'csrf': csrf, 'rules': RULES})
                supplied = request.headers.get('X-Import-CSRF', '')
                if not secrets.compare_digest(supplied, csrf):
                    raise ImportProblem('request_forbidden', 403)
                if action == 'cancel':
                    grant['import_epoch'] = grant.get('import_epoch', 0) + 1
                    self.revoke(login_id)
                    return self.response({'cancelled': True})
                previous = None
                if action == 'preview':
                    self.previews.pop(login_id, None)
                    grant['import_epoch'] = grant.get('import_epoch', 0) + 1
                epoch = grant.get('import_epoch', 0)
                if action == 'confirm':
                    previous = self.previews.get(login_id)
                    token = request.headers.get('X-Import-Preview', '')
                    if not previous or not secrets.compare_digest(previous['token'], token):
                        raise ImportProblem('preview_expired', 409)
                    # Consume only the matching token, before body validation/DB waits.
                    self.previews.pop(login_id)
            if not self.slots.acquire(blocking=False):
                raise ImportProblem('import_busy', 503)
            acquired = True
            if action in ('template-csv', 'template-xlsx'):
                kind = action.removeprefix('template-')
                payload = template_bytes(kind)
                with self.commit_guard():
                    response = Response(payload, mimetype='text/csv' if kind == 'csv' else
                                        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
                    response.headers['Content-Disposition'] = f'attachment; filename="users-template.{kind}"'
                    response.headers['Cache-Control'] = 'no-store'
                    response.headers['X-Content-Type-Options'] = 'nosniff'
                    return response
            if action not in ('preview', 'confirm'):
                raise ImportProblem('unknown_action', 404)
            raw = self.read_body()
            kind = request.headers.get('X-Import-Format', '')
            digest = hashlib.sha256(raw).digest()
            if action == 'confirm' and (previous['digest'] != digest or previous['kind'] != kind):
                raise ImportProblem('file_changed', 409)
            private, public = parse_users(raw, kind)
            raw = None
            existing = self.web.db_manager.existing_import_users([r['username'] for r in public if r['username']])
            for row in public:
                if row['username'] in existing:
                    row['errors'].append('account_conflict')
            valid = sum(not row['errors'] for row in public)
            deadline = previous['deadline'] if action == 'confirm' else None
            with self.commit_guard(epoch, deadline):
                if valid != len(public):
                    return self.response({'rows': public, 'valid_count': valid, 'can_import': False}, 400)
                if action == 'preview':
                    if len(self.previews) >= 32:
                        raise ImportProblem('preview_capacity', 503)
                    token = secrets.token_urlsafe(32)
                    response = self.response({'rows': public, 'valid_count': valid,
                                              'can_import': True, 'preview': token, 'expires_in': TTL})
                    self.previews[login_id] = {'token': token, 'digest': digest, 'kind': kind,
                                               'deadline': time.monotonic() + TTL}
                    return response
            # Expensive hashing is outside the database transaction and auth lock.
            hashed = [(username, hash_password(password)) for _, username, password in private]
            private = None
            self.web.db_manager.import_users_atomic(hashed, lambda: self.commit_guard(epoch, deadline))
            committed = True
            # Do not run a post-handler auth check that misreports a committed transaction.
            return self.response({'imported': len(hashed)}, 201)
        except ImportProblem as error:
            # Accepted preview/confirm requests already consumed their old record.
            # Never delete a newer request's preview or mutate state on CSRF denial.
            return self.response({'error': 'commit_result_unknown' if committed else error.code},
                                 503 if committed else error.status)
        except Exception:
            # No exception text, filename, body, account or password in logs/responses.
            return self.response({'error': 'commit_result_unknown' if committed else 'import_failed'},
                                 503 if committed else 400)
        finally:
            raw = private = hashed = None
            if acquired:
                self.slots.release()

    @staticmethod
    def response(payload, status=200):
        response = jsonify(payload)
        response.status_code = status
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response
