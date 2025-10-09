import asyncio, os, time
from redis.asyncio import Redis
import asyncpg
from typing import List, Dict, Any

GROUP = os.getenv("GROUP", "g_fuzz")
CONSUMER = os.getenv("CONSUMER", "c_sync_1")
STREAMS = os.getenv("STREAMS", "redis1=redis://redis1:6379,redis2=redis://redis2:6379").split(",")
STREAM_NAME = "stream:fuzz:updates"
PG_DSN = os.getenv("PG_DSN", "postgres://fuzzuser:pass@pg:5432/main")
DB_WORKER_THREADS = int(os.getenv("DB_WORKER_THREADS", "4"))
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "400"))
BATCH_TIMEOUT = float(os.getenv("BATCH_TIMEOUT", "0.1"))

UPSERT_PROGRAM_SQL = """
INSERT INTO program (program_base64, fuzzer_id, created_at)
VALUES ($1, $2, NOW())
ON CONFLICT (program_base64) DO UPDATE SET
  created_at = NOW();
"""

UPSERT_EXECUTION_SQL = """
INSERT INTO execution (program_base64, execution_type_id, feedback_vector, turboshaft_ir, coverage_total, execution_flags, created_at)
VALUES ($1, $2, $3, $4, $5, $6, NOW())
ON CONFLICT (program_base64, execution_type_id) DO UPDATE SET
  feedback_vector = EXCLUDED.feedback_vector,
  turboshaft_ir = EXCLUDED.turboshaft_ir,
  coverage_total = EXCLUDED.coverage_total,
  execution_flags = EXCLUDED.execution_flags,
  created_at = NOW();
"""

UPDATE_FEEDBACK_SQL = """
UPDATE execution SET feedback_vector = $2
WHERE program_base64 = $1;
"""

DELETE_SQL = """
DELETE FROM program WHERE program_base64 = $1;
"""

class DatabaseWorker:
    def __init__(self, worker_id: int, dsn: str):
        self.worker_id = worker_id
        self.dsn = dsn
        self.connection_pool = None
        
    async def initialize(self):
        self.connection_pool = await asyncpg.create_pool(
            self.dsn,
            min_size=2,
            max_size=10,
            command_timeout=30
        )
    
    async def process_batch(self, batch: List[Dict[str, Any]]):
        if not batch:
            return
            
        async with self.connection_pool.acquire() as conn:
            program_upserts = []
            execution_upserts = []
            updates = []
            deletes = []
            
            for operation in batch:
                op_type = operation.get('op')
                if op_type == 'del':
                    deletes.append(operation)
                elif op_type == 'update_feedback':
                    updates.append(operation)
                else:
                    if operation.get('execution_type_id'):
                        execution_upserts.append(operation)
                    else:
                        program_upserts.append(operation)
            
            if program_upserts:
                await self._batch_upsert_program(conn, program_upserts)
            if execution_upserts:
                await self._batch_upsert_execution(conn, execution_upserts)
            if updates:
                await self._batch_update_feedback(conn, updates)
            if deletes:
                await self._batch_delete(conn, deletes)
    
    async def _batch_upsert_program(self, conn, operations: List[Dict[str, Any]]):
        if not operations:
            return
            
        values = []
        for op in operations:
            program_base64 = op.get('program_base64', '')
            fuzzer_id = int(op.get('fuzzer_id', 0) or 0)
            values.append((program_base64, fuzzer_id))
        
        await conn.executemany(UPSERT_PROGRAM_SQL, values)
    
    async def _batch_upsert_execution(self, conn, operations: List[Dict[str, Any]]):
        if not operations:
            return
            
        values = []
        for op in operations:
            program_base64 = op.get('program_base64', '')
            execution_type_id = int(op.get('execution_type_id', 1) or 1)
            feedback_vector = op.get('feedback_vector', 'null')
            turboshaft_ir = op.get('turboshaft_ir', '')
            coverage_total = float(op.get('coverage_total', 0) or 0)
            execution_flags = op.get('execution_flags', [])
            
            values.append((program_base64, execution_type_id, feedback_vector, turboshaft_ir, coverage_total, execution_flags))
        
        await conn.executemany(UPSERT_EXECUTION_SQL, values)
    
    async def _batch_update_feedback(self, conn, operations: List[Dict[str, Any]]):
        if not operations:
            return
            
        values = []
        for op in operations:
            program_base64 = op.get('program_base64', '')
            feedback_vector = op.get('feedback_vector', 'null')
            values.append((program_base64, feedback_vector))
        
        await conn.executemany(UPDATE_FEEDBACK_SQL, values)
    
    async def _batch_delete(self, conn, operations: List[Dict[str, Any]]):
        if not operations:
            return
            
        values = [(op.get('program_base64', ''),) for op in operations]
        await conn.executemany(DELETE_SQL, values)
    
    async def close(self):
        if self.connection_pool:
            await self.connection_pool.close()

class DatabaseBatchProcessor:
    def __init__(self, dsn: str, num_workers: int = DB_WORKER_THREADS):
        self.dsn = dsn
        self.num_workers = num_workers
        self.workers: List[DatabaseWorker] = []
        self.operation_queue = asyncio.Queue()
        self.running = False
        
    async def initialize(self):
        self.workers = []
        for i in range(self.num_workers):
            worker = DatabaseWorker(i, self.dsn)
            await worker.initialize()
            self.workers.append(worker)
        
        self.running = True
    
    def add_operation(self, operation: Dict[str, Any]):
        self.operation_queue.put_nowait(operation)
    
    async def process_operations(self):
        batch = []
        last_batch_time = time.time()
        
        while self.running:
            try:
                try:
                    operation = await asyncio.wait_for(
                        self.operation_queue.get(), 
                        timeout=0.1
                    )
                    batch.append(operation)
                except asyncio.TimeoutError:
                    pass
                
                current_time = time.time()
                should_process_batch = (
                    len(batch) >= BATCH_SIZE or 
                    (batch and current_time - last_batch_time >= BATCH_TIMEOUT)
                )
                
                if should_process_batch and batch:
                    worker_index = hash(batch[0].get('program_base64', '')) % len(self.workers)
                    worker = self.workers[worker_index]
                    
                    try:
                        await worker.process_batch(batch.copy())
                    except Exception as e:
                        pass
                    
                    batch.clear()
                    last_batch_time = current_time
                
                await asyncio.sleep(0.01)
                
            except Exception as e:
                await asyncio.sleep(0.1)
    
    async def close(self):
        self.running = False
        
        remaining_ops = []
        while not self.operation_queue.empty():
            try:
                remaining_ops.append(self.operation_queue.get_nowait())
            except:
                break
        
        if remaining_ops:
            for worker in self.workers:
                try:
                    await worker.process_batch(remaining_ops)
                    break
                except:
                    pass
        
        for worker in self.workers:
            await worker.close()

async def ensure_group(r: Redis, stream: str):
    try:
        await r.xgroup_create(stream, GROUP, id="$", mkstream=True)
    except Exception as e:
        if "BUSYGROUP" not in str(e):
            raise

async def consume_stream(label: str, redis_url: str, batch_processor: DatabaseBatchProcessor):
    r = Redis.from_url(redis_url)
    await ensure_group(r, STREAM_NAME)
    
    while True:
        try:
            resp = await r.xreadgroup(GROUP, CONSUMER, {STREAM_NAME: ">"}, count=100, block=5000)
            if not resp:
                continue
                
            for _, entries in resp:
                for msg_id, data in entries:
                    op = data.get(b'op', b'').decode()
                    program_base64 = data.get(b'program_base64', b'').decode()
                    
                    operation = {
                        'op': op,
                        'program_base64': program_base64,
                        'msg_id': msg_id
                    }
                    
                    if op == "del":
                        pass
                    elif op == "update_feedback":
                        operation['feedback_vector'] = data.get(b'feedback_vector', b'null').decode()
                    else:
                        operation['fuzzer_id'] = data.get(b'fuzzer_id', b'0').decode()
                        operation['execution_type_id'] = data.get(b'execution_type_id', b'1').decode()
                        operation['feedback_vector'] = data.get(b'feedback_vector', b'null').decode()
                        operation['turboshaft_ir'] = data.get(b'turboshaft_ir', b'').decode()
                        operation['coverage_total'] = data.get(b'coverage_total', b'0').decode()
                        
                        execution_flags_str = data.get(b'execution_flags', b'').decode()
                        if execution_flags_str:
                            operation['execution_flags'] = execution_flags_str.split(',')
                        else:
                            operation['execution_flags'] = [
                                'is_debug=false',
                                'v8_enable_i18n_support=false', 
                                'dcheck_always_on=true',
                                'v8_static_library=true',
                                'v8_enable_verify_heap=true',
                                'v8_fuzzilli=true',
                                'sanitizer_coverage_flags=trace-pc-guard',
                                'target_cpu=x64'
                            ]
                    
                    batch_processor.add_operation(operation)
                    await r.xack(STREAM_NAME, GROUP, msg_id)
                    
        except Exception as e:
            await asyncio.sleep(1)

async def main():
    batch_processor = DatabaseBatchProcessor(PG_DSN, DB_WORKER_THREADS)
    await batch_processor.initialize()
    
    batch_task = asyncio.create_task(batch_processor.process_operations())
    
    stream_tasks = []
    for pair in STREAMS:
        label, url = pair.split("=")
        task = asyncio.create_task(consume_stream(label, url, batch_processor))
        stream_tasks.append(task)
    
    try:
        await asyncio.gather(batch_task, *stream_tasks)
    except KeyboardInterrupt:
        pass
    finally:
        await batch_processor.close()

if __name__ == "__main__":
    asyncio.run(main())
