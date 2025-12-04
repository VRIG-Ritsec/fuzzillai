-- Optimized Fuzzilli PostgreSQL Database Schema

-- Main fuzzer instance table
CREATE TABLE IF NOT EXISTS main (
    fuzzer_id SERIAL PRIMARY KEY,
    created_at TIMESTAMP DEFAULT NOW(),
    status VARCHAR(20) DEFAULT 'active',
    last_activity TIMESTAMP DEFAULT NOW(),
    engine_arguments TEXT[] -- Engine arguments used by this fuzzer instance
);

CREATE INDEX idx_main_status ON main(status) WHERE status = 'active';
CREATE INDEX idx_main_last_activity ON main(last_activity DESC);

-- Fuzzer programs table (corpus) - reduced storage
CREATE TABLE IF NOT EXISTS fuzzer (
    program_hash VARCHAR(64) PRIMARY KEY,
    fuzzer_id INT NOT NULL REFERENCES main(fuzzer_id) ON DELETE CASCADE,
    inserted_at TIMESTAMP DEFAULT NOW(),
    -- program_size INT NOT NULL, don't need this make sure to remove 
    program_base64 TEXT NOT NULL
);

CREATE INDEX idx_fuzzer_id ON fuzzer(fuzzer_id);
CREATE INDEX idx_fuzzer_inserted ON fuzzer(inserted_at DESC);
CREATE INDEX idx_fuzzer_composite ON fuzzer(fuzzer_id, inserted_at DESC);



-- Programs table (executed data about a program) - no duplicate storage
CREATE TABLE IF NOT EXISTS program (
    program_hash VARCHAR(64) PRIMARY KEY REFERENCES fuzzer(program_hash) ON DELETE CASCADE,
    fuzzer_id INT NOT NULL REFERENCES main(fuzzer_id) ON DELETE CASCADE,
    created_at TIMESTAMP DEFAULT NOW(),
    source_mutators VARCHAR(50)[],  -- Array of mutator names that contributed to this program
    contributors VARCHAR(50)[],      -- Array of all contributor names (mutators + other sources)
    parent_program_hash VARCHAR(64) REFERENCES program(program_hash) ON DELETE SET NULL
);

CREATE INDEX idx_program_fuzzer ON program(fuzzer_id);
CREATE INDEX idx_program_created ON program(created_at DESC);
CREATE INDEX idx_program_mutators ON program USING GIN(source_mutators);  -- GIN index for array searching
CREATE INDEX idx_program_contributors ON program USING GIN(contributors);  -- GIN index for array searching
CREATE INDEX idx_program_parent ON program(parent_program_hash);
CREATE INDEX idx_program_fuzzer_created ON program(fuzzer_id, created_at DESC);

-- Mutator type lookup table (must be created before mutator_stats references it)
CREATE TABLE IF NOT EXISTS mutator_type (
    id SMALLINT PRIMARY KEY,
    name VARCHAR(50) NOT NULL UNIQUE,
    category VARCHAR(30)
);

-- Add mutator statistics table (per fuzzer instance)
CREATE TABLE IF NOT EXISTS mutator_stats (
    fuzzer_id INT NOT NULL REFERENCES main(fuzzer_id) ON DELETE CASCADE,
    mutator_type_id SMALLINT NOT NULL REFERENCES mutator_type(id),
    total_samples BIGINT DEFAULT 0,
    crashes_found INT DEFAULT 0,
    timeouts INT DEFAULT 0,
    interesting_samples INT DEFAULT 0,
    invalid_samples INT DEFAULT 0,
    valid_samples INT DEFAULT 0,
    total_instructions_added BIGINT DEFAULT 0,
    correctness_rate NUMERIC(5,2),
    failure_rate NUMERIC(5,2),
    timeout_rate NUMERIC(5,2),
    interesting_samples_rate NUMERIC(5,2),
    avg_instructions_added NUMERIC(8,2),
    last_updated TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (fuzzer_id, mutator_type_id)
);

CREATE INDEX idx_mutator_stats_fuzzer ON mutator_stats(fuzzer_id);
CREATE INDEX idx_mutator_stats_mutator ON mutator_stats(mutator_type_id);

INSERT INTO mutator_type (id, name, category) VALUES 
    (1, 'ExplorationMutator', 'runtime_assisted'),
    (2, 'CodeGenMutator', 'instruction'),
    (3, 'SpliceMutator', 'instruction'),
    (4, 'ProbingMutator', 'runtime_assisted'),
    (5, 'InputMutator', 'instruction'),
    (6, 'OperationMutator', 'instruction'),
    (7, 'CombineMutator', 'instruction'),
    (8, 'ConcatMutator', 'base'),
    (9, 'FixupMutator', 'runtime_assisted'),
    (10, 'RuntimeAssistedMutator', 'runtime_assisted'),
    (11, 'InputMutator (loose)', 'instruction'),
    (12, 'InputMutator (aware)', 'instruction')
ON CONFLICT (id) DO NOTHING;

-- CREATE INDEX idx_mutator_category ON mutator_type(category);

-- Execution outcome lookup table
CREATE TABLE IF NOT EXISTS execution_outcome (
    id SMALLINT PRIMARY KEY,
    outcome VARCHAR(20) NOT NULL UNIQUE
);

INSERT INTO execution_outcome (id, outcome) VALUES 
    (1, 'Crashed'),
    (2, 'Failed'),
    (3, 'Succeeded'),
    (4, 'TimedOut')
ON CONFLICT (id) DO NOTHING;

-- Main execution table - partitioned for performance
CREATE TABLE IF NOT EXISTS execution (
    execution_id BIGSERIAL PRIMARY KEY,
    program_hash VARCHAR(64) NOT NULL REFERENCES program(program_hash) ON DELETE CASCADE,
    -- id of if the system crashed, failed, succeeded, timed out, or sigcheck
    execution_outcome_id SMALLINT NOT NULL REFERENCES execution_outcome(id), 
    coverage_total NUMERIC(5,2) CHECK (coverage_total >= 0 AND coverage_total <= 100), 
    -- number of edges found in the execution
    edges_found INT CHECK (edges_found >= 0),
    total_edges INT CHECK (total_edges >= 0),
    is_new_edge BOOLEAN DEFAULT FALSE,
    stdout TEXT,
    stderr TEXT,
    fuzzout TEXT,
    turbofan_optimization_bits BIGINT,
    feedback_nexus_count INT, 
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_execution_program ON execution(program_hash);
CREATE INDEX idx_execution_outcome ON execution(execution_outcome_id);
CREATE INDEX idx_execution_program_outcome ON execution(program_hash, execution_outcome_id);
CREATE INDEX idx_execution_created_desc ON execution(created_at DESC);
CREATE INDEX idx_execution_coverage_desc ON execution(coverage_total DESC NULLS LAST) WHERE coverage_total IS NOT NULL;
CREATE INDEX idx_execution_crashes ON execution(execution_outcome_id) WHERE execution_outcome_id = 1;
CREATE INDEX idx_execution_new_edges ON execution(execution_id) WHERE is_new_edge = TRUE;
CREATE INDEX idx_execution_edges ON execution(edges_found, total_edges) WHERE edges_found IS NOT NULL;

-- Feedback vector, this is def not correct 
-- CREATE TABLE IF NOT EXISTS feedback_vector_detail (
--     id BIGSERIAL PRIMARY KEY,
--     feedback metadata 
--           slot_count 
--           data_[]
--     feedbacks slots kind
--     --- this is an enum for the type of feedback stored in a slot, we can access this via GetKind 
--     MapsAndHandlers: 
--          maps_[]
--          handlers_[]
--     FeedbackIterator
--          state_
--          polymorphic_state_
--     ClosureFeedbackCaellArray
--          elements_[]

-- );

-- this was ai tweek of the above table, but it was not correct
-- CREATE TABLE IF NOT EXISTS feedback_vector_detail (
--     id BIGSERIAL PRIMARY KEY,
--     execution_id BIGINT NOT NULL REFERENCES execution(execution_id) ON DELETE CASCADE,
--     slot_count INT NOT NULL,
--     slot_index INT NOT NULL,
--     slot_kind VARCHAR(50) NOT NULL,
--     maps JSONB,
--     handlers JSONB,
--     polymorphic_state VARCHAR(30),
--     elements JSONB,
--     created_at TIMESTAMP DEFAULT NOW(),
--     CONSTRAINT unique_execution_slot UNIQUE (execution_id, slot_index)
-- );

-- CREATE INDEX idx_feedback_execution ON feedback_vector_detail(execution_id);
-- CREATE INDEX idx_feedback_slot_kind ON feedback_vector_detail(slot_kind);
-- CREATE INDEX idx_feedback_polymorphic ON feedback_vector_detail(polymorphic_state) WHERE polymorphic_state IS NOT NULL;
-- CREATE INDEX idx_feedback_maps ON feedback_vector_detail USING GIN(maps) WHERE maps IS NOT NULL;
-- CREATE INDEX idx_feedback_handlers ON feedback_vector_detail USING GIN(handlers) WHERE handlers IS NOT NULL;

-- Materialized view: Fuzzer performance dashboard
CREATE MATERIALIZED VIEW IF NOT EXISTS fuzzer_dashboard AS
SELECT 
    m.fuzzer_id,
    m.status,
    m.created_at as fuzzer_started,
    m.last_activity,
    COUNT(DISTINCT p.program_hash) as total_programs,
    COUNT(e.execution_id) as total_executions,
    COUNT(e.execution_id) FILTER (WHERE e.created_at > NOW() - INTERVAL '1 hour') as executions_last_hour,
    ROUND(COUNT(e.execution_id) FILTER (WHERE e.created_at > NOW() - INTERVAL '1 hour')::NUMERIC / 3600.0, 2) as execs_per_second,
    COUNT(e.execution_id) FILTER (WHERE e.execution_outcome_id = 1) as total_crashes,
    COUNT(e.execution_id) FILTER (WHERE e.execution_outcome_id = 1 AND e.created_at > NOW() - INTERVAL '1 hour') as crashes_last_hour,
    COUNT(e.execution_id) FILTER (WHERE e.is_new_edge = TRUE) as new_edges_found,
    MAX(e.coverage_total) as max_coverage,
    AVG(e.coverage_total) FILTER (WHERE e.coverage_total IS NOT NULL) as avg_coverage,
    MAX(e.edges_found) as max_edges_found,
    NOW() as refreshed_at
FROM main m
LEFT JOIN program p ON m.fuzzer_id = p.fuzzer_id
LEFT JOIN execution e ON p.program_hash = e.program_hash
GROUP BY m.fuzzer_id, m.status, m.created_at, m.last_activity;

CREATE UNIQUE INDEX idx_fuzzer_dashboard_id ON fuzzer_dashboard(fuzzer_id);

-- Per-fuzzer mutator effectiveness
CREATE MATERIALIZED VIEW IF NOT EXISTS mutator_effectiveness_per_fuzzer AS
SELECT 
    ms.fuzzer_id,
    m.fuzzer_id as main_fuzzer_id,
    m.status,
    mt.id as mutator_id,
    mt.name as mutator_name,
    mt.category,
    ms.total_samples,
    ms.crashes_found,
    ms.interesting_samples,
    ms.correctness_rate,
    ms.failure_rate,
    ms.timeout_rate,
    ms.interesting_samples_rate,
    ms.avg_instructions_added,
    ms.last_updated
FROM mutator_stats ms
JOIN mutator_type mt ON ms.mutator_type_id = mt.id
JOIN main m ON ms.fuzzer_id = m.fuzzer_id;

CREATE UNIQUE INDEX idx_mutator_effectiveness_per_fuzzer_id ON mutator_effectiveness_per_fuzzer(fuzzer_id, mutator_id);

-- Aggregate across all fuzzers (campaign-wide view)
CREATE MATERIALIZED VIEW IF NOT EXISTS mutator_effectiveness_aggregate AS
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

-- Materialized view: Coverage progression
CREATE MATERIALIZED VIEW IF NOT EXISTS coverage_progression AS
SELECT 
    p.fuzzer_id,
    DATE_TRUNC('hour', e.created_at) as time_bucket,
    MAX(e.coverage_total) as max_coverage,
    AVG(e.coverage_total) as avg_coverage,
    MAX(e.edges_found) as max_edges_found,
    COUNT(e.execution_id) FILTER (WHERE e.is_new_edge = TRUE) as new_edges_count,
    COUNT(e.execution_id) as execution_count
FROM execution e
JOIN program p ON e.program_hash = p.program_hash
WHERE e.coverage_total IS NOT NULL
GROUP BY p.fuzzer_id, DATE_TRUNC('hour', e.created_at);

CREATE INDEX idx_coverage_progression_fuzzer ON coverage_progression(fuzzer_id, time_bucket DESC);
CREATE UNIQUE INDEX idx_coverage_progression_unique ON coverage_progression(fuzzer_id, time_bucket);

-- Materialized view: Crash analysis
CREATE MATERIALIZED VIEW IF NOT EXISTS crash_analysis AS
SELECT 
    p.fuzzer_id,
    e.program_hash,
    COUNT(*) as crash_count,
    MIN(e.created_at) as first_crash,
    MAX(e.created_at) as last_crash,
    MAX(e.coverage_total) as max_coverage_before_crash,
    BOOL_OR(e.is_new_edge) as found_new_edges
FROM execution e
JOIN program p ON e.program_hash = p.program_hash
WHERE e.execution_outcome_id = 1
GROUP BY p.fuzzer_id, e.program_hash;

CREATE INDEX idx_crash_analysis_fuzzer ON crash_analysis(fuzzer_id);
CREATE INDEX idx_crash_analysis_count ON crash_analysis(crash_count DESC);
CREATE UNIQUE INDEX idx_crash_analysis_unique ON crash_analysis(fuzzer_id, program_hash);

-- Materialized view: Program convergence analysis
-- Tracks similar program sizes and their outcomes to detect convergence patterns
CREATE MATERIALIZED VIEW IF NOT EXISTS program_convergence AS
SELECT 
    p.fuzzer_id,
    DATE_TRUNC('hour', p.created_at) as time_bucket,
    LENGTH(f.program_base64) as program_size,
    COUNT(DISTINCT p.program_hash) as unique_programs,
    COUNT(e.execution_id) as total_executions,
    COUNT(e.execution_id) FILTER (WHERE e.execution_outcome_id = 1) as crashes,
    COUNT(e.execution_id) FILTER (WHERE e.execution_outcome_id = 2) as failures,
    COUNT(e.execution_id) FILTER (WHERE e.execution_outcome_id = 3) as successes,
    COUNT(e.execution_id) FILTER (WHERE e.execution_outcome_id = 4) as timeouts,
    AVG(e.coverage_total) as avg_coverage,
    MAX(e.coverage_total) as max_coverage,
    COUNT(e.execution_id) FILTER (WHERE e.is_new_edge = TRUE) as new_edges_found
FROM program p
JOIN fuzzer f ON p.program_hash = f.program_hash
LEFT JOIN execution e ON p.program_hash = e.program_hash
GROUP BY p.fuzzer_id, DATE_TRUNC('hour', p.created_at), LENGTH(f.program_base64);

CREATE INDEX idx_program_convergence_fuzzer ON program_convergence(fuzzer_id, time_bucket DESC);
CREATE INDEX idx_program_convergence_size ON program_convergence(program_size);
CREATE UNIQUE INDEX idx_program_convergence_unique ON program_convergence(fuzzer_id, time_bucket, program_size);

-- Materialized view: Execution outcome distribution over time
-- Provides time-series data on execution outcomes for trend analysis
CREATE MATERIALIZED VIEW IF NOT EXISTS execution_outcome_distribution AS
SELECT 
    p.fuzzer_id,
    DATE_TRUNC('minute', e.created_at) as time_bucket,
    eo.outcome,
    e.execution_outcome_id,
    COUNT(e.execution_id) as execution_count,
    AVG(e.coverage_total) FILTER (WHERE e.coverage_total IS NOT NULL) as avg_coverage,
    COUNT(e.execution_id) FILTER (WHERE e.is_new_edge = TRUE) as new_edges_count
FROM execution e
JOIN program p ON e.program_hash = p.program_hash
JOIN execution_outcome eo ON e.execution_outcome_id = eo.id
GROUP BY p.fuzzer_id, DATE_TRUNC('minute', e.created_at), eo.outcome, e.execution_outcome_id;

CREATE INDEX idx_execution_outcome_dist_fuzzer ON execution_outcome_distribution(fuzzer_id, time_bucket DESC);
CREATE INDEX idx_execution_outcome_dist_outcome ON execution_outcome_distribution(execution_outcome_id);
CREATE UNIQUE INDEX idx_execution_outcome_dist_unique ON execution_outcome_distribution(fuzzer_id, time_bucket, execution_outcome_id);

-- Materialized view: Program coverage mapping
-- Maps programs to their best coverage and execution stats
CREATE MATERIALIZED VIEW IF NOT EXISTS program_coverage_mapping AS
SELECT 
    p.fuzzer_id,
    p.program_hash,
    p.created_at,
    p.source_mutators,
    p.contributors,
    COUNT(e.execution_id) as execution_count,
    MAX(e.coverage_total) as max_coverage,
    AVG(e.coverage_total) FILTER (WHERE e.coverage_total IS NOT NULL) as avg_coverage,
    MAX(e.edges_found) as max_edges_found,
    AVG(e.edges_found) FILTER (WHERE e.edges_found IS NOT NULL) as avg_edges_found,
    COUNT(e.execution_id) FILTER (WHERE e.is_new_edge = TRUE) as new_edges_discovered,
    COUNT(e.execution_id) FILTER (WHERE e.execution_outcome_id = 1) as crash_count,
    COUNT(e.execution_id) FILTER (WHERE e.execution_outcome_id = 3) as success_count,
    COUNT(e.execution_id) FILTER (WHERE e.execution_outcome_id = 4) as timeout_count,
    LENGTH(f.program_base64) as program_size,
    MIN(e.created_at) as first_execution,
    MAX(e.created_at) as last_execution
FROM program p
JOIN fuzzer f ON p.program_hash = f.program_hash
LEFT JOIN execution e ON p.program_hash = e.program_hash
GROUP BY p.fuzzer_id, p.program_hash, p.created_at, p.source_mutators, p.contributors, f.program_base64;

CREATE INDEX idx_program_coverage_mapping_fuzzer ON program_coverage_mapping(fuzzer_id);
CREATE INDEX idx_program_coverage_mapping_coverage ON program_coverage_mapping(max_coverage DESC NULLS LAST);
CREATE INDEX idx_program_coverage_mapping_edges ON program_coverage_mapping(max_edges_found DESC NULLS LAST);
CREATE INDEX idx_program_coverage_mapping_new_edges ON program_coverage_mapping(new_edges_discovered DESC);
CREATE UNIQUE INDEX idx_program_coverage_mapping_unique ON program_coverage_mapping(program_hash);

-- Materialized view: Program lineage (mutation tree)
CREATE MATERIALIZED VIEW IF NOT EXISTS program_lineage AS
WITH RECURSIVE lineage AS (
    SELECT 
        program_hash,
        fuzzer_id,
        parent_program_hash,
        source_mutators,
        created_at,
        1 as generation,
        program_hash::TEXT as lineage_path
    FROM program
    WHERE parent_program_hash IS NULL
    
    UNION ALL
    
    SELECT 
        p.program_hash,
        p.fuzzer_id,
        p.parent_program_hash,
        p.source_mutators,
        p.created_at,
        l.generation + 1,
        l.lineage_path || ' -> ' || p.program_hash
    FROM program p
    JOIN lineage l ON p.parent_program_hash = l.program_hash
    WHERE l.generation < 100
)
SELECT 
    l.*,
    (SELECT COUNT(*) FROM program WHERE parent_program_hash = l.program_hash) as child_count,
    (SELECT COUNT(*) FROM execution e WHERE e.program_hash = l.program_hash AND e.execution_outcome_id = 1) as crash_count,
    (SELECT MAX(coverage_total) FROM execution e WHERE e.program_hash = l.program_hash) as max_coverage
FROM lineage l;

CREATE INDEX idx_program_lineage_fuzzer ON program_lineage(fuzzer_id);
CREATE INDEX idx_program_lineage_generation ON program_lineage(generation);
CREATE UNIQUE INDEX idx_program_lineage_unique ON program_lineage(program_hash);

-- Materialized view: Feedback slot statistics
-- COMMENTED OUT: references feedback_vector_detail which is commented out
-- CREATE MATERIALIZED VIEW IF NOT EXISTS feedback_slot_stats AS
-- SELECT 
--     slot_kind,
--     COUNT(DISTINCT execution_id) as execution_count,
--     COUNT(*) as total_slots,
--     AVG(slot_index) as avg_slot_index,
--     COUNT(*) FILTER (WHERE maps IS NOT NULL) as slots_with_maps,
--     COUNT(*) FILTER (WHERE handlers IS NOT NULL) as slots_with_handlers,
--     COUNT(*) FILTER (WHERE polymorphic_state IS NOT NULL) as polymorphic_slots,
--     NOW() as refreshed_at
-- FROM feedback_vector_detail
-- GROUP BY slot_kind;

-- CREATE UNIQUE INDEX idx_feedback_slot_stats_kind ON feedback_slot_stats(slot_kind);

-- View: Recent activity (not materialized, always current)
CREATE OR REPLACE VIEW recent_activity AS
SELECT 
    e.execution_id,
    e.program_hash,
    p.fuzzer_id,
    array_to_string(p.source_mutators, ', ') as source_mutators,
    eo.outcome,
    e.coverage_total,
    e.edges_found,
    e.is_new_edge,
    e.created_at
FROM execution e
JOIN program p ON e.program_hash = p.program_hash
JOIN execution_outcome eo ON e.execution_outcome_id = eo.id
WHERE e.created_at > NOW() - INTERVAL '1 hour'
ORDER BY e.created_at DESC;

-- View: Top performing programs
CREATE OR REPLACE VIEW top_performing_programs AS
SELECT 
    p.program_hash,
    p.fuzzer_id,
    p.source_mutators,
    p.contributors,
    COUNT(e.execution_id) as execution_count,
    MAX(e.coverage_total) as max_coverage,
    AVG(e.coverage_total) as avg_coverage,
    COUNT(*) FILTER (WHERE e.is_new_edge = TRUE) as new_edges_found,
    MIN(e.created_at) as first_execution,
    MAX(e.created_at) as last_execution
FROM program p
LEFT JOIN execution e ON p.program_hash = e.program_hash
GROUP BY p.program_hash, p.fuzzer_id, p.source_mutators, p.contributors
HAVING COUNT(e.execution_id) > 0
ORDER BY new_edges_found DESC, max_coverage DESC
LIMIT 1000;

-- Function: Refresh all materialized views
CREATE OR REPLACE FUNCTION refresh_all_stats()
RETURNS TABLE(view_name TEXT, refresh_time INTERVAL) AS $$
DECLARE
    start_time TIMESTAMP;
    view_record RECORD;
BEGIN
    FOR view_record IN 
        SELECT matviewname FROM pg_matviews WHERE schemaname = 'public'
    LOOP
        start_time := clock_timestamp();
        EXECUTE 'REFRESH MATERIALIZED VIEW CONCURRENTLY ' || view_record.matviewname;
        view_name := view_record.matviewname;
        refresh_time := clock_timestamp() - start_time;
        RETURN NEXT;
    END LOOP;
END;
$$ LANGUAGE plpgsql;

-- Function: Get fuzzer statistics
CREATE OR REPLACE FUNCTION get_fuzzer_stats(p_fuzzer_id INTEGER)
RETURNS TABLE(
    total_programs BIGINT,
    total_executions BIGINT,
    total_crashes BIGINT,
    unique_crashes BIGINT,
    max_coverage NUMERIC,
    new_edges BIGINT,
    execs_per_hour NUMERIC
) AS $$
BEGIN
    RETURN QUERY
    SELECT 
        COUNT(DISTINCT p.program_hash)::BIGINT as total_programs,
        COUNT(e.execution_id)::BIGINT as total_executions,
        COUNT(e.execution_id) FILTER (WHERE e.execution_outcome_id = 1)::BIGINT as total_crashes,
        COUNT(DISTINCT e.program_hash) FILTER (WHERE e.execution_outcome_id = 1)::BIGINT as unique_crashes,
        MAX(e.coverage_total) as max_coverage,
        COUNT(e.execution_id) FILTER (WHERE e.is_new_edge = TRUE)::BIGINT as new_edges,
        ROUND(COUNT(e.execution_id) FILTER (WHERE e.created_at > NOW() - INTERVAL '1 hour')::NUMERIC, 2) as execs_per_hour
    FROM program p
    LEFT JOIN execution e ON p.program_hash = e.program_hash
    WHERE p.fuzzer_id = p_fuzzer_id;
END;
$$ LANGUAGE plpgsql STABLE;

-- Function: Update fuzzer last activity
CREATE OR REPLACE FUNCTION update_fuzzer_last_activity()
RETURNS TRIGGER AS $$
BEGIN
    UPDATE main 
    SET last_activity = NEW.created_at
    WHERE fuzzer_id = (SELECT fuzzer_id FROM program WHERE program_hash = NEW.program_hash);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_update_last_activity
AFTER INSERT ON execution
FOR EACH ROW
EXECUTE FUNCTION update_fuzzer_last_activity();

-- Function: Calculate coverage percentage
CREATE OR REPLACE FUNCTION calculate_coverage_percentage(p_edges_found INT, p_total_edges INT)
RETURNS NUMERIC AS $$
BEGIN
    IF p_total_edges IS NULL OR p_total_edges = 0 THEN
        RETURN NULL;
    END IF;
    RETURN ROUND((p_edges_found::NUMERIC / p_total_edges::NUMERIC) * 100, 2);
END;
$$ LANGUAGE plpgsql IMMUTABLE;

-- Grant permissions
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO fuzzilli;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO fuzzilli;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO fuzzilli;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO fuzzilli;
