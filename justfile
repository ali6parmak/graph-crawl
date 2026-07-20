db-up:
    docker compose up -d --wait

db-down:
    docker compose stop

db-reset:
    docker compose down -v
    rm -rf .pgdata
    docker compose up -d --wait

test-db: db-up
    pytest tests/test_db_integration.py -v
