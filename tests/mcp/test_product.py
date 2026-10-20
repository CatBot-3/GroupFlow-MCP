"""The public boundary: guarded writes, correction, personal feed access."""
from tests.mcp.test_mcp import Phone, call, client_for, server  # noqa: F401


async def test_guarded_action_retry_and_correction_over_real_mcp(server):  # noqa: F811
    async with client_for(server, 'alice') as client:
        target = await call(client, 'resolve_action', {'query': 'Venue'})
        assert target['outcome'] == 'resolved' and target['targets'][0]['item'] == 'party.venue'
        version = target['targets'][0]['version']
        args = {'action': 'apply', 'tool': 'bring_list', 'arguments': {'plan': 'party', 'action': 'bring', 'things': ['picnic rug']},
                'request_key': 'rug-request', 'expected': version}
        first = await call(client, 'action_receipts', args)
        retry = await call(client, 'action_receipts', args)
        receipt = first['action_receipt']['id']
        assert retry['replayed'] and retry['action_receipt']['id'] == receipt
        refused = await call(client, 'action_receipts', {**args, 'request_key': 'new-request'})
        assert refused['error']['code'] == 'STALE'
    async with client_for(server, 'bob') as client:
        assert (await call(client, 'action_receipts', {'action': 'correct', 'receipt': receipt}))['error']['code'] == 'NOT_FOUND'
    async with client_for(server, 'alice') as client:
        corrected = await call(client, 'action_receipts', {'action': 'correct', 'receipt': receipt})
        assert corrected['action_receipt']['id'] != receipt
        repeated = await call(client, 'action_receipts', {'action': 'correct', 'receipt': receipt})
        assert repeated['outcome'] == 'already_corrected'
    assert server.rt.db.scalar("SELECT claimed_by FROM bring_items WHERE label='picnic rug'") is None


async def test_personal_preferences_and_calendar_require_human_session(server):  # noqa: F811
    alice = await Phone(server, 'alice').login()
    bob = await Phone(server, 'bob').login()
    try:
        path = server.base + '/human/preferences'
        assert (await alice.http.post(path, headers=alice.headers(), json={'plan': 'party', 'muted': True})).status_code == 403
        saved = await alice.http.post(path, headers=alice.headers(True), json={'plan': 'party', 'muted': True, 'digest': True})
        assert saved.status_code == 200 and 'action_receipt' in saved.json()
        bad = await alice.http.post(path, headers=alice.headers(True), json={'plan': 'party', 'quiet_start': 25, 'quiet_end': 8})
        assert bad.status_code == 422
        issued = await alice.http.post(server.base + '/human/calendar', headers=alice.headers(True), json={})
        link = issued.json()
        feed = await alice.http.get(server.base + link['url'])
        assert feed.status_code == 200 and 'BEGIN:VCALENDAR' in feed.text
        assert feed.headers['cache-control'] == 'private, no-store'
        other = await bob.http.post(server.base + '/human/calendar/revoke', headers=bob.headers(True), json={'id': link['id']})
        assert other.status_code == 404
        await alice.http.post(server.base + '/human/calendar/revoke', headers=alice.headers(True), json={'id': link['id']})
        assert (await alice.http.get(server.base + link['url'])).status_code == 404
    finally:
        await alice.http.aclose()
        await bob.http.aclose()
