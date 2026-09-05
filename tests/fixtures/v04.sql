CREATE TABLE people (id INTEGER PRIMARY KEY, name VARCHAR NOT NULL);
CREATE TABLE chain_specs (id INTEGER PRIMARY KEY, name VARCHAR NOT NULL, speeds INTEGER, link_count INTEGER, manufacturer VARCHAR, model VARCHAR);
CREATE TABLE bikes (id INTEGER PRIMARY KEY, person_id INTEGER, name VARCHAR NOT NULL, strava_gear_id VARCHAR UNIQUE, chain_spec_id INTEGER, warning_km FLOAT NOT NULL, change_km FLOAT NOT NULL, overdue_km FLOAT NOT NULL);
CREATE TABLE wax_products (id INTEGER PRIMARY KEY, name VARCHAR NOT NULL UNIQUE, notes VARCHAR);
CREATE TABLE chains (id INTEGER PRIMARY KEY, code VARCHAR NOT NULL UNIQUE, bike_id INTEGER NOT NULL, chain_spec_id INTEGER NOT NULL, status VARCHAR NOT NULL, first_used_at DATETIME, total_km FLOAT NOT NULL, km_since_wax FLOAT NOT NULL, current_wear_percent FLOAT, last_wear_at DATETIME, retired_at DATETIME);
CREATE TABLE activities (id INTEGER PRIMARY KEY, source VARCHAR NOT NULL, external_id VARCHAR NOT NULL, occurred_at DATETIME NOT NULL, distance_km FLOAT NOT NULL, bike_id INTEGER, processed BOOLEAN NOT NULL, excluded BOOLEAN NOT NULL, raw_gear_id VARCHAR, raw_name VARCHAR);
CREATE TABLE events (id INTEGER PRIMARY KEY, created_at DATETIME NOT NULL, event_type VARCHAR NOT NULL, chain_id INTEGER, bike_id INTEGER, activity_id INTEGER, distance_km FLOAT, note VARCHAR, metadata_json VARCHAR);
CREATE TABLE wear_measurements (id INTEGER PRIMARY KEY, chain_id INTEGER NOT NULL, measured_at DATETIME NOT NULL, wear_percent FLOAT NOT NULL, timing VARCHAR NOT NULL, note VARCHAR);
CREATE TABLE wax_events (id INTEGER PRIMARY KEY, chain_id INTEGER NOT NULL, wax_product_id INTEGER NOT NULL, applied_at DATETIME NOT NULL, km_since_previous_wax FLOAT NOT NULL, note VARCHAR);

INSERT INTO people VALUES (1, 'Example Rider');
INSERT INTO chain_specs VALUES (1, 'Example Spec', 12, 116, NULL, NULL);
INSERT INTO bikes VALUES (1, 1, 'Example Bike', 'gear-example', 1, 500, 600, 800);
INSERT INTO wax_products VALUES (1, 'Example Wax', NULL);
INSERT INTO chains VALUES (1, 'EXAMPLE-01', 1, 1, 'IN_USE', '2024-01-01 00:00:00', 650, 650, NULL, NULL, NULL);
INSERT INTO activities VALUES (1, 'strava', 'ride-example', '2024-02-01 00:00:00', 25, 1, 1, 0, 'gear-example', 'Example Ride');
INSERT INTO wax_events VALUES (1, 1, 1, '2024-01-01 00:00:00', 0, NULL);
INSERT INTO events VALUES (1, '2024-02-01 00:00:00', 'RIDE_ADDED', 1, 1, 1, 25, NULL, NULL);
INSERT INTO events VALUES (2, '2024-02-02 00:00:00', 'PUSHOVER_WARNING_SENT', 1, 1, NULL, NULL, NULL, NULL);

