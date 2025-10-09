main=> CREATE TABLE main (
    fuzzer_id SERIAL PRIMARY KEY,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE fuzzer (
    program_base64 TEXT PRIMARY KEY,
    fuzzer_id INT NOT NULL REFERENCES main(fuzzer_id) ON DELETE CASCADE,
    inserted_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE program (
    program_base64 TEXT PRIMARY KEY,
    fuzzer_id INT NOT NULL REFERENCES main(fuzzer_id) ON DELETE CASCADE,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE execution (
    execution_id SERIAL PRIMARY KEY,
    program_base64 TEXT NOT NULL REFERENCES program(fuzzer_id) ON DELETE CASCADE,
    feedback_vector JSONB,
    turboshaft_ir TEXT,
    coverage_total NUMERIC(5,2),
    created_at TIMESTAMP DEFAULT NOW(),
    execution_flags TEXT[]
);


ALTER TABLE program
ADD CONSTRAINT fk_program_fuzzer
FOREIGN KEY (program_base64)
REFERENCES fuzzer(program_base64)