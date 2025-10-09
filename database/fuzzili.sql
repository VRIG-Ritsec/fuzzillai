CREATE TABLE main (
 fuzzer_id SERIAL PRIMARY KEY,
 created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE fuzzer (
 program_base64 TEXT PRIMARY KEY, -- Base64-encoded fuzzer program (unique identifier)
 fuzzer_id INT NOT NULL REFERENCES main(fuzzer_id) ON DELETE CASCADE, -- Links to parent fuzzer instance
 inserted_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE execution_type (
    id SERIAL PRIMARY KEY,
    title VARCHAR(32) NOT NULL UNIQUE
);

-- Program table: Stores generated test programs
CREATE TABLE program (
 program_base64 TEXT PRIMARY KEY, -- Base64-encoded test program (unique identifier)
 fuzzer_id INT NOT NULL REFERENCES main(fuzzer_id) ON DELETE CASCADE, -- Links to parent fuzzer instance
 created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE execution (
 execution_id SERIAL PRIMARY KEY, -- Unique identifier for each execution
 program_base64 TEXT NOT NULL REFERENCES program(program_base64) ON DELETE CASCADE, -- Links to the executed program
 execution_type_id INTEGER NOT NULL REFERENCES execution_type(id), -- Links to execution type
 feedback_vector JSONB, -- JSON structure containing execution feedback data
 turboshaft_ir TEXT, -- Turboshaft intermediate representation output
 coverage_total NUMERIC(5,2), -- Total code coverage percentage (0.00 to 999.99)
 created_at TIMESTAMP DEFAULT NOW(), -- Timestamp when execution occurred
 execution_flags TEXT[] -- Array of flags/options used during execution
);

ALTER TABLE program
ADD CONSTRAINT fk_program_fuzzer
FOREIGN KEY (program_base64)
REFERENCES fuzzer(program_base64);

INSERT INTO execution_type (title) VALUES 
 ('ai_mutation'),
 ('delta_analysis'),
 ('directed_testcases'),
 ('generalistic_testcases');

CREATE INDEX idx_execution_program ON execution(program_base64);
CREATE INDEX idx_execution_type ON execution(execution_type_id);
CREATE INDEX idx_execution_created ON execution(created_at);
CREATE INDEX idx_execution_coverage ON execution(coverage_total);