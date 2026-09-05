from sqlalchemy.orm import Session

from app import main


def test_swap_and_wax_follow_explicit_state_transitions(db_engine, configured_chain, monkeypatch):
    with Session(db_engine) as db:
        active = db.get(main.Chain, configured_chain["chain_id"])
        spare = main.Chain(code="EXAMPLE-02", bike_id=active.bike_id,
                           chain_spec_id=active.chain_spec_id, status="READY",
                           first_used_at=None, total_km=0, km_since_wax=0,
                           current_wear_percent=None, last_wear_at=None, retired_at=None)
        db.add(spare); db.flush()
        db.add(main.WaxEvent(chain_id=spare.id, wax_product_id=configured_chain["wax_id"],
                             applied_at=main.now(), km_since_previous_wax=0, note=None))
        db.commit(); spare_id = spare.id

    monkeypatch.setattr(main, "engine", db_engine)
    main.swap_chain(configured_chain["bike_id"], spare_id)
    with Session(db_engine) as db:
        assert db.get(main.Chain, configured_chain["chain_id"]).status == "NEEDS_WAX"
        assert db.get(main.Chain, spare_id).status == "IN_USE"

    main.add_wear(configured_chain["chain_id"], 0.25, "before_wax", "")
    main.add_wax(configured_chain["chain_id"], configured_chain["wax_id"], "", False)
    with Session(db_engine) as db:
        removed = db.get(main.Chain, configured_chain["chain_id"])
        assert removed.status == "READY"
        assert main.wax_cycle_summary(db, removed.id)["current_cycle"] == 2

