#!/usr/bin/env python3
from agents.EBG import *

import time

if __name__ == '__main__':
    start = time.time()
    print(f"Started at: {start}")
    #print(db_get_execution_outcome_distribution(fuzzer_id=7))
    #print(db_list_programs(limit=2, fuzzer_id=7, include_source=True))
    #print(db_get_fuzzer_performance_summary(7))
    #print(db_query("SELECT program_hash, fuzzer_id, created_at, program_size, source_mutator, parent_program_hash FROM program WHERE fuzzer_id = %s LIMIT %s", [7,2]))
    #print(db_query("SELECT source_mutator, COUNT(*) FROM program WHERE fuzzer_id = %s GROUP BY source_mutator LIMIT %s;", [1, 10]))
    #print(db_get_mutator_effectiveness(fuzzer_id=7))
    for i in range(8):
        print(db_get_crash_diversity(fuzzer_id=i+1))
    #print(db_get_program_convergence(fuzzer_id=1))
    #print(db_get_program_coverage_mapping(fuzzer_id=7))
    end = time.time()

    print(f"Ended at: {end}")
    print(f"Elapsed time: {end-start}")
