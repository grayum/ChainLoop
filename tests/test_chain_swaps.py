"""Swap ordering and atomicity against SQLite's real partial unique index."""
import re
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select, text
from sqlalchemy.orm import Session

from app import main

FIRST_USE = datetime(2025, 1, 1, tzinfo=timezone.utc)
SWAP_TIME = datetime(2026, 11, 22, 10, tzinfo=timezone.utc)


@pytest.fixture
def client(db_engine, monkeypatch):
    monkeypatch.setattr(main, 'engine', db_engine)
    with TestClient(main.app, base_url='https://testserver') as value:
        yield value


def prepare_swap(db_engine, configured_chain, active_id, previously_used):
    assert configured_chain['chain_id'] == 1
    with Session(db_engine) as db:
        original = db.get(main.Chain, 1)
        for chain_id, status in [(2, 'NEEDS_WAX'), (3, 'READY')]:
            db.add(main.Chain(id=chain_id, code=f'SAMPLE-{chain_id:02}',
                              bike_id=original.bike_id, chain_spec_id=original.chain_spec_id,
                              status=status, total_km=0, km_since_wax=0))
            db.add(main.WaxEvent(chain_id=chain_id, wax_product_id=configured_chain['wax_id'],
                                applied_at=FIRST_USE, km_since_previous_wax=0))
        db.flush()
        if active_id == 3:
            original.status = 'READY'
            db.flush()  # Fixture setup must also respect the unique active-chain index.
            db.get(main.Chain, 3).status = 'IN_USE'
        incoming_id = 3 if active_id == 1 else 1
        db.get(main.Chain, active_id).first_used_at = FIRST_USE
        db.get(main.Chain, incoming_id).first_used_at = FIRST_USE if previously_used else None
        db.commit()
    return incoming_id


def post_swap(client, bike_id, chain_id):
    dashboard = client.get('/')
    assert dashboard.status_code == 200
    token = re.search(r'name="csrf_token" value="([^"]+)"', dashboard.text)[1]
    return client.post(f'/bikes/{bike_id}/swap',
                       data={'csrf_token': token, 'chain_id': chain_id}, follow_redirects=False)


@pytest.mark.parametrize('active_id', [1, 3], ids=['1-to-3', '3-to-1'])
@pytest.mark.parametrize('previously_used', [False, True], ids=['first-install', 'reinstall'])
def test_swap_in_both_id_directions_commits_once(
    client, db_engine, configured_chain, monkeypatch, active_id, previously_used,
):
    incoming_id = prepare_swap(db_engine, configured_chain, active_id, previously_used)
    monkeypatch.setattr(main, 'now', lambda: SWAP_TIME)
    commits = []
    integrity_failures = []

    def committed(connection):
        commits.append(True)

    def failed(context):
        integrity_failures.append(str(context.original_exception))

    event.listen(db_engine, 'commit', committed)
    event.listen(db_engine, 'handle_error', failed)
    try:
        response = post_swap(client, configured_chain['bike_id'], incoming_id)
    finally:
        event.remove(db_engine, 'commit', committed)
        event.remove(db_engine, 'handle_error', failed)
    assert response.status_code == 303, (response.text, integrity_failures)
    assert response.headers['location'] == '/'
    assert len(commits) == 1
    with Session(db_engine) as db:
        active = db.scalars(select(main.Chain).where(
            main.Chain.bike_id == configured_chain['bike_id'], main.Chain.status == 'IN_USE')).all()
        assert [chain.id for chain in active] == [incoming_id]
        assert db.get(main.Chain, active_id).status == 'NEEDS_WAX'
        assert db.get(main.Chain, 2).status == 'NEEDS_WAX'
        assert main.ensure_activity_datetime(db.get(main.Chain, active_id).first_used_at) == FIRST_USE
        expected = FIRST_USE if previously_used else SWAP_TIME
        assert main.ensure_activity_datetime(db.get(main.Chain, incoming_id).first_used_at) == expected
        assert db.scalar(select(func.count(main.Event.id))) == 2
        events = db.execute(select(main.Event.event_type, main.Event.chain_id, main.Event.bike_id)
                            .order_by(main.Event.id)).all()
        assert events == [('CHAIN_REMOVED', active_id, configured_chain['bike_id']),
                          ('CHAIN_INSTALLED', incoming_id, configured_chain['bike_id'])]
        assert all(chain.total_km == chain.km_since_wax == 0 for chain in db.scalars(select(main.Chain)))
    assert client.get('/').status_code == 200


@pytest.mark.parametrize('active_id', [1, 3], ids=['1-to-3', '3-to-1'])
def test_failure_after_removal_flush_rolls_back_state_and_event(
    client, db_engine, configured_chain, monkeypatch, active_id,
):
    incoming_id = prepare_swap(db_engine, configured_chain, active_id, previously_used=False)
    original_record_event = main.record_event
    reached_install = []

    def fail_install(db, event_type, **kwargs):
        if event_type == 'CHAIN_INSTALLED':
            # Inspect SQL state without autoflushing the incoming installation.
            with db.no_autoflush:
                assert db.scalar(text('SELECT status FROM chains WHERE id=:id'),
                                 {'id': active_id}) == 'NEEDS_WAX'
                assert db.scalar(text("SELECT count(*) FROM events WHERE event_type='CHAIN_REMOVED'")) == 1
            # A separate reader must still see the previously committed state.
            with Session(db_engine) as reader:
                assert reader.get(main.Chain, active_id).status == 'IN_USE'
                assert reader.scalar(select(func.count(main.Event.id))) == 0
            reached_install.append(True)
            raise HTTPException(409, 'Synthetic installation conflict')
        original_record_event(db, event_type, **kwargs)

    monkeypatch.setattr(main, 'record_event', fail_install)
    response = post_swap(client, configured_chain['bike_id'], incoming_id)
    assert response.status_code == 409
    assert response.json() == {'detail': 'Synthetic installation conflict'}
    assert reached_install == [True]
    with Session(db_engine) as db:
        assert db.get(main.Chain, active_id).status == 'IN_USE'
        assert db.get(main.Chain, incoming_id).status == 'READY'
        assert db.get(main.Chain, incoming_id).first_used_at is None
        assert db.scalar(select(func.count(main.Event.id))) == 0
        assert db.scalar(select(func.count(main.Chain.id)).where(main.Chain.status == 'IN_USE')) == 1
