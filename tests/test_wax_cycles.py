from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app import main


def test_first_wax_is_cycle_one_and_second_completes_first_cycle(db_engine, configured_chain):
    with Session(db_engine) as db:
        chain = db.get(main.Chain, configured_chain["chain_id"])
        assert main.wax_cycle_summary(db, chain.id) == {"treatments": 1, "current_cycle": 1, "completed_cycles": 0}
        chain.km_since_wax = 412.5
        db.add(main.WaxEvent(chain_id=chain.id, wax_product_id=configured_chain["wax_id"],
                             applied_at=datetime(2025, 2, 1, tzinfo=timezone.utc),
                             km_since_previous_wax=412.5, note=None))
        chain.km_since_wax = 0
        db.commit()
        assert main.wax_cycle_summary(db, chain.id) == {"treatments": 2, "current_cycle": 2, "completed_cycles": 1}
        assert main.wax_cycle_records(db)[0]["km"] == 412.5


def test_legacy_initial_wax_preserves_existing_cycle_distance(db_engine, monkeypatch):
    with Session(db_engine) as db:
        spec = main.ChainSpec(name="Legacy Spec", speeds=None, link_count=None, manufacturer=None, model=None)
        person = main.Person(name="Legacy Rider")
        wax = main.WaxProduct(name="Legacy Wax", notes=None, archived=False)
        db.add_all([spec, person, wax]); db.flush()
        bike = main.Bike(person_id=person.id, name="Legacy Bike", strava_gear_id=None,
                         chain_spec_id=spec.id, warning_km=500, change_km=600,
                         overdue_km=800, tracking_start_at=None)
        db.add(bike); db.flush()
        chain = main.Chain(code="LEGACY-01", bike_id=bike.id, chain_spec_id=spec.id,
                           status="READY", first_used_at=None, total_km=250,
                           km_since_wax=125, current_wear_percent=None,
                           last_wear_at=None, retired_at=None)
        db.add(chain); db.commit(); chain_id, wax_id = chain.id, wax.id
    monkeypatch.setattr(main, "engine", db_engine)
    main.record_initial_wax(chain_id, wax_id, "2025-01-01", "")
    with Session(db_engine) as db:
        assert db.get(main.Chain, chain_id).km_since_wax == 125
