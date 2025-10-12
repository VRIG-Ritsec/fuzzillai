import asyncio, os, time, json
from redis.asyncio import Redis
import asyncpg

GROUP = os.getenv("GROUP", "g_fuzz")
CONSUMER = os.getenv("CONSUMER", "c_sync_1")
STREAMS = os.getenv("STREAMS", "redis1=redis://redis1:6379,redis2=redis://redis2:6379").split(",")
STREAM_NAME = "stream:fuzz:updates"
PG_DSN = os.getenv("PG_DSN", "postgres://fuzzuser:pass@postgres:5432/main")

CREATE_GROUP_OK = {"OK", "BUSYGROUP Consumer Group name already exists"}

# SQL for creating fuzzer instance
CREATE_FUZZER_SQL = """
SELECT get_or_create_fuzzer();
"""

# SQL for inserting fuzzer program
INSERT_FUZZER_PROGRAM_SQL = """
INSERT INTO fuzzer (program_base64, fuzzer_id, inserted_at)
VALUES ($1, $2, NOW())
ON CONFLICT (program_base64) DO NOTHING;
"""

# SQL for inserting test program
INSERT_PROGRAM_SQL = """
INSERT INTO program (program_base64, fuzzer_id, created_at)
VALUES ($1, $2, NOW())
ON CONFLICT (program_base64) DO NOTHING;
"""

# SQL for inserting execution record using the safe function
INSERT_EXECUTION_SQL = """
SELECT insert_execution_safe($1, $2, $3, $4, $5, $6, $7);
"""

# SQL for getting execution type ID
GET_EXECUTION_TYPE_SQL = """
SELECT id FROM execution_type WHERE title = $1;
"""

async def ensure_group(r: Redis, stream: str):
    try:
        await r.xgroup_create(stream, GROUP, id="$", mkstream=True)
    except Exception as e:
        if "BUSYGROUP" not in str(e):
            raise

async def consume_stream(label: str, redis_url: str, pg):
    r = Redis.from_url(redis_url)
    await ensure_group(r, STREAM_NAME)
    while True:
        try:
            # Read new messages for this consumer
            resp = await r.xreadgroup(GROUP, CONSUMER, {STREAM_NAME: ">"}, count=100, block=5000)
            if not resp:
                continue
            # resp = [(b'stream:fuzz:updates', [(id, {b'k':b'v', ...}), ...])]
            for _, entries in resp:
                for msg_id, data in entries:
                    op = data.get(b'op', b'').decode()
                    
                    if op == "create_fuzzer":
                        # Create new fuzzer instance
                        fuzzer_id = await pg.fetchval(CREATE_FUZZER_SQL)
                        print(f"Created fuzzer instance with ID: {fuzzer_id}")
                        
                    elif op == "fuzzer_program":
                        # Insert fuzzer program
                        program_base64 = data.get(b'program_base64', b'').decode()
                        fuzzer_id = int(data.get(b'fuzzer_id', b'0').decode() or 0)
                        await pg.execute(INSERT_FUZZER_PROGRAM_SQL, program_base64, fuzzer_id)
                        print(f"Inserted fuzzer program for fuzzer {fuzzer_id}")
                        
                    elif op == "test_program":
                        # Insert test program
                        program_base64 = data.get(b'program_base64', b'').decode()
                        fuzzer_id = int(data.get(b'fuzzer_id', b'0').decode() or 0)
                        await pg.execute(INSERT_PROGRAM_SQL, program_base64, fuzzer_id)
                        print(f"Inserted test program for fuzzer {fuzzer_id}")
                        
                    elif op == "execution":
                        # Insert execution record using the safe function
                        program_base64 = data.get(b'program_base64', b'').decode()
                        fuzzer_id = int(data.get(b'fuzzer_id', b'0').decode() or 0)
                        execution_type = data.get(b'execution_type', b'generalistic_testcases').decode()
                        
                        # Parse feedback vector as JSON
                        feedback_vector_str = data.get(b'feedback_vector', b'null').decode()
                        try:
                            feedback_vector = json.loads(feedback_vector_str) if feedback_vector_str != 'null' else None
                        except json.JSONDecodeError:
                            print(f"Warning: Invalid JSON in feedback_vector, using null")
                            feedback_vector = None
                        
                        turboshaft_ir = data.get(b'turboshaft_ir', b'').decode()
                        coverage_total = float(data.get(b'coverage_total', b'0').decode() or 0)
                        
                        # Parse execution flags as JSON array
                        execution_flags_str = data.get(b'execution_flags', b'[]').decode()
                        try:
                            execution_flags = json.loads(execution_flags_str) if execution_flags_str else []
                        except json.JSONDecodeError:
                            print(f"Warning: Invalid JSON in execution_flags, using empty array")
                            execution_flags = []
                        
                        # Use the safe function to insert execution
                        execution_id = await pg.fetchval(
                            INSERT_EXECUTION_SQL, 
                            program_base64, 
                            fuzzer_id,
                            execution_type,
                            feedback_vector, 
                            turboshaft_ir, 
                            coverage_total,
                            execution_flags
                        )
                        print(f"Inserted execution {execution_id} for program {program_base64[:20]}...")
                        
                    elif op == "del":
                        # Delete program entry
                        program_base64 = data.get(b'program_base64', b'').decode()
                        await pg.execute("DELETE FROM program WHERE program_base64=$1", program_base64)
                        print(f"Deleted program {program_base64[:20]}...")
                        
                    await r.xack(STREAM_NAME, GROUP, msg_id)
        except Exception as e:
            print(f"Error processing stream {label}: {e}")
            # backoff on errors
            await asyncio.sleep(1)

async def main():
    print(f"Connecting to PostgreSQL: {PG_DSN}")
    pg = await asyncpg.connect(PG_DSN)
    print("Connected to PostgreSQL successfully")
    
    print(f"Processing streams: {STREAMS}")
    tasks = []
    for pair in STREAMS:
        label, url = pair.split("=")
        print(f"Starting consumer for {label} at {url}")
        tasks.append(asyncio.create_task(consume_stream(label, url, pg)))
    
    print("Starting stream consumers...")
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    print("=== Starting sync service ===")
    print("Environment variables:")
    print(f"  PG_DSN: {os.getenv('PG_DSN')}")
    print(f"  STREAMS: {os.getenv('STREAMS')}")
    print(f"  GROUP: {os.getenv('GROUP')}")
    print(f"  CONSUMER: {os.getenv('CONSUMER')}")
    print("=== Starting main function ===")
    asyncio.run(main())
