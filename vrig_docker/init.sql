-- Database schema for Fuzzilli Redis Stream Integration
-- This script initializes the PostgreSQL database with the proper schema

-- Main fuzzer instances table
CREATE TABLE IF NOT EXISTS main (
    fuzzer_id SERIAL PRIMARY KEY,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Fuzzer programs table (base programs used by fuzzers)
CREATE TABLE IF NOT EXISTS fuzzer (
    program_base64 TEXT PRIMARY KEY, -- Base64-encoded fuzzer program (unique identifier)
    fuzzer_id INT NOT NULL REFERENCES main(fuzzer_id) ON DELETE CASCADE, -- Links to parent fuzzer instance
    inserted_at TIMESTAMP DEFAULT NOW()
);

-- Execution types lookup table
CREATE TABLE IF NOT EXISTS execution_type (
    id SERIAL PRIMARY KEY,
    title VARCHAR(32) NOT NULL UNIQUE
);

-- Program table: Stores generated test programs
CREATE TABLE IF NOT EXISTS program (
    program_base64 TEXT PRIMARY KEY, -- Base64-encoded test program (unique identifier)
    fuzzer_id INT NOT NULL REFERENCES main(fuzzer_id) ON DELETE CASCADE, -- Links to parent fuzzer instance
    created_at TIMESTAMP DEFAULT NOW()
);

-- Execution table: Stores execution results and feedback
CREATE TABLE IF NOT EXISTS execution (
    execution_id SERIAL PRIMARY KEY, -- Unique identifier for each execution
    program_base64 TEXT NOT NULL REFERENCES program(program_base64) ON DELETE CASCADE, -- Links to the executed program
    execution_type_id INTEGER NOT NULL REFERENCES execution_type(id), -- Links to execution type
    feedback_vector JSONB, -- JSON structure containing execution feedback data
    turboshaft_ir TEXT, -- Turboshaft intermediate representation output
    coverage_total NUMERIC(5,2), -- Total code coverage percentage (0.00 to 999.99)
    created_at TIMESTAMP DEFAULT NOW(), -- Timestamp when execution occurred
    execution_flags TEXT[] -- Array of flags/options used during execution
);

-- Add foreign key constraint from program to fuzzer
ALTER TABLE program
ADD CONSTRAINT IF NOT EXISTS fk_program_fuzzer
FOREIGN KEY (program_base64)
REFERENCES fuzzer(program_base64);

-- Insert execution types
INSERT INTO execution_type (title) VALUES 
    ('agentic_analysis'),
    ('delta_analysis'),
    ('directed_testcases'),
    ('generalistic_testcases')
ON CONFLICT (title) DO NOTHING;

-- Indexes for performance optimization
CREATE INDEX IF NOT EXISTS idx_execution_program ON execution(program_base64);
CREATE INDEX IF NOT EXISTS idx_execution_type ON execution(execution_type_id);
CREATE INDEX IF NOT EXISTS idx_execution_created ON execution(created_at);
CREATE INDEX IF NOT EXISTS idx_execution_coverage ON execution(coverage_total);
CREATE INDEX IF NOT EXISTS idx_program_fuzzer_id ON program(fuzzer_id);
CREATE INDEX IF NOT EXISTS idx_fuzzer_fuzzer_id ON fuzzer(fuzzer_id);
CREATE INDEX IF NOT EXISTS idx_main_created ON main(created_at);

-- Create a view for easy querying of execution results with type names
CREATE OR REPLACE VIEW execution_summary AS
SELECT 
    e.execution_id,
    e.program_base64,
    p.fuzzer_id,
    et.title as execution_type,
    e.feedback_vector,
    e.turboshaft_ir,
    e.coverage_total,
    e.execution_flags,
    e.created_at
FROM execution e
JOIN program p ON e.program_base64 = p.program_base64
JOIN execution_type et ON e.execution_type_id = et.id;

-- Create a function to get or create fuzzer instance
CREATE OR REPLACE FUNCTION get_or_create_fuzzer()
RETURNS INTEGER AS $$
DECLARE
    fuzzer_id INTEGER;
BEGIN
    INSERT INTO main (created_at) VALUES (NOW()) RETURNING main.fuzzer_id INTO fuzzer_id;
    RETURN fuzzer_id;
END;
$$ LANGUAGE plpgsql;

-- Create a function to safely insert execution data
CREATE OR REPLACE FUNCTION insert_execution_safe(
    p_program_base64 TEXT,
    p_fuzzer_id INTEGER,
    p_execution_type_title TEXT,
    p_feedback_vector JSONB DEFAULT NULL,
    p_turboshaft_ir TEXT DEFAULT NULL,
    p_coverage_total NUMERIC DEFAULT 0,
    p_execution_flags TEXT[] DEFAULT '{}'
)
RETURNS INTEGER AS $$
DECLARE
    execution_type_id INTEGER;
    execution_id INTEGER;
BEGIN
    -- Get execution type ID
    SELECT id INTO execution_type_id FROM execution_type WHERE title = p_execution_type_title;
    
    IF execution_type_id IS NULL THEN
        RAISE EXCEPTION 'Unknown execution type: %', p_execution_type_title;
    END IF;
    
    -- Ensure program exists
    INSERT INTO program (program_base64, fuzzer_id, created_at)
    VALUES (p_program_base64, p_fuzzer_id, NOW())
    ON CONFLICT (program_base64) DO NOTHING;
    
    -- Insert execution record
    INSERT INTO execution (
        program_base64, 
        execution_type_id, 
        feedback_vector, 
        turboshaft_ir, 
        coverage_total, 
        execution_flags,
        created_at
    )
    VALUES (
        p_program_base64, 
        execution_type_id, 
        p_feedback_vector, 
        p_turboshaft_ir, 
        p_coverage_total,
        p_execution_flags,
        NOW()
    )
    RETURNING execution.execution_id INTO execution_id;
    
    RETURN execution_id;
END;
$$ LANGUAGE plpgsql;
