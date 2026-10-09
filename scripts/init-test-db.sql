-- Runs once when the Postgres container is first created: a separate
-- database for pytest so tests never touch your dev data.
CREATE DATABASE aegis_test OWNER aegis;
