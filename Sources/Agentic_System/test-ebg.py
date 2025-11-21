#!/usr/bin/env python3
from agents.EBG import *

from config_loader import get_openai_api_key, get_anthropic_api_key, get_deepseek_api_key
logger = logging.getLogger("boiling_eggs")
if not logger.handlers:
    logger.addHandler(logging.NullHandler())
logger.propagate = False
logger.disabled = True
est_timezone = pytz.timezone('US/Eastern')

if __name__ == '__main__':
    #print(db_get_execution_outcome_distribution(fuzzer_id=7))
    #db_list_programs(limit=2, fuzzer_id=7)
    #print(db_query("SELECT program_hash, fuzzer_id, created_at, program_size, source_mutator, parent_program_hash FROM program WHERE fuzzer_id = %s LIMIT %s", [7,2]))
    #print(db_query("SELECT source_mutator, COUNT(*) FROM program WHERE fuzzer_id = %s GROUP BY source_mutator LIMIT %s;", [1, 10]))
    #print(db_get_program_convergence(fuzzer_id=7))
    #print(db_get_mutator_effectiveness(fuzzer_id=7))
    #print(db_get_crash_diversity(fuzzer_id=7))