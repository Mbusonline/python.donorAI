
# import redis

# r = redis.from_url("redis://127.0.0.1:6379/0")

# print(r.ping())





import psycopg2
# "username": "tally_user",
# "password": "Admin!Tally123",
# "database": "db_tally",
# "host": "localhost",

DB_URL = "postgresql://tally_user:Admin!Tally123@localhost:5432/db_tally"

def check_db():
    try:
        conn = psycopg2.connect(DB_URL)
        conn.close()
        print("✅ PostgreSQL connected successfully!")
    except Exception as e:
        print("❌ Connection failed:", e)

check_db()