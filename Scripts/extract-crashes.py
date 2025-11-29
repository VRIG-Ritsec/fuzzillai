#!/usr/bin/env python3
"""
Fuzzilli Crash Extraction Script
Extracts crash information from PostgreSQL database with advanced filtering and analysis.

Usage:
    python3 Scripts/extract-crashes.py [options]

Environment Variables:
    POSTGRES_HOST - PostgreSQL host (default: localhost)
    POSTGRES_PORT - PostgreSQL port (default: 5432)
    POSTGRES_DB - Database name (default: fuzzilli_master)
    POSTGRES_USER - Database user (default: fuzzilli)
    POSTGRES_PASSWORD - Database password (default: fuzzilli123)
"""

import os
import sys
import json
import csv
import argparse
import base64
from datetime import datetime
from typing import List, Dict, Optional, Any
from pathlib import Path

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    print("Error: psycopg2 is not installed.")
    print("Install it with: pip3 install psycopg2-binary")
    sys.exit(1)


class CrashExtractor:
    """Extract crashes from Fuzzilli PostgreSQL database."""
    
    def __init__(self, host: str, port: int, database: str, user: str, password: str):
        """Initialize database connection."""
        self.conn_params = {
            'host': host,
            'port': port,
            'database': database,
            'user': user,
            'password': password
        }
        self.conn = None
        
    def connect(self) -> bool:
        """Connect to the database."""
        try:
            self.conn = psycopg2.connect(**self.conn_params)
            return True
        except psycopg2.Error as e:
            print(f"\033[91mError connecting to database: {e}\033[0m")
            return False
    
    def disconnect(self):
        """Close database connection."""
        if self.conn:
            self.conn.close()
    
    def get_crash_statistics(self) -> Dict[str, Any]:
        """Get overall crash statistics."""
        query = """
            SELECT 
                COUNT(DISTINCT e.program_hash) as unique_crashes,
                COUNT(*) as total_crash_executions,
                COUNT(DISTINCT p.fuzzer_id) as fuzzers_with_crashes,
                MIN(e.created_at) as first_crash,
                MAX(e.created_at) as latest_crash,
                AVG(e.coverage_total) as avg_coverage_at_crash,
                COUNT(*) FILTER (WHERE e.is_new_edge = true) as crashes_with_new_edges,
                COUNT(*) FILTER (WHERE e.turbofan_optimization_bits IS NOT NULL) as crashes_with_turbofan_data
            FROM execution e
            JOIN program p ON e.program_hash = p.program_hash
            JOIN execution_outcome eo ON e.execution_outcome_id = eo.id
            WHERE eo.outcome = 'Crashed'
        """
        
        with self.conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(query)
            result = cursor.fetchone()
            return dict(result) if result else {}
    
    def save_programs_to_files(self, programs_dir: Path, crashes: List[Dict[str, Any]]):
        """Save program base64 data to separate files."""
        programs_dir.mkdir(parents=True, exist_ok=True)
        
        saved_count = 0
        for crash in crashes:
            program_hash = crash.get('program_hash')
            program_base64 = crash.get('program_base64')
            
            if program_hash and program_base64:
                filepath = programs_dir / f"{program_hash}.b64"
                with open(filepath, 'w') as f:
                    f.write(program_base64)
                print(f"\033[92m  ✓ Saved: {program_hash}.b64\033[0m")
                saved_count += 1
                
                # Remove from crash data to keep output clean
                crash.pop('program_base64', None)
        
        print(f"\033[96mSaved {saved_count} program(s) to {programs_dir}\033[0m")
    
    def extract_all_crashes(self, 
                           limit: int = 100, 
                           fuzzer_id: Optional[int] = None,
                           include_program: bool = False,
                           save_programs: bool = False) -> List[Dict[str, Any]]:
        """Extract all crash executions."""
        # Build query dynamically
        program_field = ", f.program_base64" if (include_program or save_programs) else ""
        where_clause = "WHERE eo.outcome = 'Crashed'"
        if fuzzer_id is not None:
            where_clause += f" AND p.fuzzer_id = {fuzzer_id}"
        
        query = f"""
            SELECT 
                e.execution_id,
                e.program_hash,
                p.fuzzer_id,
                p.source_mutator,
                p.created_at as program_created_at,
                e.created_at as crash_time,
                e.coverage_total,
                e.edges_found,
                e.total_edges,
                e.is_new_edge,
                e.turbofan_optimization_bits,
                e.feedback_nexus_count,
                e.stdout,
                e.stderr,
                e.fuzzout,
                mt.name as mutator_name,
                mt.category as mutator_category
                {program_field}
            FROM execution e
            JOIN program p ON e.program_hash = p.program_hash
            JOIN execution_outcome eo ON e.execution_outcome_id = eo.id
            LEFT JOIN mutator_type mt ON e.mutator_type_id = mt.id
            LEFT JOIN fuzzer f ON e.program_hash = f.program_hash
            {where_clause}
            ORDER BY e.created_at DESC
            LIMIT {limit}
        """
        
        with self.conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(query)
            results = cursor.fetchall()
            return [dict(row) for row in results]
    
    def extract_unique_crashes(self,
                               limit: int = 100,
                               fuzzer_id: Optional[int] = None,
                               include_program: bool = False,
                               save_programs: bool = False) -> List[Dict[str, Any]]:
        """Extract unique crashes (deduplicated by program hash)."""
        where_clause = ""
        if fuzzer_id is not None:
            where_clause = f"WHERE ca.fuzzer_id = {fuzzer_id}"
        
        program_field = ", f.program_base64" if (include_program or save_programs) else ""
        
        query = f"""
            SELECT 
                ca.program_hash,
                ca.fuzzer_id,
                ca.crash_count,
                ca.first_crash,
                ca.last_crash,
                ca.mutators_involved,
                ca.max_coverage_before_crash,
                ca.found_new_edges
                {program_field}
            FROM crash_analysis ca
            LEFT JOIN fuzzer f ON ca.program_hash = f.program_hash
            {where_clause}
            ORDER BY ca.crash_count DESC, ca.first_crash DESC
            LIMIT {limit}
        """
        
        with self.conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(query)
            results = cursor.fetchall()
            return [dict(row) for row in results]
    
    def get_crashes_by_fuzzer(self) -> List[Dict[str, Any]]:
        """Get crash count by fuzzer."""
        query = """
            SELECT 
                p.fuzzer_id,
                COUNT(DISTINCT e.program_hash) as unique_crashes,
                COUNT(*) as total_crash_executions,
                MIN(e.created_at) as first_crash,
                MAX(e.created_at) as latest_crash
            FROM execution e
            JOIN program p ON e.program_hash = p.program_hash
            JOIN execution_outcome eo ON e.execution_outcome_id = eo.id
            WHERE eo.outcome = 'Crashed'
            GROUP BY p.fuzzer_id
            ORDER BY unique_crashes DESC
        """
        
        with self.conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(query)
            results = cursor.fetchall()
            return [dict(row) for row in results]
    
    def decode_program(self, program_hash: str) -> Optional[str]:
        """Decode a program from base64 to JavaScript code."""
        query = """
            SELECT program_base64 
            FROM fuzzer 
            WHERE program_hash = %s
        """
        
        with self.conn.cursor() as cursor:
            cursor.execute(query, (program_hash,))
            result = cursor.fetchone()
            if result and result[0]:
                try:
                    # Decode base64
                    decoded = base64.b64decode(result[0])
                    # This is a protobuf-encoded program, would need proper decoding
                    return result[0]  # Return base64 for now
                except Exception as e:
                    print(f"Error decoding program: {e}")
                    return None
            return None


def save_json(data: Any, filepath: Path):
    """Save data to JSON file."""
    from decimal import Decimal
    
    class DateTimeEncoder(json.JSONEncoder):
        def default(self, obj):
            if isinstance(obj, datetime):
                return obj.isoformat()
            elif isinstance(obj, Decimal):
                return float(obj)
            return super().default(obj)
    
    with open(filepath, 'w') as f:
        json.dump(data, f, indent=2, cls=DateTimeEncoder)
    print(f"\033[92m✓ Saved to: {filepath}\033[0m")


def save_csv(data: List[Dict[str, Any]], filepath: Path):
    """Save data to CSV file."""
    if not data:
        print("\033[93mNo data to save\033[0m")
        return
    
    with open(filepath, 'w', newline='') as f:
        # Handle datetime objects
        processed_data = []
        for row in data:
            processed_row = {}
            for key, value in row.items():
                if isinstance(value, datetime):
                    processed_row[key] = value.isoformat()
                else:
                    processed_row[key] = value
            processed_data.append(processed_row)
        
        writer = csv.DictWriter(f, fieldnames=processed_data[0].keys())
        writer.writeheader()
        writer.writerows(processed_data)
    
    print(f"\033[92m✓ Saved to: {filepath}\033[0m")


def save_text(data: List[Dict[str, Any]], filepath: Path):
    """Save data to text file."""
    with open(filepath, 'w') as f:
        for i, item in enumerate(data, 1):
            f.write(f"=== Crash {i} ===\n")
            for key, value in item.items():
                f.write(f"{key}: {value}\n")
            f.write("\n")
    
    print(f"\033[92m✓ Saved to: {filepath}\033[0m")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Extract crashes from Fuzzilli PostgreSQL database',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    parser.add_argument('-f', '--format', 
                       choices=['json', 'csv', 'text'], 
                       default='json',
                       help='Output format (default: json)')
    parser.add_argument('-l', '--limit', 
                       type=int, 
                       default=100,
                       help='Maximum number of crashes to extract (default: 100)')
    parser.add_argument('-o', '--output', 
                       type=str, 
                       default='crashes',
                       help='Output directory (default: ./crashes)')
    parser.add_argument('--fuzzer-id', 
                       type=int,
                       help='Filter by fuzzer ID')
    parser.add_argument('--include-program', 
                       action='store_true',
                       help='Include base64 encoded program in output')
    parser.add_argument('--save-programs', 
                       action='store_true',
                       help='Save each program base64 to a separate file (excludes from main output)')
    parser.add_argument('--unique', 
                       action='store_true', 
                       default=True,
                       help='Extract only unique crashes (default)')
    parser.add_argument('--all', 
                       action='store_true',
                       help='Extract all crash executions')
    parser.add_argument('--stats', 
                       action='store_true',
                       help='Show crash statistics only')
    parser.add_argument('--by-fuzzer', 
                       action='store_true',
                       help='Show crashes grouped by fuzzer')
    
    args = parser.parse_args()
    
    # Get database connection parameters from environment
    host = os.getenv('POSTGRES_HOST', 'localhost')
    port = int(os.getenv('POSTGRES_PORT', '5432'))
    database = os.getenv('POSTGRES_DB', 'fuzzilli_master')
    user = os.getenv('POSTGRES_USER', 'fuzzilli')
    password = os.getenv('POSTGRES_PASSWORD', 'fuzzilli123')
    
    # Create extractor
    extractor = CrashExtractor(host, port, database, user, password)
    
    # Connect to database
    if not extractor.connect():
        sys.exit(1)
    
    print("\033[96m=== Fuzzilli Crash Extraction Tool ===\033[0m")
    print()
    
    try:
        # Show statistics
        if args.stats:
            stats = extractor.get_crash_statistics()
            print("\033[92m=== Crash Statistics ===\033[0m")
            for key, value in stats.items():
                print(f"  {key}: {value}")
            return
        
        # Show by fuzzer
        if args.by_fuzzer:
            crashes = extractor.get_crashes_by_fuzzer()
            print("\033[92m=== Crashes by Fuzzer ===\033[0m")
            for fuzzer in crashes:
                print(f"Fuzzer {fuzzer['fuzzer_id']}: {fuzzer['unique_crashes']} unique crashes, "
                      f"{fuzzer['total_crash_executions']} total")
            return
        
        # Extract crashes
        output_dir = Path(args.output)
        output_dir.mkdir(parents=True, exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        if args.all:
            print("\033[92mExtracting all crash executions...\033[0m")
            crashes = extractor.extract_all_crashes(
                limit=args.limit,
                fuzzer_id=args.fuzzer_id,
                include_program=args.include_program,
                save_programs=args.save_programs
            )
            filename_prefix = "crashes"
        else:
            print("\033[92mExtracting unique crashes...\033[0m")
            crashes = extractor.extract_unique_crashes(
                limit=args.limit,
                fuzzer_id=args.fuzzer_id,
                include_program=args.include_program,
                save_programs=args.save_programs
            )
            filename_prefix = "unique_crashes"
        
        print(f"Found {len(crashes)} crashes")
        
        # Save programs to separate files if requested
        if args.save_programs and crashes:
            programs_dir = output_dir / f"programs_{timestamp}"
            print(f"\n\033[93mSaving programs to separate files...\033[0m")
            extractor.save_programs_to_files(programs_dir, crashes)
            print()
        
        # Save to file
        if args.format == 'json':
            filepath = output_dir / f"{filename_prefix}_{timestamp}.json"
            save_json(crashes, filepath)
        elif args.format == 'csv':
            filepath = output_dir / f"{filename_prefix}_{timestamp}.csv"
            save_csv(crashes, filepath)
        elif args.format == 'text':
            filepath = output_dir / f"{filename_prefix}_{timestamp}.txt"
            save_text(crashes, filepath)
        
        print()
        print("\033[92mExtraction complete!\033[0m")
        
    finally:
        extractor.disconnect()


if __name__ == '__main__':
    main()
