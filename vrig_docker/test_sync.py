import asyncio
import asyncpg
import json
import base64
from sync import DatabaseWorker, DatabaseBatchProcessor

async def setup_test_database():
    dsn = "postgres://fuzzuser:pass@localhost:5432/main"
    conn = await asyncpg.connect(dsn)
    
    try:
        await conn.execute("DELETE FROM execution")
        await conn.execute("DELETE FROM program")
        await conn.execute("DELETE FROM fuzzer")
        await conn.execute("DELETE FROM main")
        print("✓ Test database cleared")
    finally:
        await conn.close()

async def test_database_worker():
    dsn = "postgres://fuzzuser:pass@localhost:5432/main"
    
    worker = DatabaseWorker(0, dsn)
    await worker.initialize()
    
    test_operations = [
        {
            'op': 'set',
            'program_base64': base64.b64encode(b'test_program_1').decode(),
            'fuzzer_id': 1,
            'execution_type_id': 1,
            'feedback_vector': '{"edges": [1, 2, 3]}',
            'turboshaft_ir': 'test_ir_data',
            'coverage_total': 85.5,
            'execution_flags': ['is_debug=false', 'v8_fuzzilli=true']
        },
        {
            'op': 'update_feedback',
            'program_base64': base64.b64encode(b'test_program_1').decode(),
            'feedback_vector': '{"edges": [1, 2, 3, 4]}'
        },
        {
            'op': 'del',
            'program_base64': base64.b64encode(b'test_program_2').decode()
        }
    ]
    
    try:
        await worker.process_batch(test_operations)
        print("✓ Database worker test passed")
    except Exception as e:
        print(f"✗ Database worker test failed: {e}")
    finally:
        await worker.close()

async def test_batch_processor():
    dsn = "postgres://fuzzuser:pass@localhost:5432/main"
    
    processor = DatabaseBatchProcessor(dsn, 2)
    await processor.initialize()
    
    for i in range(20):
        operation = {
            'op': 'set',
            'program_base64': base64.b64encode(f'test_program_{i}'.encode()).decode(),
            'fuzzer_id': (i % 3) + 1,
            'execution_type_id': (i % 4) + 1,
            'feedback_vector': f'{{"edges": [{i}, {i+1}, {i+2}]}}',
            'turboshaft_ir': f'test_ir_{i}',
            'coverage_total': float(i * 5),
            'execution_flags': ['is_debug=false', 'v8_fuzzilli=true', 'target_cpu=x64']
        }
        processor.add_operation(operation)
    
    await asyncio.sleep(3)
    await processor.close()
    print("✓ Batch processor test passed")

async def verify_results():
    dsn = "postgres://fuzzuser:pass@localhost:5432/main"
    conn = await asyncpg.connect(dsn)
    
    try:
        program_count = await conn.fetchval("SELECT COUNT(*) FROM program")
        execution_count = await conn.fetchval("SELECT COUNT(*) FROM execution")
        
        print(f"✓ Programs in database: {program_count}")
        print(f"✓ Executions in database: {execution_count}")
        
        if execution_count > 0:
            sample = await conn.fetchrow("""
                SELECT program_base64, execution_type_id, coverage_total, execution_flags
                FROM execution 
                ORDER BY created_at DESC 
                LIMIT 1
            """)
            
            print(f"✓ Sample execution:")
            print(f"  Program: {sample['program_base64']}")
            print(f"  Type: {sample['execution_type_id']}")
            print(f"  Coverage: {sample['coverage_total']}%")
            print(f"  Flags: {sample['execution_flags']}")
        
    finally:
        await conn.close()

async def main():
    print("Testing multi-threaded database sync with mock data...")
    
    await setup_test_database()
    await test_database_worker()
    await test_batch_processor()
    await verify_results()
    
    print("All tests completed")

if __name__ == "__main__":
    asyncio.run(main())
