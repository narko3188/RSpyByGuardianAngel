-- SerbiaTracker - PostgreSQL + PostGIS Migration Script
-- Remplace SQLite par PostgreSQL pour meilleures performances spatiales

-- ============================================
-- TABLES PRINCIPALES
-- ============================================

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- Antennes relais (PostGIS geometry)
CREATE TABLE IF NOT EXISTS cell_towers (
    id BIGSERIAL PRIMARY KEY,
    radio VARCHAR(10),
    mcc INTEGER NOT NULL,
    mnc INTEGER NOT NULL,
    lac INTEGER NOT NULL,
    cell_id INTEGER NOT NULL,
    lon DOUBLE PRECISION,
    lat DOUBLE PRECISION,
    geom GEOMETRY(POINT, 4326),
    radius_km FLOAT DEFAULT 0,
    samples INTEGER DEFAULT 0,
    altitude_m FLOAT,
    azimuth INTEGER,
    tx_power_dbm INTEGER,
    band VARCHAR(10),
    source VARCHAR(50) DEFAULT 'opencellid',
    first_seen TIMESTAMPTZ,
    last_seen TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index spatiaux
CREATE INDEX IF NOT EXISTS idx_towers_geom ON cell_towers USING GIST(geom);
CREATE INDEX IF NOT EXISTS idx_towers_mcc_mnc ON cell_towers(mcc, mnc);
CREATE INDEX IF NOT EXISTS idx_towers_lac_cell ON cell_towers(mcc, mnc, lac, cell_id);
CREATE INDEX IF NOT EXISTS idx_towers_location ON cell_towers(lat, lon);

-- Mesures de signal (TimescaleDB hypertable)
CREATE TABLE IF NOT EXISTS signal_measurements (
    time TIMESTAMPTZ NOT NULL,
    phone_hash TEXT NOT NULL,
    mnc INTEGER,
    lac INTEGER,
    cell_id INTEGER,
    lat DOUBLE PRECISION,
    lon DOUBLE PRECISION,
    rssi INTEGER,
    rsrp INTEGER,
    rsrq INTEGER,
    ta INTEGER,
    rtt FLOAT,
    altitude DOUBLE PRECISION,
    speed_kmh FLOAT,
    accuracy_m FLOAT,
    source TEXT DEFAULT 'cell'
);

-- Convertir en hypertable TimescaleDB
SELECT create_hypertable('signal_measurements', 'time', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS idx_measurements_phone ON signal_measurements(phone_hash, time DESC);

-- Tracking sessions
CREATE TABLE IF NOT EXISTS tracking_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    phone_hash TEXT NOT NULL,
    mnc INTEGER,
    carrier TEXT,
    started_at TIMESTAMPTZ DEFAULT NOW(),
    ended_at TIMESTAMPTZ,
    is_active BOOLEAN DEFAULT TRUE,
    total_positions INTEGER DEFAULT 0
);

-- Historique positions
CREATE TABLE IF NOT EXISTS positions (
    id BIGSERIAL PRIMARY KEY,
    session_id UUID REFERENCES tracking_sessions(id),
    time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    lat DOUBLE PRECISION NOT NULL,
    lon DOUBLE PRECISION NOT NULL,
    geom GEOMETRY(POINT, 4326),
    accuracy_km FLOAT,
    confidence TEXT,
    method TEXT,
    towers_used INTEGER,
    city TEXT,
    altitude_m FLOAT,
    speed_kmh FLOAT
);

CREATE INDEX IF NOT EXISTS idx_positions_session ON positions(session_id, time);
CREATE INDEX IF NOT EXISTS idx_positions_geom ON positions USING GIST(geom);

-- Geofences
CREATE TABLE IF NOT EXISTS geofences (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    center_lat DOUBLE PRECISION NOT NULL,
    center_lon DOUBLE PRECISION NOT NULL,
    radius_km FLOAT NOT NULL,
    geom GEOMETRY(POLYGON, 4326),
    active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================
-- FONCTIONS UTILITAIRES
-- ============================================

-- Fonction pour trouver les antennes proches
CREATE OR REPLACE FUNCTION get_nearest_towers(
    search_lat DOUBLE PRECISION,
    search_lon DOUBLE PRECISION,
    radius_km DOUBLE PRECISION DEFAULT 10,
    max_results INTEGER DEFAULT 20
) RETURNS TABLE (
    radio VARCHAR, mcc INTEGER, mnc INTEGER, lac INTEGER, cell_id INTEGER,
    lat DOUBLE PRECISION, lon DOUBLE PRECISION, distance_km DOUBLE PRECISION,
    altitude_m DOUBLE PRECISION, samples INTEGER
) AS $$
BEGIN
    RETURN QUERY
    SELECT 
        t.radio, t.mcc, t.mnc, t.lac, t.cell_id,
        t.lat, t.lon,
        ST_DistanceSphere(t.geom, ST_SetSRID(ST_MakePoint(search_lon, search_lat), 4326)) / 1000.0 AS distance_km,
        t.altitude_m, t.samples
    FROM cell_towers t
    WHERE t.mcc = 220
      AND ST_DWithin(t.geom, ST_SetSRID(ST_MakePoint(search_lon, search_lat), 4326), radius_km * 1000)
    ORDER BY t.geom <-> ST_SetSRID(ST_MakePoint(search_lon, search_lat), 4326)
    LIMIT max_results;
END;
$$ LANGUAGE plpgsql;

-- Fonction derniere position connue
CREATE OR REPLACE FUNCTION get_last_position(
    phone_hash_input TEXT
) RETURNS TABLE (
    lat DOUBLE PRECISION, lon DOUBLE PRECISION, accuracy_km DOUBLE PRECISION,
    confidence TEXT, time TIMESTAMPTZ
) AS $$
BEGIN
    RETURN QUERY
    SELECT p.lat, p.lon, p.accuracy_km, p.confidence, p.time
    FROM positions p
    JOIN tracking_sessions s ON p.session_id = s.id
    WHERE s.phone_hash = phone_hash_input
    ORDER BY p.time DESC
    LIMIT 1;
END;
$$ LANGUAGE plpgsql;

-- ============================================
-- IMPORT OPENCELLID
-- =================================== =========
-- psql -d serbiatracker -c "\COPY cell_towers(radio,mcc,mnc,lac,cell_id,lon,lat,radius_km,samples,source) FROM 'serbia_towers.csv' CSV HEADER;"
-- UPDATE cell_towers SET geom = ST_SetSRID(ST_MakePoint(lon, lat), 4326);

-- ============================================
-- VUES
-- ============================================

-- Vue statistiques par operateur
CREATE OR REPLACE VIEW operator_stats AS
SELECT 
    mnc,
    COUNT(*) as tower_count,
    AVG(samples) as avg_samples,
    MIN(radius_km) as min_radius,
    MAX(radius_km) as max_radius
FROM cell_towers
WHERE mcc = 220
GROUP BY mnc;

-- Vue heatmap donnees
CREATE OR REPLACE VIEW coverage_heatmap AS
SELECT 
    ST_SnapToGrid(geom, 0.005) as grid_cell,
    COUNT(*) as tower_count,
    AVG(radius_km) as avg_radius,
    ST_Centroid(ST_Collect(geom)) as center
FROM cell_towers
WHERE mcc = 220
GROUP BY ST_SnapToGrid(geom, 0.005);
