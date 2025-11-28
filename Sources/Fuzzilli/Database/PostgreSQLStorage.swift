import Foundation
import PostgresNIO
import PostgresKit

/// PostgreSQL storage backend for Fuzzilli corpus and execution data
///
/// This class provides methods to store and retrieve programs, executions, crashes,
/// and metadata from PostgreSQL database. It handles the actual database operations
/// that the PostgreSQLCorpus uses for persistence and synchronization.
public actor PostgreSQLStorage {

    private let databasePool: DatabasePool
    private let logger: Logging.Logger
    private let enableLogging: Bool
    
    // Batching
    private var pendingPrograms: [(Program, Int)] = []
    private var pendingExecutions: [ExecutionInput] = []

    
    /// Input data for creating an execution record (before database insert assigns executionId)
    /// This mirrors ExecutionRecord from Models.swift but without the executionId field
    public struct ExecutionInput {
        public let programHash: String
        public let mutatorTypeId: Int?
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
        
        public init(programHash: String, mutatorTypeId: Int?, executionOutcomeId: Int, coverageTotal: Double?, edgesFound: Int?, totalEdges: Int?, isNewEdge: Bool, stdout: String?, stderr: String?, fuzzout: String?, turbofanOptimizationBits: Int64?, feedbackNexusCount: Int?, createdAt: Date) {
            self.programHash = programHash
            self.mutatorTypeId = mutatorTypeId
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
    
    public init(databasePool: DatabasePool, enableLogging: Bool = false) {
        self.databasePool = databasePool
        self.enableLogging = enableLogging
        self.logger = Logging.Logger(label: "PostgreSQLStorage")
    }
    
    private func createDirectConnection() async throws -> PostgresConnection {
        guard let eventLoopGroup = databasePool.getEventLoopGroup() else {
            throw PostgreSQLStorageError.noResult
        }
        
        let connectionString = databasePool.getConnectionString()
        guard let url = URL(string: connectionString) else {
            throw PostgreSQLStorageError.connectionFailed
        }
        
        guard url.scheme == "postgresql" || url.scheme == "postgres" else {
            throw PostgreSQLStorageError.connectionFailed
        }
        
        let host = url.host ?? "localhost"
        let port = url.port ?? 5432
        let username = url.user ?? "postgres"
        let password = url.password ?? ""
        let database = url.path.isEmpty ? nil : String(url.path.dropFirst()) // Remove leading slash
        
        if enableLogging {
            logger.info("Creating direct connection to: host=\(host), port=\(port), database=\(database ?? "none")")
        }
        
        return try await PostgresConnection.connect(
            on: eventLoopGroup.next(),
            configuration: PostgresConnection.Configuration(
                host: host,
                port: port,
                username: username,
                password: password,
                database: database,
                tls: .disable // For now, disable TLS
            ),
            id: 0,
            logger: logger
        )
    }

    /// Register a new fuzzer instance in the database or register worker with an existing fuzzer
    /// 
    /// This function implements atomic fuzzer registration using PostgreSQL's SELECT FOR UPDATE SKIP LOCKED
    /// to prevent race conditions when multiple workers start simultaneously. It will:
    /// 1. Try to claim an existing inactive fuzzer or a stale active fuzzer (no activity for 5+ minutes)
    /// 2. If no fuzzer is available, create a new one
    public func registerFuzzer(engineArguments: [String]? = nil) async throws -> Int {
        // Use direct connection to avoid connection pool deadlock
        let connection: PostgresConnection
        do {
            connection = try await createDirectConnection()
            if enableLogging {
                let connString = databasePool.getConnectionString()
                logger.info("Created direct connection to: \(connString)")
            }
        } catch {
            if enableLogging {
                logger.error("Failed to create direct connection: \(error)")
            }
            throw error
        }
        defer { Task { _ = try? await connection.close() } }
        
        do {
            try await connection.query("BEGIN", logger: self.logger)
            
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
            
            let claimResult = try await connection.query(claimQuery, logger: self.logger)
            let claimRows = try await claimResult.collect()
            
            let fuzzerId: Int
            
            if let firstRow = claimRows.first {
                // Found an existing fuzzer to reuse
                fuzzerId = try firstRow.decode(Int.self, context: PostgresDecodingContext.default)
                
                if enableLogging {
                    logger.info("Claiming existing fuzzer_id: \(fuzzerId)")
                }
                
                // Update the fuzzer to mark it as active
                let updateQuery = PostgresQuery(stringLiteral: """
                    UPDATE main 
                    SET status = 'active', last_activity = NOW()
                    WHERE fuzzer_id = \(fuzzerId)
                """)
                try await connection.query(updateQuery, logger: self.logger)
                
            } else {
                // No inactive/stale fuzzer found, create a new one
                if enableLogging {
                    logger.info("No inactive fuzzer found, creating new fuzzer")
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
                
                let insertResult = try await connection.query(insertQuery, logger: self.logger)
                let insertRows = try await insertResult.collect()
                
                guard let firstRow = insertRows.first else {
                    throw PostgreSQLStorageError.noResult
                }
                
                fuzzerId = try firstRow.decode(Int.self, context: PostgresDecodingContext.default)
                
                if enableLogging {
                    logger.info("Created new fuzzer_id: \(fuzzerId)")
                }
            }
            
            try await connection.query("COMMIT", logger: self.logger)
            
            if enableLogging {
                logger.info("Successfully registered with fuzzer_id: \(fuzzerId)")
            }
            
            return fuzzerId
            
        } catch {
            _ = try? await connection.query("ROLLBACK", logger: self.logger)
            
            if enableLogging {
                logger.error("Failed to register fuzzer: \(error)")
            }
            
            throw PostgreSQLStorageError.queryFailed("Fuzzer registration failed: \(error)")
        }
    }
    
    
    /// Update fuzzer activity timestamp (heartbeat)
    /// 
    /// This should be called periodically (e.g., every 60 seconds) to indicate the worker is still alive.
    /// Prevents the fuzzer from being marked as stale and reclaimed by another worker.
    public func updateFuzzerActivity(fuzzerId: Int) async throws {
        let connection = try await createDirectConnection()
        defer { Task { _ = try? await connection.close() } }
        
        let query = PostgresQuery(stringLiteral: """
            UPDATE main 
            SET last_activity = NOW() 
            WHERE fuzzer_id = \(fuzzerId)
        """)
        
        try await connection.query(query, logger: self.logger)
        
        if enableLogging {
            logger.debug("Updated activity for fuzzer_id: \(fuzzerId)")
        }
    }
    
    /// Deactivate fuzzer on graceful shutdown
    /// 
    /// Marks the fuzzer as inactive so it can be reused by another worker when the campaign resumes.
    /// Should be called during worker shutdown.
    public func deactivateFuzzer(fuzzerId: Int) async throws {
        let connection = try await createDirectConnection()
        defer { Task { _ = try? await connection.close() } }
        
        let query = PostgresQuery(stringLiteral: """
            UPDATE main 
            SET status = 'inactive' 
            WHERE fuzzer_id = \(fuzzerId)
        """)
        
        try await connection.query(query, logger: self.logger)
        
        if enableLogging {
            logger.info("Deactivated fuzzer_id: \(fuzzerId)")
        }
    }
    
    public func addProgramToBatch(_ program: Program, fuzzerId: Int) {
        pendingPrograms.append((program, fuzzerId))
    }
    
    public func addExecutionToBatch(_ execution: ExecutionInput) {
        pendingExecutions.append(execution)
    }
    
    public func flushBatches() async throws {
        let programsToStore: [(Program, Int)]
        let executionsToStore: [ExecutionInput]
        
        programsToStore = pendingPrograms
        executionsToStore = pendingExecutions
        pendingPrograms = []
        pendingExecutions = []
        
        if !programsToStore.isEmpty {
            // Group by fuzzerId to use storeProgramsBatch
            let groupedPrograms = Dictionary(grouping: programsToStore, by: { $0.1 })
            for (fuzzerId, items) in groupedPrograms {
                let programs = items.map { $0.0 }
                _ = try await storeProgramsBatch(programs: programs, fuzzerId: fuzzerId)
            }
        }
        
        if !executionsToStore.isEmpty {
            _ = try await storeExecutionsBatch(executions: executionsToStore)
        }
    }

    // TODO Aleksi: Verify this actually works :)
    public func storeProgramsBatch(programs: [Program], fuzzerId: Int) async throws -> [String] {
        guard !programs.isEmpty else { return [] }

        let connection = try await createDirectConnection()
        defer { Task { _ = try? await connection.close() } }

        var programHashes: [String] = []

        try await connection.query("BEGIN", logger: self.logger)

        do {
            for program in programs {
                let programHash = DatabaseUtils.calculateProgramHash(program: program)

                let programData = DatabaseUtils.encodeProgramToBase64(program: program)

                //logger.info("Storing program: \(programHash) with data: \(programData)")

                let mutatorName = program.contributors.first(where: { $0.name.contains("Mutator") })?.name

                let parentHash: String?
                if let parentProgram = program.parent {
                    parentHash = DatabaseUtils.calculateProgramHash(program: parentProgram)
                } else {
                    parentHash = nil
                }

                let fuzzerQuery: PostgresQuery = """
                    INSERT INTO fuzzer (program_hash, fuzzer_id, inserted_at, program_base64) 
                    VALUES (\(programHash), \(fuzzerId), NOW(), \(programData))
                    ON CONFLICT (program_hash) DO UPDATE SET 
                        fuzzer_id = EXCLUDED.fuzzer_id,
                        program_base64 = EXCLUDED.program_base64,
                        inserted_at = EXCLUDED.inserted_at
                """

                try await connection.query(fuzzerQuery, logger: self.logger)

                let programQuery: PostgresQuery = """
                    INSERT INTO program (program_hash, fuzzer_id, created_at, source_mutator, parent_program_hash) 
                    VALUES (\(programHash), \(fuzzerId), NOW(), \(mutatorName), \(parentHash))
                    ON CONFLICT (program_hash) DO NOTHING
                """

                try await connection.query(programQuery, logger: self.logger)

                programHashes.append(programHash)
            }

            try await connection.query("COMMIT", logger: self.logger)

            if enableLogging {
                logger.info("Successfully batch stored \(programHashes.count) programs in database")
            }

            return programHashes

        } catch {
            _ = try? await connection.query("ROLLBACK", logger: self.logger)

            if enableLogging {
                logger.error("Failed to batch store programs: \(String(reflecting: error))")
            }
            throw error
        }
    }

    // TODO Aleksi: Verify this actually works :)
    public func storeExecutionsBatch(executions: [ExecutionInput]) async throws -> [Int] {
        guard !executions.isEmpty else { return [] }
    
        let connection = try await createDirectConnection()
        defer { Task { _ = try? await connection.close() } }
    
        var executionIds: [Int] = []
    
        try await connection.query("BEGIN", logger: self.logger)
    
        do {
            for execution in executions {
                let query: PostgresQuery = """
                    INSERT INTO execution (
                        program_hash, mutator_type_id, execution_outcome_id, coverage_total, 
                        edges_found, total_edges, is_new_edge, 
                        stdout, stderr, fuzzout, 
                        turbofan_optimization_bits, feedback_nexus_count, created_at
                    ) VALUES (
                        \(execution.programHash), \(execution.mutatorTypeId), \(execution.executionOutcomeId), \(execution.coverageTotal), 
                        \(execution.edgesFound), \(execution.totalEdges), \(execution.isNewEdge), 
                        \(execution.stdout), \(execution.stderr), \(execution.fuzzout), 
                        \(execution.turbofanOptimizationBits), \(execution.feedbackNexusCount), NOW()
                    ) RETURNING execution_id
                """
    
                let result = try await connection.query(query, logger: self.logger)
                
                for row in try await result.collect() {
                    let id = try row.decode(Int.self) 
                    executionIds.append(id)
                }
            }
    
            try await connection.query("COMMIT", logger: self.logger)
    
            if enableLogging {
                logger.info("Successfully batch stored \(executionIds.count) executions")
            }
    
            return executionIds
    
        } catch {
            _ = try? await connection.query("ROLLBACK", logger: self.logger)
            if enableLogging {
                logger.error("Failed to batch store executions: \(String(reflecting: error))")
            }
            throw error
        }
    } 

    /// Synchronize corpus from database after fuzzer registration
    /// 
    /// Fetches all programs from the fuzzer table and returns them for corpus initialization.
    /// This ensures the worker starts with the current shared corpus state.
    public func syncCorpusFromDatabase() async throws -> [Program] {
        if enableLogging {
            logger.info("Syncing corpus from database...")
        }
        
        let connection = try await createDirectConnection()
        defer { Task { _ = try? await connection.close() } }
        
        let query = PostgresQuery(stringLiteral: """
            SELECT program_hash, program_base64 
            FROM fuzzer 
            ORDER BY inserted_at DESC
        """)
        
        if enableLogging {
            logger.info("Executing sync query: \(query)")
        }

        let result = try await connection.query(query, logger: self.logger)
        let rows = try await result.collect()
        
        if enableLogging {
            logger.info("Sync query returned \(rows.count) rows")
        }

        var programs: [Program] = []
        var seenHashes = Set<String>() 

        for row in rows {
            do {
                let (hash, data) = try row.decode((String, String).self, context: .default)

                guard !seenHashes.contains(hash) else { 
                    if enableLogging {
                        logger.debug("Skipping duplicate hash: \(hash)")
                    }
                    continue 
                }
                
                if let program = try? DatabaseUtils.decodeProgramFromBase64(base64: data) {
                    programs.append(program)
                    seenHashes.insert(hash)
                } else if enableLogging {
                    logger.warning("Failed to decode program for hash: \(hash)")
                }
            } catch {
                if enableLogging {
                    logger.warning("Failed to decode row from database: \(error)")
                }
            }
        }
        
        if enableLogging {
            logger.info("Synced \(programs.count) programs from database (unique)")
        }
        
        return programs
    }

    /// Fetch new programs from the database that were added after a certain time
    /// - Parameters:
    ///   - since: The timestamp to fetch programs from
    ///   - limit: Maximum number of programs to fetch
    public func fetchNewPrograms(since: Date, limit: Int = 100) async throws -> [(Program, String)] {
        if enableLogging {
            logger.info("Fetching new programs since: \(since)")
        }
        
        let connection = try await createDirectConnection()
        defer { Task { _ = try? await connection.close() } }
        
        // Format the date for PostgreSQL
        let dateFormatter = ISO8601DateFormatter()
        dateFormatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let sinceString = dateFormatter.string(from: since)
        
        let query = PostgresQuery(stringLiteral: """
            SELECT program_hash, program_base64 
            FROM fuzzer 
            WHERE inserted_at > '\(sinceString)'
            ORDER BY inserted_at ASC
            LIMIT \(limit)
        """)
        
        if enableLogging {
            logger.info("Executing fetch query: \(query)")
        }

        let result = try await connection.query(query, logger: self.logger)
        let rows = try await result.collect()
        
        if enableLogging {
            logger.info("Fetch query returned \(rows.count) rows")
        }

        var programs: [(Program, String)] = []
        
        for row in rows {
            do {
                let (hash, data) = try row.decode((String, String).self, context: .default)

                //logger.info("FOUND data: \(data)")
                //logger.info("FOUND hash: \(hash)")
                
                if let program = try? DatabaseUtils.decodeProgramFromBase64(base64: data) {
                    programs.append((program, hash))
                } else if enableLogging {
                    logger.warning("Failed to decode program for hash: \(hash)")
                }
            } catch {
                if enableLogging {
                    logger.warning("Failed to decode row from database: \(error)")
                }
            }
        }
        
        if enableLogging {
            logger.info("Fetched \(programs.count) new programs successfully decoded")
        }
        
        return programs
    }

    public enum PostgreSQLStorageError: Error, LocalizedError {
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