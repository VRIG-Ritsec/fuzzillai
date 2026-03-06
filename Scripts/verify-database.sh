    #!/bin/bash
# Database initialization verification and fix script

echo "=== Checking PostgreSQL Connection ==="
docker exec fuzzilli-postgres-master pg_isready -U fuzzilli -d fuzzilli_master
if [ $? -ne 0 ]; then
    echo "ERROR: PostgreSQL is not ready"
    exit 1
fi

echo -e "\n=== Checking if tables exist ==="
TABLES=$(docker exec fuzzilli-postgres-master psql -U fuzzilli -d fuzzilli_master -t -c "\dt" | grep -c "main")

if [ "$TABLES" -eq 0 ]; then
    echo "⚠️  Database tables NOT found. Initializing schema..."
    docker exec -i fuzzilli-postgres-master psql -U fuzzilli -d fuzzilli_master < /home/aleksi/fuzzillai/postgres-init.sql
    
    if [ $? -eq 0 ]; then
        echo "✅ Schema initialized successfully!"
    else
        echo "❌ Failed to initialize schema"
        exit 1
    fi
else
    echo "✅ Database tables already exist"
fi

echo -e "\n=== Verifying schema ==="
docker exec fuzzilli-postgres-master psql -U fuzzilli -d fuzzilli_master -c "\dt"

echo -e "\n=== Checking 'main' table ==="
docker exec fuzzilli-postgres-master psql -U fuzzilli -d fuzzilli_master -c "SELECT COUNT(*) as fuzzer_count FROM main;"

echo -e "\n=== Database ready! ==="
