# get the cloud sql proxy

brew install postgresql

curl -o cloud-sql-proxy https://storage.googleapis.com/cloud-sql-connectors/cloud-sql-proxy/v2.11.0/cloud-sql-proxy.darwin.amd64
chmod +x cloud-sql-proxy

# In a new terminal - create the db
psql -h 127.0.0.1 -p 5432 -U postgres -d postgres
-> provide password used during cloud sql instance creation

# Run the migration scripts
psql -h 127.0.0.1 -U postgres -d postgres -f src/db/migrations/001_init_db.sql
psql -h 127.0.0.1 -U app_user -d app_db -f src/db/migrations/002_init_schema.sql