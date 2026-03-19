-- Run this while connected to "postgres"

CREATE DATABASE app_db;

CREATE USER app_user WITH PASSWORD 'Super_user1';

GRANT ALL PRIVILEGES ON DATABASE app_db TO app_user;