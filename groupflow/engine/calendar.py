"""Revocable personal iCalendar publication; no external account connections."""
from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from datetime import datetime, timezone
from groupflow.repair.local_time import local_instant, validate_interval

from groupflow.core.errors import DomainError
from .product import fingerprint
from .agreement import SATISFIED


def escape(value: str) -> str:
    return value.replace('\\', '\\\\').replace('\n', '\\n').replace('\r', '').replace(';', '\\;').replace(',', '\\,')


def fold(line: str) -> str:
    parts, chunk = [], ''
    for char in line:
        if len((chunk + char).encode('utf8')) > 75:
            parts.append(chunk)
            chunk = ' '
        chunk += char
    return '\r\n'.join(parts + [chunk])


def utc_local(day: str, minute: int, zone: str, occurrence: int | None = None) -> str:
    return local_instant(day, minute, zone, occurrence).strftime('%Y%m%dT%H%M%SZ')


class Calendar:
    def issue_calendar(self, actor_id: str) -> dict:
        self.scope_for(actor_id)
        token = secrets.token_urlsafe(32)
        ident = self.new_id('feed')
        self.db.run('INSERT INTO calendar_links (id,member_id,token_hash,created_at) VALUES (?,?,?,?)',
                    ident, actor_id, hashlib.sha256(token.encode()).hexdigest(), self.ts())
        return {'id': ident, 'url': f'/calendar/{token}.ics', 'message': 'Private subscription created. Anyone holding this link can read your agreed calendar until you revoke it.'}

    def revoke_calendar(self, actor_id: str, ident: str) -> dict:
        if not self.db.get('SELECT id FROM calendar_links WHERE id=? AND member_id=?', ident, actor_id):
            raise DomainError('NOT_FOUND', 'No such personal calendar link.')
        self.db.run('UPDATE calendar_links SET revoked_at=? WHERE id=?', self.ts(), ident)
        return {'outcome': 'revoked', 'message': 'Calendar link revoked.'}

    def calendar_feed(self, token: str) -> str:
        def run():
            link = self.db.get('SELECT * FROM calendar_links WHERE token_hash=? AND revoked_at IS NULL', hashlib.sha256(token.encode()).hexdigest())
            if not link:
                raise DomainError('NOT_FOUND', 'No active calendar subscription.')
            member = link['member_id']
            scope = self.scope_for(member)
            current = {}
            for p in scope.plans.values():
                if not p['active_revision_id'] or p['status'] == 'CANCELLED':
                    continue
                revision = self._revision(p['active_revision_id'])
                snap = self._snapshot(revision)
                own_scopes = [(req, status) for req, status, _ in self._requirements(revision) if req['member_id'] == member]
                catalog = self._catalog(p)
                for a in snap.activities:
                    if a.cancelled or member not in a.participants:
                        continue
                    required = [status for req, status in own_scopes if req['scope_kind'] == 'whole_plan' or req['activity_id'] == a.id]
                    if not required or any(status not in SATISFIED for status in required):
                        continue
                    validate_interval(snap.start_date,a.start,a.end,snap.timezone,a.start_fold,a.end_fold)
                    current[(p['id'], a.id)] = {'summary': p['title'] + ': ' + a.title,
                        'location': catalog.places[a.place_id].name if a.place_id in catalog.places else a.place_name or '',
                        'start': utc_local(snap.start_date, a.start, snap.timezone, a.start_fold),
                        'end': utc_local(snap.start_date, a.end, snap.timezone, a.end_fold), 'status': 'CONFIRMED'}
            old = {(r['plan_id'], r['activity_id']): r for r in self.db.all('SELECT * FROM calendar_events WHERE member_id=?', member)}
            for key in old.keys() - current.keys():
                prior = json.loads(old[key]['payload_json'])
                current[key] = {'start': prior['start'], 'end': prior['end'], 'status': 'CANCELLED'}
            for key, payload in current.items():
                row = old.get(key)
                sig = fingerprint(payload)
                if row and sig == row['fingerprint']:
                    continue
                uid = row['uid'] if row else str(uuid.uuid5(uuid.NAMESPACE_URL, 'groupflow:' + member + ':' + ':'.join(key))) + '@groupflow'
                seq = row['sequence'] + 1 if row else 0
                self.db.run('INSERT INTO calendar_events VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(member_id,plan_id,activity_id) DO UPDATE SET sequence=excluded.sequence,fingerprint=excluded.fingerprint,payload_json=excluded.payload_json,changed_at=excluded.changed_at',
                            member, *key, uid, seq, sig, json.dumps(payload), self.ts())
            lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//GroupFlow//Personal plans//EN', 'CALSCALE:GREGORIAN', 'METHOD:PUBLISH']
            for r in self.db.all('SELECT * FROM calendar_events WHERE member_id=? ORDER BY uid', member):
                payload = json.loads(r['payload_json'])
                stamp = datetime.fromisoformat(r['changed_at'].replace('Z', '+00:00')).astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
                lines += ['BEGIN:VEVENT', 'UID:' + r['uid'], 'SEQUENCE:' + str(r['sequence']), 'DTSTAMP:' + stamp,
                          'DTSTART:' + payload['start'], 'DTEND:' + payload['end'], 'STATUS:' + payload['status']]
                if payload['status'] != 'CANCELLED':
                    lines += ['SUMMARY:' + escape(payload['summary']), 'LOCATION:' + escape(payload['location'])]
                lines.append('END:VEVENT')
            lines.append('END:VCALENDAR')
            return '\r\n'.join(fold(line) for line in lines) + '\r\n'
        return self.db.tx(run)
