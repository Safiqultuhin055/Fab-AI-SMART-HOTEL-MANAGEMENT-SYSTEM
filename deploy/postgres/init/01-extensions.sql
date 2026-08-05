-- Runs once, on first initialisation of an empty data volume.
--
-- Django migrations also create `vector` (apps/core/0001), but doing it here
-- too means the extension exists even before the first migrate, and it is
-- created by the superuser rather than requiring the app role to hold CREATE.

CREATE EXTENSION IF NOT EXISTS vector;

-- Trigram index support for hybrid retrieval: pure vector search misses exact
-- keyword hits like a room number or a WiFi SSID (SRS §9.1, hybrid retrieval).
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Accent/case-insensitive guest name matching for walk-in lookup.
CREATE EXTENSION IF NOT EXISTS unaccent;

-- Required by the reservation exclusion constraint: lets a GiST index mix
-- UUID equality with daterange overlap in one constraint.
CREATE EXTENSION IF NOT EXISTS btree_gist;
