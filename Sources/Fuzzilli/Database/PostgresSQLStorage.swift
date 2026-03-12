import Foundation
import PostgresNIO
import PostgresKit

/// PostgreSQL storage backend for Fuzzilli corpus and execution data
///
/// This class provides methods to store and retrieve programs, executions, crashes,
/// and metadata from PostgreSQL database. It handles the actual database operations
/// that the PostgreSQLCorpus uses for persistence and synchronization.
public actor PostgresSQLStorage {
    public struct ProgramSyncRecord {
        public let program: Program
        public let hash: String
        public let insertedAt: Date

        public init(program: Program, hash: String, insertedAt: Date) {
            self.program = program
            self.hash = hash
            self.insertedAt = insertedAt
        }
    }

    private let databasePool: DatabasePool
    private let logger: Logging.Logger
    private let enableLogging: Bool
    
    // Batching
    private var pendingPrograms: [ProgramInput] = []
    private var pendingExecutions: [ExecutionInput] = []
    
    // Track program hashes we've already seen to avoid duplicates
    private var seenProgramHashes: Set<String> = []

    /// Input data for creating an execution record (before database insert assigns executionId)
    /// This mirrors ExecutionRecord from Models.swift but without the executionId field
    public struct ExecutionInput {
        public let programHash: String
        public let executionOutcomeId: Int
        public let coverageTotal: Double?
        public let edgesFound: Int?
        public let totalEdges: Int?
        public let isNewEdge: Bool
        public let stdout: String?
        public let stderr: String?
        public let fuzzout: String?
        public let turbofanOptimizationBits: Int64?
        public let feedbackNexusCount: Int?
        public let createdAt: Date
        
        public init(
            programHash: String, 
            executionOutcomeId: Int, 
            coverageTotal: Double?, 
            edgesFound: Int?, 
            totalEdges: Int?, 
            isNewEdge: Bool, 
            stdout: String?, 
            stderr: String?, 
            fuzzout: String?, 
            turbofanOptimizationBits: Int64?, 
            feedbackNexusCount: Int?, 
            createdAt: Date
        ) {
            self.programHash = programHash
            self.executionOutcomeId = executionOutcomeId
            self.coverageTotal = coverageTotal
            self.edgesFound = edgesFound
            self.totalEdges = totalEdges
            self.isNewEdge = isNewEdge
            self.stdout = stdout
            self.stderr = stderr
            self.fuzzout = fuzzout
            self.turbofanOptimizationBits = turbofanOptimizationBits
            self.feedbackNexusCount = feedbackNexusCount
            self.createdAt = createdAt
        }
    }

    public struct ProgramInput {
        public let program: Program
        public let fuzzerId: Int
        public let mutatorNames: [String]
        public let contributorNames: [String]

        public init(
            program: Program, 
            fuzzerId: Int, 
            mutatorNames: [String], 
            contributorNames: [String]
        ) {
            self.program = program
            self.fuzzerId = fuzzerId
            self.mutatorNames = mutatorNames
            self.contributorNames = contributorNames
        }
    }

    /// Input data for mutator statistics (per fuzzer instance)
    public struct MutatorStatsInput {
        public let fuzzerId: Int
        public let mutatorTypeId: Int
        public let totalSamples: Int
        public let crashesFound: Int
        public let timeouts: Int
        public let interestingSamples: Int
        public let invalidSamples: Int
        public let validSamples: Int
        public let totalInstructionsAdded: Int
        public let correctnessRate: Double?
        public let failureRate: Double?
        public let timeoutRate: Double?
        public let interestingSamplesRate: Double?
        public let avgInstructionsAdded: Double
        
        public init(
            fuzzerId: Int,
            mutatorTypeId: Int,
            totalSamples: Int,
            crashesFound: Int,
            timeouts: Int,
            interestingSamples: Int,
            invalidSamples: Int,
            validSamples: Int,
            totalInstructionsAdded: Int,
            correctnessRate: Double?,
            failureRate: Double?,
            timeoutRate: Double?,
            interestingSamplesRate: Double?,
            avgInstructionsAdded: Double
        ) {
            self.fuzzerId = fuzzerId
            self.mutatorTypeId = mutatorTypeId
            self.totalSamples = totalSamples
            self.crashesFound = crashesFound
            self.timeouts = timeouts
            self.interestingSamples = interestingSamples
            self.invalidSamples = invalidSamples
            self.validSamples = validSamples
            self.totalInstructionsAdded = totalInstructionsAdded
            self.correctnessRate = correctnessRate
            self.failureRate = failureRate
            self.timeoutRate = timeoutRate
            self.interestingSamplesRate = interestingSamplesRate
            self.avgInstructionsAdded = avgInstructionsAdded
        }
    }
    
    public init(databasePool: DatabasePool, enableLogging: Bool = false) {
        self.databasePool = databasePool
        self.enableLogging = enableLogging
        self.logger = Logging.Logger(label: "PostgresSQLStorage")
    }

    public func fetchLatestProgramSyncCursor() async throws -> (insertedAt: Date, hash: String)? {
        return try await databasePool.withConnection { connection in
            let query = PostgresQuery(stringLiteral: """
                SELECT inserted_at, program_hash
                FROM program
                ORDER BY inserted_at DESC, program_hash DESC
                LIMIT 1
            """)

            let result = try await connection.query(query, logger: self.logger)
            let rows = try await result.collect()

            guard let row = rows.first else {
                return nil
            }

            let (insertedAt, hash) = try row.decode((Date, String).self, context: .default)
            return (insertedAt, hash)
        }
    }
    
    /// Register a new fuzzer instance in the database or register worker with an existing fuzzer
    /// 
    /// This function implements atomic fuzzer registration using PostgreSQL's SELECT FOR UPDATE SKIP LOCKED
    /// to prevent race conditions when multiple workers start simultaneously. It will:
    /// 1. Try to claim an existing inactive fuzzer or a stale active fuzzer (no activity for 5+ minutes)
    /// 2. If no fuzzer is available, create a new one
    /// 3. Verify the fuzzer ID exists in the database before returning
    public func registerFuzzer(engineArguments: [String]? = nil) async throws -> Int {
        return try await databasePool.withConnection { connection in 
            do {
                try await connection.query("BEGIN", logger:self.logger)
                
                // Step 1: Try to claim an existing inactive or stale fuzzer
                // Use SELECT FOR UPDATE SKIP LOCKED to prevent race conditions
                // - FOR UPDATE: Lock the row for this transaction
                // - SKIP LOCKED: If row is locked by another worker, skip it and try next
                let claimQuery = PostgresQuery(stringLiteral: """
                    SELECT fuzzer_id FROM main
                    WHERE status = 'inactive' 
                       OR (status = 'active' AND last_activity < NOW() - INTERVAL '5 minutes')
                    ORDER BY fuzzer_id ASC
                    LIMIT 1
                    FOR UPDATE SKIP LOCKED
                """)
                
                let claimResult = try await connection.query(claimQuery, logger:self.logger)
                let claimRows = try await claimResult.collect()
                
                let fuzzerId: Int
                
                if let firstRow = claimRows.first {
                    // Found an existing fuzzer to reuse
                    fuzzerId = try firstRow.decode(Int.self, context: PostgresDecodingContext.default)
                    
                    if self.enableLogging {
                        self.logger.info("Claiming existing fuzzer_id: \(fuzzerId)")
                    }
                    
                    // Update the fuzzer to mark it as active
                    let updateQuery = PostgresQuery(stringLiteral: """
                        UPDATE main 
                        SET status = 'active', last_activity = NOW()
                        WHERE fuzzer_id = \(fuzzerId)
                    """)
                    try await connection.query(updateQuery, logger:self.logger)
                    
                } else {
                    // No inactive/stale fuzzer found, create a new one
                    if self.enableLogging {
                        self.logger.info("No inactive fuzzer found, creating new fuzzer")
                    }

                    let engineArgsValue: String
                    if let args = engineArguments, !args.isEmpty {
                        // Escape single quotes and format as PostgreSQL array: ARRAY['arg1', 'arg2', ...]
                        let escapedArgs = args.map { "'\($0.replacingOccurrences(of: "'", with: "''"))'" }
                        engineArgsValue = "ARRAY[\(escapedArgs.joined(separator: ", "))]"
                    } else {
                        engineArgsValue = "NULL"
                    }

                    let insertQuery = PostgresQuery(stringLiteral: """
                        INSERT INTO main (status, last_activity, engine_arguments) 
                        VALUES ('active', NOW(), \(engineArgsValue))
                        RETURNING fuzzer_id
                    """)
                    
                    let insertResult = try await connection.query(insertQuery, logger:self.logger)
                    let insertRows = try await insertResult.collect()
                    
                    guard let firstRow = insertRows.first else {
                        throw PostgresSQLStorageError.noResult
                    }
                    
                    fuzzerId = try firstRow.decode(Int.self, context: PostgresDecodingContext.default)
                    
                    if self.enableLogging {
                        self.logger.info("Created new fuzzer_id: \(fuzzerId)")
                    }
                }
                
                try await connection.query("COMMIT", logger:self.logger)
                
                // Step 2: Verify the fuzzer ID exists and is active in the database
                // This ensures the commit was successful and the ID is available
                // This may be slightly un-necessary but I want to be extra sure
                let verifyQuery = PostgresQuery(stringLiteral: """
                    SELECT fuzzer_id, status FROM main
                    WHERE fuzzer_id = \(fuzzerId) AND status = 'active'
                """)
                
                let verifyResult = try await connection.query(verifyQuery, logger:self.logger)
                let verifyRows = try await verifyResult.collect()
                
                guard !verifyRows.isEmpty else {
                    throw PostgresSQLStorageError.queryFailed("Fuzzer registration verification failed: fuzzer_id \(fuzzerId) not found or not active after commit")
                }
                
                if self.enableLogging {
                    self.logger.info("Successfully registered and verified fuzzer_id: \(fuzzerId)")
                }
                
                return fuzzerId
                
            } catch {
                _ = try? await connection.query("ROLLBACK", logger:self.logger)
                
                if self.enableLogging {
                    self.logger.error("Failed to register fuzzer: \(error)")
                }
                
                throw PostgresSQLStorageError.queryFailed("Fuzzer registration failed: \(error)")
            }
        }
    }
    
    
    /// Update fuzzer activity timestamp (heartbeat)
    /// 
    /// This should be called periodically (e.g., every 60 seconds) to indicate the worker is still alive.
    /// Prevents the fuzzer from being marked as stale and reclaimed by another worker.
    public func updateFuzzerActivity(fuzzerId: Int) async throws {
        return try await databasePool.withConnection { connection in 
            let query = PostgresQuery(stringLiteral: """
                UPDATE main 
                SET last_activity = NOW() 
                WHERE fuzzer_id = \(fuzzerId)
            """)
        
            try await connection.query(query, logger:self.logger)
        
            if self.enableLogging {
                self.logger.debug("Updated activity for fuzzer_id: \(fuzzerId)")
            }
        }
    }
    
    /// Deactivate fuzzer on graceful shutdown
    /// 
    /// Marks the fuzzer as inactive so it can be reused by another worker when the campaign resumes.
    /// Should be called during worker shutdown.
    public func deactivateFuzzer(fuzzerId: Int) async throws {
        return try await databasePool.withConnection { connection in 
            let query = PostgresQuery(stringLiteral: """
                UPDATE main 
                SET status = 'inactive' 
                WHERE fuzzer_id = \(fuzzerId)
            """)
        
            try await connection.query(query, logger:self.logger)
        
            if self.enableLogging {
                self.logger.info("Deactivated fuzzer_id: \(fuzzerId)")
            }
        }
    }

    public func updateMutatorStats(_ stats: [MutatorStatsInput]) async throws {
        guard !stats.isEmpty else { return }

        return try await databasePool.withConnection { connection in 
            try await connection.query("BEGIN", logger:self.logger)

            do {
                for stat in stats {
                    // Convert rates to percentages (0.0-1.0 -> 0.00-100.00)
                    // Use Optional<Double> to properly handle NULL values
                    let correctnessRateValue = stat.correctnessRate.map { $0 * 100 }
                    let failureRateValue = stat.failureRate.map { $0 * 100 }
                    let timeoutRateValue = stat.timeoutRate.map { $0 * 100 }
                    let interestingSamplesRateValue = stat.interestingSamplesRate.map { $0 * 100 }
                    
                    let query: PostgresQuery = """
                        INSERT INTO mutator_stats (
                            fuzzer_id, mutator_type_id, 
                            total_samples, crashes_found, timeouts, 
                            interesting_samples, invalid_samples, valid_samples,
                            total_instructions_added,
                            correctness_rate, failure_rate, timeout_rate,
                            interesting_samples_rate, avg_instructions_added,
                            last_updated
                        ) VALUES (
                            \(stat.fuzzerId), \(stat.mutatorTypeId),
                            \(stat.totalSamples), \(stat.crashesFound), \(stat.timeouts),
                            \(stat.interestingSamples), \(stat.invalidSamples), \(stat.validSamples),
                            \(stat.totalInstructionsAdded),
                            \(correctnessRateValue), \(failureRateValue), \(timeoutRateValue),
                            \(interestingSamplesRateValue), \(stat.avgInstructionsAdded),
                            NOW()
                        )
                        ON CONFLICT (fuzzer_id, mutator_type_id) 
                        DO UPDATE SET
                            total_samples = EXCLUDED.total_samples,
                            crashes_found = EXCLUDED.crashes_found,
                            timeouts = EXCLUDED.timeouts,
                            interesting_samples = EXCLUDED.interesting_samples,
                            invalid_samples = EXCLUDED.invalid_samples,
                            valid_samples = EXCLUDED.valid_samples,
                            total_instructions_added = EXCLUDED.total_instructions_added,
                            correctness_rate = EXCLUDED.correctness_rate,
                            failure_rate = EXCLUDED.failure_rate,
                            timeout_rate = EXCLUDED.timeout_rate,
                            interesting_samples_rate = EXCLUDED.interesting_samples_rate,
                            avg_instructions_added = EXCLUDED.avg_instructions_added,
                            last_updated = NOW()
                    """

                    try await connection.query(query, logger:self.logger)
                }

                try await connection.query("COMMIT", logger:self.logger)

                if self.enableLogging {
                    self.logger.info("Successfully updated \(stats.count) mutator statistics")
                }

            } catch {
                _ = try? await connection.query("ROLLBACK", logger:self.logger)

                if self.enableLogging {
                    self.logger.error("Failed to update mutator stats: \(String(reflecting: error))")
                }

                throw PostgresSQLStorageError.queryFailed("Mutator stats update failed: \(error)")
            }
        }
    }
    
    public func addProgramToBatch(_ programInput: ProgramInput) {
        guard let programHash = try? DatabaseUtils.calculateProgramHash(program: programInput.program) else {
            if self.enableLogging {
                self.logger.warning("Failed to calculate program hash, skipping program")
            }
            return
        }
        
        // Skip if we've already added this program to the batch
        guard !seenProgramHashes.contains(programHash) else {
            //if self.enableLogging {
            //    self.logger.verbose("Skipping duplicate program with hash: \(programHash)")
            //}
            return
        }
        
        seenProgramHashes.insert(programHash)
        pendingPrograms.append(programInput)
    }
    
    public func addExecutionToBatch(_ execution: ExecutionInput) {
        pendingExecutions.append(execution)
    }
    
    public func flushBatches() async throws {
        let programsToStore: [ProgramInput]
        let executionsToStore: [ExecutionInput]
        
        programsToStore = pendingPrograms
        executionsToStore = pendingExecutions
        pendingPrograms = []
        pendingExecutions = []
        
        // Cleanup seenProgramHashes if it grows too large (prevent unbounded memory growth)
        // Keep the most recent hashes by removing oldest entries when we exceed threshold
        if seenProgramHashes.count > 10000 {
            // Remove approximately 20% of the oldest hashes
            // Since Set doesn't maintain order, we'll just remove arbitrary elements
            // This is acceptable since we're just trying to prevent memory growth
            // TODO Aleksi: Could update seenProgramHashes track ordering to be able to remove the oldest entries 
            let countToRemove = seenProgramHashes.count / 5
            let hashesToRemove = Array(seenProgramHashes.prefix(countToRemove))
            for hash in hashesToRemove {
                seenProgramHashes.remove(hash)
            }
            
            if self.enableLogging {
                self.logger.info("Cleaned up seenProgramHashes: removed \(countToRemove) entries, \(seenProgramHashes.count) remaining")
            }
        }
        
        if !programsToStore.isEmpty {
            // Group by fuzzerId to use storeProgramsBatch
            let groupedPrograms = Dictionary(grouping: programsToStore, by: { $0.fuzzerId})
            for (fuzzerId, programInputs) in groupedPrograms {
                _ = try await storeProgramsBatch(programInputs: programInputs, fuzzerId: fuzzerId)
            }
        }
        
        if !executionsToStore.isEmpty {
            _ = try await storeExecutionsBatch(executions: executionsToStore)
        }
    }

    public func storeProgramsBatch(programInputs: [ProgramInput], fuzzerId: Int) async throws -> [String] {
        guard !programInputs.isEmpty else { return [] }

        return try await retryOnDeadlock {
            try await self._storeProgramsBatchImpl(programInputs: programInputs, fuzzerId: fuzzerId)
        }
    }
    
    private func _storeProgramsBatchImpl(programInputs: [ProgramInput], fuzzerId: Int) async throws -> [String] {
        // Pre-calculate all hashes and sort to ensure consistent lock ordering
        struct PreparedProgram {
            let hash: String
            let input: ProgramInput
            let parentHash: String?
            let programData: String
        }
        
        var preparedPrograms: [PreparedProgram] = []
        
        for programInput in programInputs {
            let program = programInput.program
            
            // Calculate program hash
            let programHash: String
            do {
                programHash = try DatabaseUtils.calculateProgramHash(program: program)
            } catch {
                if self.enableLogging {
                    self.logger.warning("Failed to calculate hash for program, skipping: \(error)")
                }
                continue
            }
            
            // Calculate parent hash if exists
            let parentHash: String?
            if let parentProgram = program.parent {
                do {
                    parentHash = try DatabaseUtils.calculateProgramHash(program: parentProgram)
                } catch {
                    if self.enableLogging {
                        self.logger.warning("Failed to calculate parent hash, using nil: \(error)")
                    }
                    parentHash = nil
                }
            } else {
                parentHash = nil
            }
            
            // Encode program (must be done after parent hash calculation)
            let programData: String
            do {
                programData = try DatabaseUtils.encodeProgramToBase64(program: program)
            } catch {
                if self.enableLogging {
                    self.logger.warning("Failed to encode program with hash \(programHash), skipping: \(error)")
                }
                continue
            }
            
            preparedPrograms.append(PreparedProgram(
                hash: programHash,
                input: programInput,
                parentHash: parentHash,
                programData: programData
            ))
        }
        
        // Sort by hash - ensures all workers acquire locks in the same order
        preparedPrograms.sort { $0.hash < $1.hash }
        
        return try await databasePool.withConnection { connection in
            var programHashes: [String] = []

            try await connection.query("BEGIN", logger:self.logger)

            do {
                var insertedCount = 0
                var skippedCount = 0
                
                for prepared in preparedPrograms {
                    let programHash = prepared.hash
                    let programInput = prepared.input
                    let mutatorNames = programInput.mutatorNames
                    let contributorNames = programInput.contributorNames

                    // Format as PostgreSQL arrays: ARRAY['name1', 'name2', ...]
                    let mutatorsArray: String
                    if !mutatorNames.isEmpty {
                        let escapedMutators = mutatorNames.map { "'\($0.replacingOccurrences(of: "'", with: "''"))'" }
                        mutatorsArray = "ARRAY[\(escapedMutators.joined(separator: ", "))]"
                    } else {
                        mutatorsArray = "NULL"
                    }

                    let contributorsArray: String
                    if !contributorNames.isEmpty {
                        let escapedContributors = contributorNames.map { "'\($0.replacingOccurrences(of: "'", with: "''"))'" }
                        contributorsArray = "ARRAY[\(escapedContributors.joined(separator: ", "))]"
                    } else {
                        contributorsArray = "NULL"
                    }

                    // Use ON CONFLICT DO NOTHING with RETURNING to detect if insert succeeded
                    // First worker to insert wins, others skip gracefully
                    // Single unified INSERT into the program table (previously split between fuzzer and program tables)
                    let programQuery = PostgresQuery(stringLiteral: """
                        INSERT INTO program (program_hash, fuzzer_id, inserted_at, program_base64, created_at, source_mutators, contributors, parent_program_hash) 
                        VALUES ('\(programHash)', \(fuzzerId), NOW(), '\(prepared.programData)', NOW(), \(mutatorsArray), \(contributorsArray), \(prepared.parentHash != nil ? "'\(prepared.parentHash!)'" : "NULL"))
                        ON CONFLICT (program_hash) DO NOTHING
                        RETURNING program_hash
                    """)

                    let programResult = try await connection.query(programQuery, logger:self.logger)
                    let programRows = try await programResult.collect()
                    
                    // If RETURNING gave us a row, the insert succeeded
                    // If no rows, it was skipped due to conflict
                    if !programRows.isEmpty {
                        programHashes.append(programHash)
                        insertedCount += 1
                    } else {
                        skippedCount += 1
                    }
                }

                try await connection.query("COMMIT", logger:self.logger)

                if self.enableLogging {
                    self.logger.info("Successfully batch stored \(insertedCount) programs in database (\(skippedCount) skipped as duplicates)")
                }

                return programHashes

            } catch {
                _ = try? await connection.query("ROLLBACK", logger:self.logger)

                if self.enableLogging {
                    self.logger.error("Failed to batch store programs: \(String(reflecting: error))")
                }
                throw error
            }
        }
    }

    public func storeExecutionsBatch(executions: [ExecutionInput]) async throws -> [Int] {
        guard !executions.isEmpty else { return [] }
    
        return try await retryOnDeadlock {
            try await self._storeExecutionsBatchImpl(executions: executions)
        }
    }
    
    private func _storeExecutionsBatchImpl(executions: [ExecutionInput]) async throws -> [Int] {
        return try await databasePool.withConnection { connection in
            var executionIds: [Int] = []
    
            try await connection.query("BEGIN", logger:self.logger)
    
            do {
                // Sort by program_hash for consistent lock ordering
                let sortedExecutions = executions.sorted { $0.programHash < $1.programHash }
                
                for execution in sortedExecutions {
                    let query: PostgresQuery = """
                        INSERT INTO execution (
                            program_hash, execution_outcome_id, coverage_total, 
                            edges_found, total_edges, is_new_edge, 
                            stdout, stderr, fuzzout, 
                            turbofan_optimization_bits, feedback_nexus_count, created_at
                        ) VALUES (
                            \(execution.programHash), \(execution.executionOutcomeId), \(execution.coverageTotal), 
                            \(execution.edgesFound), \(execution.totalEdges), \(execution.isNewEdge), 
                            \(execution.stdout), \(execution.stderr), \(execution.fuzzout), 
                            \(execution.turbofanOptimizationBits), \(execution.feedbackNexusCount), NOW()
                        ) RETURNING execution_id
                    """
    
                    let result = try await connection.query(query, logger:self.logger)

                    for row in try await result.collect() {
                        let id = try row.decode(Int.self) 
                        executionIds.append(id)
                    }
                }
    
                try await connection.query("COMMIT", logger:self.logger)
    
                if self.enableLogging {
                    self.logger.info("Successfully batch stored \(executionIds.count) executions")
                }
    
                return executionIds
    
            } catch {
                _ = try? await connection.query("ROLLBACK", logger:self.logger)
                if self.enableLogging {
                    self.logger.error("Failed to batch store executions: \(String(reflecting: error))")
                }
                throw error
            }
        }
    } 

    /// Retry wrapper for deadlock handling
    /// Detects PostgreSQL deadlock errors (40P01) and retries with exponential backoff
    private func retryOnDeadlock<T>(operation: @escaping () async throws -> T) async throws -> T {
        let maxRetries = 3
        let baseDelayMs: UInt64 = 100
        var lastError: Error?
        
        for attempt in 1...maxRetries {
            do {
                return try await operation()
            } catch {
                lastError = error
                
                let errorString = String(describing: error)
                let isDeadlock = errorString.contains("40P01") || 
                                errorString.contains("deadlock") ||
                                errorString.contains("could not serialize")
                
                if isDeadlock && attempt < maxRetries {
                    if self.enableLogging {
                        self.logger.warning("Deadlock detected (attempt \(attempt)/\(maxRetries)), retrying after delay...")
                    }
                    
                    // Exponential backoff with jitter to avoid thundering herd
                    let delayNs = baseDelayMs * UInt64(attempt) * 1_000_000
                    let jitter = UInt64.random(in: 0...(delayNs / 2))
                    try? await Task.sleep(nanoseconds: delayNs + jitter)
                    
                    continue
                } else {
                    throw error
                }
            }
        }
        
        throw lastError ?? PostgresSQLStorageError.queryFailed("Unknown error after retries")
    }

    /// Synchronize corpus from database after fuzzer registration
    /// 
    /// Fetches all programs from the fuzzer table using pagination to handle large corpora.
    /// This prevents memory spikes and connection timeouts for campaigns with 50k+ programs.
    public func syncCorpusFromDatabase() async throws -> [Program] {
        if self.enableLogging {
            self.logger.info("Syncing corpus from database...")
        }
        
        var allPrograms: [Program] = []
        var seenHashes = Set<String>()
        let batchSize = 5000  // Fetch 5k programs at a time
        var offset = 0
        var totalFetched = 0
        
        while true {
            let batch = try await fetchCorpusBatch(offset: offset, limit: batchSize)
            
            if batch.isEmpty {
                break
            }
            
            // Deduplicate and add to results
            for program in batch {
                do {
                    let hash = try DatabaseUtils.calculateProgramHash(program: program)
                    
                    if !seenHashes.contains(hash) {
                        allPrograms.append(program)
                        seenHashes.insert(hash)
                    }
                } catch {
                    if self.enableLogging {
                        self.logger.warning("Failed to calculate hash for program, skipping: \(error)")
                    }
                }
            }
            
            totalFetched += batch.count
            
            if self.enableLogging {
                self.logger.info("Corpus sync fetching progress: \(allPrograms.count) unique programs (\(totalFetched) total fetched)")
            }
            
            offset += batchSize
            
            // If we got fewer results than requested, we've reached the end
            if batch.count < batchSize {
                break
            }
        }
        
        if self.enableLogging {
            self.logger.info("Corpus sync fetching complete: \(allPrograms.count) unique programs from \(totalFetched) total")
        }
        
        return allPrograms
    }
    
    /// Fetch a batch of programs from the corpus (internal helper for pagination)
    private func fetchCorpusBatch(offset: Int, limit: Int) async throws -> [Program] {
        return try await databasePool.withConnection { connection in 
            let query = PostgresQuery(stringLiteral: """
                SELECT program_hash, program_base64 
                FROM program 
                ORDER BY inserted_at DESC
                LIMIT \(limit) OFFSET \(offset)
            """)
            
            let result = try await connection.query(query, logger:self.logger)
            let rows = try await result.collect()
            
            var programs: [Program] = []
            
            for row in rows {
                do {
                    let (_, data) = try row.decode((String, String).self, context: .default)
                    
                    if let program = try? DatabaseUtils.decodeProgramFromBase64(base64: data) {
                        programs.append(program)
                    }
                } catch {
                    if self.enableLogging {
                        self.logger.warning("Failed to decode row from database: \(error)")
                    }
                }
            }
            
            return programs
        }
    }

    /// Fetch new programs from the database that were added after a certain time
    /// - Parameters:
    ///   - since: The timestamp to fetch programs from
    ///   - limit: Maximum number of programs to fetch
    public func fetchNewPrograms(since insertedAt: Date, after hash: String, limit: Int = 100) async throws -> [ProgramSyncRecord] {
        if self.enableLogging {
            self.logger.info("Fetching new programs after cursor: \(insertedAt) / \(hash)")
        }
        
        return try await databasePool.withConnection { connection in 
            // Format the date for PostgreSQL
            let dateFormatter = ISO8601DateFormatter()
            dateFormatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
            let sinceString = dateFormatter.string(from: insertedAt)
            let escapedHash = hash.replacingOccurrences(of: "'", with: "''")
        
            let query = PostgresQuery(stringLiteral: """
                SELECT program_hash, program_base64, inserted_at
                FROM program 
                WHERE inserted_at > '\(sinceString)'
                   OR (inserted_at = '\(sinceString)' AND program_hash > '\(escapedHash)')
                ORDER BY inserted_at ASC, program_hash ASC
                LIMIT \(limit)
            """)
        
            if self.enableLogging {
                self.logger.info("Executing fetch query: \(query)")
            }

            let result = try await connection.query(query, logger:self.logger)
            let rows = try await result.collect()
        
            if self.enableLogging {
                self.logger.info("Fetch query returned \(rows.count) rows")
            }

            var programs: [ProgramSyncRecord] = []
        
            for row in rows {
                do {
                    let (hash, data, rowInsertedAt) = try row.decode((String, String, Date).self, context: .default)

                    if let program = try? DatabaseUtils.decodeProgramFromBase64(base64: data) {
                        programs.append(ProgramSyncRecord(program: program, hash: hash, insertedAt: rowInsertedAt))
                    } else if self.enableLogging {
                        self.logger.warning("Failed to decode program for hash: \(hash)")
                    }
                } catch {
                    if self.enableLogging {
                        self.logger.warning("Failed to decode row from database: \(error)")
                    }
                }
            }
        
            if self.enableLogging {
                self.logger.info("Fetched \(programs.count) new programs successfully decoded (from \(rows.count) rows)")
            }
        
            return programs
        }
    }

    public func refreshMaterializedViews() async throws {
        try await databasePool.withConnection { connection in
            do {
                let lockQuery = PostgresQuery(stringLiteral: "SELECT pg_try_advisory_lock(937451)")
                let lockResult = try await connection.query(lockQuery, logger: self.logger)
                let lockRows = try await lockResult.collect()
                let shouldRefresh = try lockRows.first?.decode(Bool.self, context: .default) ?? false

                guard shouldRefresh else {
                    if self.enableLogging {
                        self.logger.info("Skipping materialized view refresh because another worker holds the refresh lock")
                    }
                    return
                }

                let startTime = Date()
                
                // Call the PostgreSQL function that refreshes all materialized views
                let query = PostgresQuery(stringLiteral: "SELECT * FROM refresh_all_stats()")
                let result = try await connection.query(query, logger: self.logger)
                let rows = try await result.collect()
                
                // Calculate elapsed time in Swift
                let elapsedTime = Date().timeIntervalSince(startTime)
                
                if self.enableLogging {
                    self.logger.info("Refreshed \(rows.count) materialized views:")
                    for row in rows {
                        if let viewName = try? row.decode(String.self, context: .default) {
                            self.logger.info("  - \(viewName): \(String(format: "%.2f", elapsedTime))s")
                        }
                    }
                }

                let unlockQuery = PostgresQuery(stringLiteral: "SELECT pg_advisory_unlock(937451)")
                _ = try await connection.query(unlockQuery, logger: self.logger)
            } catch {
                let unlockQuery = PostgresQuery(stringLiteral: "SELECT pg_advisory_unlock(937451)")
                _ = try? await connection.query(unlockQuery, logger: self.logger)
                if self.enableLogging {
                    self.logger.error("Failed to refresh materialized views: \(String(reflecting: error))")
                }
                throw PostgresSQLStorageError.queryFailed("Materialized view refresh failed: \(error)")
            }
        }
    }

    public enum PostgresSQLStorageError: Error, LocalizedError {
        case noResult
        case invalidData
        case connectionFailed
        case queryFailed(String)
        
        public var errorDescription: String? {
            switch self {
            case .noResult:
                return "No result returned from database query"
            case .invalidData:
                return "Invalid data returned from database"
            case .connectionFailed:
                return "Failed to connect to database"
            case .queryFailed(let message):
                return "Database query failed: \(message)"
            }
        }
    }
}
