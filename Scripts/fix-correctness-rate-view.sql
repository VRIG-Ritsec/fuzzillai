-- Fix correctness_rate in materialized view to handle both decimal (0.0-1.0) and percentage (0-100) formats
-- This migration normalizes old decimal values to percentages before averaging

-- Drop and recreate the materialized view with the fix
DROP MATERIALIZED VIEW IF EXISTS mutator_effectiveness_aggregate;

CREATE MATERIALIZED VIEW mutator_effectiveness_aggregate AS
SELECT 
    mt.id as mutator_id,
    mt.name as mutator_name,
    mt.category,
    SUM(ms.total_samples) as total_samples,
    SUM(ms.crashes_found) as total_crashes_found,
    SUM(ms.interesting_samples) as total_interesting_samples,
    AVG(CASE 
        WHEN ms.correctness_rate < 1.0 THEN ms.correctness_rate * 100
        ELSE ms.correctness_rate
    END) as avg_correctness_rate,
    AVG(CASE 
        WHEN ms.failure_rate < 1.0 THEN ms.failure_rate * 100
        ELSE ms.failure_rate
    END) as avg_failure_rate,
    AVG(ms.avg_instructions_added) as avg_instructions_added,
    COUNT(DISTINCT ms.fuzzer_id) as active_fuzzers_using_mutator,
    NOW() as refreshed_at
FROM mutator_stats ms
JOIN mutator_type mt ON ms.mutator_type_id = mt.id
GROUP BY mt.id, mt.name, mt.category;

CREATE UNIQUE INDEX idx_mutator_effectiveness_aggregate_id ON mutator_effectiveness_aggregate(mutator_id);

