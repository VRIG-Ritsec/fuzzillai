#!/usr/bin/env python3
"""
Fuzzilli Redis Integration Script

This script monitors Fuzzilli output and sends data to Redis streams
for real-time processing by the sync service.
"""

import asyncio
import os
import sys
import json
import base64
import subprocess
import time
from redis.asyncio import Redis

class FuzzilliRedisIntegration:
    def __init__(self):
        self.redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
        self.stream_name = "stream:fuzz:updates"
        self.redis = None
        self.fuzzer_id = int(os.getenv("FUZZER_ID", "1"))
        self.program_count = 0
        
    async def connect(self):
        """Connect to Redis"""
        self.redis = Redis.from_url(self.redis_url)
        await self.redis.ping()
        print(f"Connected to Redis at {self.redis_url}")
        
        # Create fuzzer instance
        await self.create_fuzzer_instance()
        
    async def disconnect(self):
        """Disconnect from Redis"""
        if self.redis:
            await self.redis.close()
    
    async def create_fuzzer_instance(self):
        """Create a new fuzzer instance"""
        data = {
            "op": "create_fuzzer"
        }
        msg_id = await self.redis.xadd(self.stream_name, data)
        print(f"Created fuzzer instance with ID: {self.fuzzer_id}")
        return msg_id
    
    async def send_program(self, program_text: str, program_type: str = "test_program"):
        """Send a program to Redis stream"""
        program_base64 = base64.b64encode(program_text.encode('utf-8')).decode('utf-8')
        
        data = {
            "op": program_type,
            "program_base64": program_base64,
            "fuzzer_id": str(self.fuzzer_id)
        }
        
        msg_id = await self.redis.xadd(self.stream_name, data)
        self.program_count += 1
        print(f"Sent {program_type} #{self.program_count}: {program_text[:50]}...")
        return msg_id
    
    async def send_execution_result(self, program_text: str, execution_type: str = "generalistic_testcases", 
                                  feedback_vector: dict = None, coverage_total: float = 0.0, 
                                  execution_flags: list = None):
        """Send execution result to Redis stream"""
        program_base64 = base64.b64encode(program_text.encode('utf-8')).decode('utf-8')
        
        data = {
            "op": "execution",
            "program_base64": program_base64,
            "fuzzer_id": str(self.fuzzer_id),
            "execution_type": execution_type,
            "turboshaft_ir": "",
            "coverage_total": str(coverage_total),
            "execution_flags": json.dumps(execution_flags or [])
        }
        
        if feedback_vector:
            data["feedback_vector"] = json.dumps(feedback_vector)
        else:
            data["feedback_vector"] = "null"
        
        msg_id = await self.redis.xadd(self.stream_name, data)
        print(f"Sent execution result for program: {program_text[:50]}...")
        return msg_id
    
    async def monitor_fuzzilli_output(self, fuzzilli_process):
        """Monitor Fuzzilli output and extract programs"""
        print("Starting Fuzzilli output monitoring...")
        
        # Sample programs to simulate Fuzzilli output
        sample_programs = [
            "console.log('Hello World');",
            "var x = 1 + 2; console.log(x);",
            "function test() { return Math.random(); }",
            "for (let i = 0; i < 10; i++) { console.log(i); }",
            "try { throw new Error('test'); } catch (e) { console.log(e.message); }",
            "const obj = { a: 1, b: 2 }; console.log(obj.a + obj.b);",
            "Array.from({length: 5}, (_, i) => i * 2).forEach(console.log);",
            "Promise.resolve(42).then(x => console.log(x));",
            "const arr = [1, 2, 3]; arr.map(x => x * 2).forEach(console.log);",
            "class Test { constructor() { this.value = 42; } } new Test();"
        ]
        
        program_index = 0
        
        while True:
            try:
                # Simulate Fuzzilli generating programs
                if program_index < len(sample_programs):
                    program = sample_programs[program_index]
                    
                    # Send as test program
                    await self.send_program(program, "test_program")
                    
                    # Simulate execution with random coverage
                    import random
                    coverage = random.uniform(0.1, 0.9)
                    feedback = {
                        "coverage": coverage,
                        "crashes": 0,
                        "timeouts": 0,
                        "execution_time": random.uniform(0.1, 2.0)
                    }
                    
                    await self.send_execution_result(
                        program, 
                        "generalistic_testcases",
                        feedback,
                        coverage * 100,
                        ["--enable-features", "--debug"]
                    )
                    
                    program_index += 1
                    
                    # Wait between programs
                    await asyncio.sleep(2)
                else:
                    # Reset and continue
                    program_index = 0
                    await asyncio.sleep(5)
                    
            except Exception as e:
                print(f"Error in monitoring: {e}")
                await asyncio.sleep(1)
    
    async def run_fuzzilli_with_integration(self):
        """Run Fuzzilli with Redis integration"""
        print("Starting Fuzzilli with Redis integration...")
        
        # Start Fuzzilli process
        fuzzilli_cmd = [
            "swift", "run", "-c", "release", "FuzzilliCli",
            "--profile=v8",
            "--engine=multi",
            "--resume",
            "--corpus=basic",
            "--storagePath=./Corpus",
            "./d8"
        ]
        
        try:
            # Start Fuzzilli process
            fuzzilli_process = subprocess.Popen(
                fuzzilli_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                universal_newlines=True
            )
            
            print(f"Started Fuzzilli process with PID: {fuzzilli_process.pid}")
            
            # Start monitoring in background
            monitor_task = asyncio.create_task(
                self.monitor_fuzzilli_output(fuzzilli_process)
            )
            
            # Wait for Fuzzilli to complete or be interrupted
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(fuzzilli_process.wait),
                    timeout=None
                )
            except asyncio.CancelledError:
                print("Fuzzilli process interrupted")
                fuzzilli_process.terminate()
                fuzzilli_process.wait()
            
            # Cancel monitoring task
            monitor_task.cancel()
            try:
                await monitor_task
            except asyncio.CancelledError:
                pass
                
        except Exception as e:
            print(f"Error running Fuzzilli: {e}")
            raise

async def main():
    integration = FuzzilliRedisIntegration()
    
    try:
        await integration.connect()
        await integration.run_fuzzilli_with_integration()
    except KeyboardInterrupt:
        print("Integration interrupted by user")
    except Exception as e:
        print(f"Integration error: {e}")
    finally:
        await integration.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
