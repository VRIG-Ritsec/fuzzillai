import Foundation
import PostgresNIO
import Logging

/// Manages database schema creation and verification
public class DatabaseSchema {
    private let logger: Logger
    private let enableLogging: Bool
    
    public init(enableLogging: Bool = false) {
        self.enableLogging = enableLogging
        self.logger = Logger(withLabel: "DatabaseSchema")
    }
    
    /// The complete database schema SQL
    public static let schemaSQL = """
    -- Optimized Fuzzilli PostgreSQL Database Schema
    
    -- Main fuzzer instance table
    CREATE TABLE IF NOT EXISTS main (
        fuzzer_id SERIAL PRIMARY KEY,
        created_at TIMESTAMP DEFAULT NOW(),
        status VARCHAR(20) DEFAULT 'active',
        last_activity TIMESTAMP DEFAULT NOW(),
        engine_arguments TEXT[] -- Engine arguments used by this fuzzer instance
    );
    
    CREATE INDEX IF NOT EXISTS idx_main_status ON main(status) WHERE status = 'active';
    CREATE INDEX IF NOT EXISTS idx_main_last_activity ON main(last_activity DESC);
    
    -- Fuzzer programs table (corpus) - reduced storage
    CREATE TABLE IF NOT EXISTS fuzzer (
        program_hash VARCHAR(64) PRIMARY KEY,
        fuzzer_id INT NOT NULL REFERENCES main(fuzzer_id) ON DELETE CASCADE,
        inserted_at TIMESTAMP DEFAULT NOW(),
        -- program_size INT NOT NULL, don't need this make sure to remove 
        program_base64 TEXT NOT NULL
    );
    
    CREATE INDEX IF NOT EXISTS idx_fuzzer_id ON fuzzer(fuzzer_id);
    CREATE INDEX IF NOT EXISTS idx_fuzzer_inserted ON fuzzer(inserted_at DESC);
    CREATE INDEX IF NOT EXISTS idx_fuzzer_composite ON fuzzer(fuzzer_id, inserted_at DESC);
    
    -- Programs table (executed data about a program) - no duplicate storage
    CREATE TABLE IF NOT EXISTS program (
        program_hash VARCHAR(64) PRIMARY KEY REFERENCES fuzzer(program_hash) ON DELETE CASCADE,
        fuzzer_id INT NOT NULL REFERENCES main(fuzzer_id) ON DELETE CASCADE,
        created_at TIMESTAMP DEFAULT NOW(),
        source_mutator VARCHAR(50),
        parent_program_hash VARCHAR(64) REFERENCES program(program_hash) ON DELETE SET NULL
    );
    
    CREATE INDEX IF NOT EXISTS idx_program_fuzzer ON program(fuzzer_id);
    CREATE INDEX IF NOT EXISTS idx_program_created ON program(created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_program_mutator ON program(source_mutator);
    CREATE INDEX IF NOT EXISTS idx_program_parent ON program(parent_program_hash);
    CREATE INDEX IF NOT EXISTS idx_program_fuzzer_created ON program(fuzzer_id, created_at DESC);
    
    -- Mutator type lookup table
    CREATE TABLE IF NOT EXISTS mutator_type (
        id SMALLINT PRIMARY KEY,
        name VARCHAR(50) NOT NULL UNIQUE,
        category VARCHAR(30)
    );
    
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
        (10, 'RuntimeAssistedMutator', 'runtime_assisted')
    ON CONFLICT (id) DO NOTHING;
    
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
        -- id of the mutator that was used to execute the program
        mutator_type_id SMALLINT REFERENCES mutator_type(id),
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
    
    CREATE INDEX IF NOT EXISTS idx_execution_program ON execution(program_hash);
    CREATE INDEX IF NOT EXISTS idx_execution_outcome ON execution(execution_outcome_id);
    CREATE INDEX IF NOT EXISTS idx_execution_program_outcome ON execution(program_hash, execution_outcome_id);
    CREATE INDEX IF NOT EXISTS idx_execution_created_desc ON execution(created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_execution_coverage_desc ON execution(coverage_total DESC NULLS LAST) WHERE coverage_total IS NOT NULL;
    CREATE INDEX IF NOT EXISTS idx_execution_crashes ON execution(execution_outcome_id) WHERE execution_outcome_id = 1;
    CREATE INDEX IF NOT EXISTS idx_execution_new_edges ON execution(execution_id) WHERE is_new_edge = TRUE;
    CREATE INDEX IF NOT EXISTS idx_execution_mutator ON execution(mutator_type_id) WHERE mutator_type_id IS NOT NULL;
    CREATE INDEX IF NOT EXISTS idx_execution_edges ON execution(edges_found, total_edges) WHERE edges_found IS NOT NULL;
    
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
    
    CREATE UNIQUE INDEX IF NOT EXISTS idx_fuzzer_dashboard_id ON fuzzer_dashboard(fuzzer_id);
    
    -- Materialized view: Mutator effectiveness
    CREATE MATERIALIZED VIEW IF NOT EXISTS mutator_effectiveness AS
    SELECT 
        mt.id as mutator_id,
        mt.name as mutator_name,
        mt.category,
        COUNT(e.execution_id) as total_executions,
        COUNT(e.execution_id) FILTER (WHERE e.is_new_edge = TRUE) as new_edges_found,
        ROUND(COUNT(e.execution_id) FILTER (WHERE e.is_new_edge = TRUE)::NUMERIC / 
              NULLIF(COUNT(e.execution_id), 0) * 100, 2) as edge_discovery_rate,
        COUNT(e.execution_id) FILTER (WHERE e.execution_outcome_id = 1) as crashes_found,
        AVG(e.coverage_total) FILTER (WHERE e.coverage_total IS NOT NULL) as avg_coverage,
        MAX(e.coverage_total) as max_coverage,
        NOW() as refreshed_at
    FROM mutator_type mt
    LEFT JOIN execution e ON mt.id = e.mutator_type_id
    GROUP BY mt.id, mt.name, mt.category;
    
    CREATE UNIQUE INDEX IF NOT EXISTS idx_mutator_effectiveness_id ON mutator_effectiveness(mutator_id);
    
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
    
    CREATE INDEX IF NOT EXISTS idx_coverage_progression_fuzzer ON coverage_progression(fuzzer_id, time_bucket DESC);
    
    -- Materialized view: Crash analysis
    CREATE MATERIALIZED VIEW IF NOT EXISTS crash_analysis AS
    SELECT 
        p.fuzzer_id,
        e.program_hash,
        COUNT(*) as crash_count,
        MIN(e.created_at) as first_crash,
        MAX(e.created_at) as last_crash,
        STRING_AGG(DISTINCT e.mutator_type_id::TEXT, ',') as mutators_involved,
        MAX(e.coverage_total) as max_coverage_before_crash,
        BOOL_OR(e.is_new_edge) as found_new_edges
    FROM execution e
    JOIN program p ON e.program_hash = p.program_hash
    WHERE e.execution_outcome_id = 1
    GROUP BY p.fuzzer_id, e.program_hash;
    
    CREATE INDEX IF NOT EXISTS idx_crash_analysis_fuzzer ON crash_analysis(fuzzer_id);
    CREATE INDEX IF NOT EXISTS idx_crash_analysis_count ON crash_analysis(crash_count DESC);
    
    -- Materialized view: Program lineage (mutation tree)
    CREATE MATERIALIZED VIEW IF NOT EXISTS program_lineage AS
    WITH RECURSIVE lineage AS (
        SELECT 
            program_hash,
            fuzzer_id,
            parent_program_hash,
            source_mutator,
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
            p.source_mutator,
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
    
    CREATE INDEX IF NOT EXISTS idx_program_lineage_fuzzer ON program_lineage(fuzzer_id);
    CREATE INDEX IF NOT EXISTS idx_program_lineage_generation ON program_lineage(generation);
    
    -- View: Recent activity (not materialized, always current)
    CREATE OR REPLACE VIEW recent_activity AS
    SELECT 
        e.execution_id,
        e.program_hash,
        p.fuzzer_id,
        p.source_mutator,
        mt.name as mutator_name,
        eo.outcome,
        e.coverage_total,
        e.edges_found,
        e.is_new_edge,
        e.created_at
    FROM execution e
    JOIN program p ON e.program_hash = p.program_hash
    LEFT JOIN mutator_type mt ON e.mutator_type_id = mt.id
    JOIN execution_outcome eo ON e.execution_outcome_id = eo.id
    WHERE e.created_at > NOW() - INTERVAL '1 hour'
    ORDER BY e.created_at DESC;
    
    -- View: Top performing programs
    CREATE OR REPLACE VIEW top_performing_programs AS
    SELECT 
        p.program_hash,
        p.fuzzer_id,
        p.source_mutator,
        COUNT(e.execution_id) as execution_count,
        MAX(e.coverage_total) as max_coverage,
        AVG(e.coverage_total) as avg_coverage,
        COUNT(DISTINCT e.mutator_type_id) as mutators_spawned,
        COUNT(*) FILTER (WHERE e.is_new_edge = TRUE) as new_edges_found,
        MIN(e.created_at) as first_execution,
        MAX(e.created_at) as last_execution
    FROM program p
    LEFT JOIN execution e ON p.program_hash = e.program_hash
    GROUP BY p.program_hash, p.fuzzer_id, p.source_mutator
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
    
    CREATE OR REPLACE TRIGGER trigger_update_last_activity
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
    """
    
    /// Create all database tables and indexes
    public func createTables(connection: PostgresConnection) async throws {
        if enableLogging {
            logger.info("Creating database schema...")
        }
        
        do {
            let query = PostgresQuery(stringLiteral: DatabaseSchema.schemaSQL)
            _ = try await connection.query(query, logger: Logging.Logger(label: "DatabaseSchema"))
            if enableLogging {
                logger.info("Database schema created successfully")
                logger.info("Schema SQL length: \(DatabaseSchema.schemaSQL.count) characters")
            }
        } catch {
            logger.error("Failed to create database schema: \(error)")
            throw error
        }
    }
    
    /// Verify that all required tables exist
    public func verifySchema(connection: PostgresConnection) async throws -> Bool {
        if enableLogging {
            logger.info("Verifying database schema...")
        }
        
        let requiredTables = ["main", "fuzzer", "program", "mutator_type", "execution_outcome", "execution"]
        
        for table in requiredTables {
            let query: PostgresQuery = "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_schema = 'public' AND table_name = \(table))"
            let result = try await connection.query(query, logger: Logging.Logger(label: "DatabaseSchema"))
            
            var exists = false
            for try await row in result {
                exists = try row.decode(Bool.self, context: .default)
                break // We only need the first row
            }
            
            if !exists {
                logger.error("Required table \(table) does not exist")
                return false
            }
        }
        
        // Verify materialized views
        let requiredMatViews = ["fuzzer_dashboard", "mutator_effectiveness", "coverage_progression", "crash_analysis", "program_lineage", "recent_activity", "top_performing_programs"]
        
        for view in requiredMatViews {
            let query: PostgresQuery = "SELECT EXISTS (SELECT FROM pg_matviews WHERE schemaname = 'public' AND matviewname = \(view))"
            let result = try await connection.query(query, logger: Logging.Logger(label: "DatabaseSchema"))
            
            var exists = false
            for try await row in result {
                exists = try row.decode(Bool.self, context: .default)
                break
            }
            
            if !exists {
                logger.error("Required materialized view \(view) does not exist")
                return false
            }
        }
        
        if enableLogging {
            logger.info("Database schema verification successful")
        }
        return true
    }
}
