#!/usr/bin/env python3
"""
Redis Stream Producer for Fuzzilli Integration

This script demonstrates how to send data to Redis streams that will be consumed
by the sync.py service and stored in PostgreSQL.

Usage:
    python3 redis_producer.py --help
    python3 redis_producer.py --create-fuzzer
    python3 redis_producer.py --test-program --program "base64_encoded_program" --fuzzer-id 1
    python3 redis_producer.py --execution --program "base64_encoded_program" --fuzzer-id 1 --type "generalistic_testcases"
"""

import asyncio
import argparse
import base64
import json
import os
from redis.asyncio import Redis

# Configuration
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
STREAM_NAME = "stream:fuzz:updates"

class FuzzilliRedisProducer:
    def __init__(self, redis_url: str):
        self.redis_url = redis_url
        self.redis = None
    
    async def connect(self):
        """Connect to Redis"""
        self.redis = Redis.from_url(self.redis_url)
        await self.redis.ping()
        print(f"Connected to Redis at {self.redis_url}")
    
    async def disconnect(self):
        """Disconnect from Redis"""
        if self.redis:
            await self.redis.close()
    
    async def create_fuzzer(self) -> int:
        """Create a new fuzzer instance and return its ID"""
        data = {
            "op": "create_fuzzer"
        }
        msg_id = await self.redis.xadd(STREAM_NAME, data)
        print(f"Sent create_fuzzer message: {msg_id}")
        return msg_id
    
    async def send_fuzzer_program(self, program_base64: str, fuzzer_id: int):
        """Send a fuzzer program to the stream"""
        data = {
            "op": "fuzzer_program",
            "program_base64": program_base64,
            "fuzzer_id": str(fuzzer_id)
        }
        msg_id = await self.redis.xadd(STREAM_NAME, data)
        print(f"Sent fuzzer_program message: {msg_id}")
        return msg_id
    
    async def send_test_program(self, program_base64: str, fuzzer_id: int):
        """Send a test program to the stream"""
        data = {
            "op": "test_program",
            "program_base64": program_base64,
            "fuzzer_id": str(fuzzer_id)
        }
        msg_id = await self.redis.xadd(STREAM_NAME, data)
        print(f"Sent test_program message: {msg_id}")
        return msg_id
    
    async def send_execution(self, 
                           program_base64: str, 
                           fuzzer_id: int, 
                           execution_type: str = "generalistic_testcases",
                           feedback_vector: dict = None,
                           turboshaft_ir: str = "",
                           coverage_total: float = 0.0,
                           execution_flags: list = None):
        """Send an execution record to the stream"""
        data = {
            "op": "execution",
            "program_base64": program_base64,
            "fuzzer_id": str(fuzzer_id),
            "execution_type": execution_type,
            "turboshaft_ir": turboshaft_ir,
            "coverage_total": str(coverage_total),
            "execution_flags": json.dumps(execution_flags or [])
        }
        
        if feedback_vector:
            data["feedback_vector"] = json.dumps(feedback_vector)
        else:
            data["feedback_vector"] = "null"
        
        msg_id = await self.redis.xadd(STREAM_NAME, data)
        print(f"Sent execution message: {msg_id}")
        return msg_id
    
    async def delete_program(self, program_base64: str):
        """Delete a program from the database"""
        data = {
            "op": "del",
            "program_base64": program_base64
        }
        msg_id = await self.redis.xadd(STREAM_NAME, data)
        print(f"Sent delete message: {msg_id}")
        return msg_id

def encode_program(program_text: str) -> str:
    """Encode a program text to base64"""
    return base64.b64encode(program_text.encode('utf-8')).decode('utf-8')

def decode_program(program_base64: str) -> str:
    """Decode a base64 program to text"""
    return base64.b64decode(program_base64.encode('utf-8')).decode('utf-8')

async def main():
    parser = argparse.ArgumentParser(description="Redis Stream Producer for Fuzzilli")
    parser.add_argument("--redis-url", default=REDIS_URL, help="Redis URL")
    
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    
    # Create fuzzer command
    subparsers.add_parser("create-fuzzer", help="Create a new fuzzer instance")
    
    # Fuzzer program command
    fuzzer_prog_parser = subparsers.add_parser("fuzzer-program", help="Send a fuzzer program")
    fuzzer_prog_parser.add_argument("--program", required=True, help="Program text or base64")
    fuzzer_prog_parser.add_argument("--fuzzer-id", type=int, required=True, help="Fuzzer ID")
    fuzzer_prog_parser.add_argument("--base64", action="store_true", help="Program is already base64 encoded")
    
    # Test program command
    test_prog_parser = subparsers.add_parser("test-program", help="Send a test program")
    test_prog_parser.add_argument("--program", required=True, help="Program text or base64")
    test_prog_parser.add_argument("--fuzzer-id", type=int, required=True, help="Fuzzer ID")
    test_prog_parser.add_argument("--base64", action="store_true", help="Program is already base64 encoded")
    
    # Execution command
    exec_parser = subparsers.add_parser("execution", help="Send an execution record")
    exec_parser.add_argument("--program", required=True, help="Program text or base64")
    exec_parser.add_argument("--fuzzer-id", type=int, required=True, help="Fuzzer ID")
    exec_parser.add_argument("--type", default="generalistic_testcases", 
                           choices=["agentic_analysis", "delta_analysis", "directed_testcases", "generalistic_testcases"],
                           help="Execution type")
    exec_parser.add_argument("--base64", action="store_true", help="Program is already base64 encoded")
    exec_parser.add_argument("--feedback", help="Feedback vector as JSON string")
    exec_parser.add_argument("--turboshaft", default="", help="Turboshaft IR")
    exec_parser.add_argument("--coverage", type=float, default=0.0, help="Coverage percentage")
    exec_parser.add_argument("--flags", help="Execution flags as JSON array string")
    
    # Delete command
    del_parser = subparsers.add_parser("delete", help="Delete a program")
    del_parser.add_argument("--program", required=True, help="Program base64")
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return
    
    producer = FuzzilliRedisProducer(args.redis_url)
    
    try:
        await producer.connect()
        
        if args.command == "create-fuzzer":
            await producer.create_fuzzer()
            
        elif args.command == "fuzzer-program":
            program_base64 = args.program if args.base64 else encode_program(args.program)
            await producer.send_fuzzer_program(program_base64, args.fuzzer_id)
            
        elif args.command == "test-program":
            program_base64 = args.program if args.base64 else encode_program(args.program)
            await producer.send_test_program(program_base64, args.fuzzer_id)
            
        elif args.command == "execution":
            program_base64 = args.program if args.base64 else encode_program(args.program)
            
            feedback_vector = None
            if args.feedback:
                try:
                    feedback_vector = json.loads(args.feedback)
                except json.JSONDecodeError:
                    print("Warning: Invalid JSON in feedback, using None")
            
            execution_flags = None
            if args.flags:
                try:
                    execution_flags = json.loads(args.flags)
                except json.JSONDecodeError:
                    print("Warning: Invalid JSON in flags, using empty list")
                    execution_flags = []
            
            await producer.send_execution(
                program_base64=program_base64,
                fuzzer_id=args.fuzzer_id,
                execution_type=args.type,
                feedback_vector=feedback_vector,
                turboshaft_ir=args.turboshaft,
                coverage_total=args.coverage,
                execution_flags=execution_flags
            )
            
        elif args.command == "delete":
            await producer.delete_program(args.program)
            
    finally:
        await producer.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
