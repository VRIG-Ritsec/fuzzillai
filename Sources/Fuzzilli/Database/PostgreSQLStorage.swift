import Foundation
import PostgresNIO
import PostgresKit

/// PostgreSQL storage backend for Fuzzilli corpus and execution data
///
/// This class provides methods to store and retrieve programs, executions, crashes,
/// and metadata from PostgreSQL database. It handles the actual database operations
/// that the PostgreSQLCorpus uses for persistence and synchronization.
///
/// Note: This is a simplified implementation that logs operations instead of
/// performing actual database operations. The actual database integration will
/// be implemented when we have a working PostgreSQL setup.
public actor PostgreSQLStorage {
    
    // MARK: - Properties
    
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
    
    // MARK: - Initialization
    
    public init(databasePool: DatabasePool, enableLogging: Bool = false) {
        self.databasePool = databasePool
        self.enableLogging = enableLogging
        self.logger = Logging.Logger(label: "PostgreSQLStorage")
    }
    
    // MARK: - Helper Methods
    
    /// Create a direct connection using the database pool's configuration
    private func createDirectConnection() async throws -> PostgresConnection {
        guard let eventLoopGroup = databasePool.getEventLoopGroup() else {
            throw PostgreSQLStorageError.noResult
        }
        
        // Get the connection string and parse it
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
    
    // MARK: - Fuzzer Management
    
    /// Register a new fuzzer instance in the database
    public func registerFuzzer(name: String, engineType: String, hostname: String? = nil) async throws -> Int {
        if enableLogging {
            logger.info("Registering fuzzer: name=\(name), engineType=\(engineType), hostname=\(hostname ?? "none")")
        }
        
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
        
        // First, check if a fuzzer with this name already exists
        // Escape single quotes in name
        let escapedName = name.replacingOccurrences(of: "'", with: "''")
        let checkQuery = PostgresQuery(stringLiteral: "SELECT fuzzer_id, status FROM main WHERE fuzzer_name = '\(escapedName)'")
        let checkResult = try await connection.query(checkQuery, logger: self.logger)
        let checkRows = try await checkResult.collect()
        
        if let existingRow = checkRows.first {
            let existingFuzzerId = try existingRow.decode(Int.self, context: PostgresDecodingContext.default)
            let existingStatus = try existingRow.decode(String.self, context: PostgresDecodingContext.default)
            
            // Update status to active if it was inactive
            if existingStatus != "active" {
                let updateQuery: PostgresQuery = "UPDATE main SET status = 'active' WHERE fuzzer_id = \(existingFuzzerId)"
                try await connection.query(updateQuery, logger: self.logger)
                if enableLogging {
                    logger.info("Reactivated existing fuzzer: fuzzerId=\(existingFuzzerId)")
                }
            } else {
                if enableLogging {
                    logger.info("Reusing existing active fuzzer: fuzzerId=\(existingFuzzerId)")
                }
            }
            
            return existingFuzzerId
        }
        
        // If no existing fuzzer found, create a new one
        // Escape single quotes in engine type (name already escaped above)
        let escapedEngineType = engineType.replacingOccurrences(of: "'", with: "''")
        let insertQuery = PostgresQuery(stringLiteral: """
            INSERT INTO main (fuzzer_name, engine_type, status) 
            VALUES ('\(escapedName)', '\(escapedEngineType)', 'active') 
            RETURNING fuzzer_id
        """)
        
        if enableLogging {
            logger.info("Executing INSERT query to create new fuzzer")
        }
        
        let result: PostgresRowSequence
        do {
            if enableLogging {
                logger.info("Executing INSERT query: INSERT INTO main (fuzzer_name, engine_type, status) VALUES ('\(escapedName)', '\(escapedEngineType)', 'active') RETURNING fuzzer_id")
            }
            result = try await connection.query(insertQuery, logger: self.logger)
        } catch {
            if enableLogging {
                logger.error("INSERT query failed with error: \(error)")
            }
            throw error
        }
        
        let rows: [PostgresRow]
        do {
            rows = try await result.collect()
            if enableLogging {
                logger.info("INSERT query returned \(rows.count) rows")
            }
        } catch {
            if enableLogging {
                logger.error("Failed to collect rows from INSERT query: \(error)")
            }
            throw error
        }
        
        guard let row = rows.first else {
            if enableLogging {
                logger.error("INSERT query returned no rows - registration failed. This might indicate a connection issue or the query didn't execute properly.")
            }
            throw PostgreSQLStorageError.noResult
        }
        
        let fuzzerId = try row.decode(Int.self, context: PostgresDecodingContext.default)
        if enableLogging {
            self.logger.info("Created new fuzzer: fuzzerId=\(fuzzerId)")
        }
        return fuzzerId
    }
    
    // MARK: - Batching Methods
    
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

    /// Store multiple programs in batch for better performance
    public func storeProgramsBatch(programs: [Program], fuzzerId: Int) async throws -> [String] {
        guard !programs.isEmpty else { return [] }
        
        // Use direct connection to avoid connection pool deadlock
        let connection = try await createDirectConnection()
        defer { Task { _ = try? await connection.close() } }
        
        var programHashes: [String] = []
        var fuzzerBatchData: [(String, Int, Int, String)] = []
        
        // Prepare batch data
        for program in programs {
            let programHash = DatabaseUtils.calculateProgramHash(program: program)
            let programBase64 = DatabaseUtils.encodeProgramToBase64(program: program)
            programHashes.append(programHash)
            fuzzerBatchData.append((programHash, fuzzerId, program.size, programBase64))
        }
        
        // Batch insert into fuzzer table (corpus) using parameterized queries
        if !fuzzerBatchData.isEmpty {
            // Use a transaction for better performance
            try await connection.query("BEGIN", logger: self.logger)
            
            do {
                // Batch insert into fuzzer table
                for (programHash, fuzzerId, programSize, programBase64) in fuzzerBatchData {
                    // Escape single quotes in base64 string
                    let escapedProgramBase64 = programBase64.replacingOccurrences(of: "'", with: "''")
                    
                    let fuzzerQuery = PostgresQuery(stringLiteral: """
                        INSERT INTO fuzzer (program_hash, fuzzer_id, program_size, program_base64) 
                        VALUES ('\(programHash)', \(fuzzerId), \(programSize), '\(escapedProgramBase64)') 
                        ON CONFLICT (program_hash) DO UPDATE SET
                            fuzzer_id = EXCLUDED.fuzzer_id,
                            program_size = EXCLUDED.program_size,
                            program_base64 = EXCLUDED.program_base64
                    """)
                    try await connection.query(fuzzerQuery, logger: self.logger)
                }
                
                // Commit transaction
                try await connection.query("COMMIT", logger: self.logger)
                
                if enableLogging {
                    logger.info("Successfully batch stored \(programHashes.count) programs in database")
                }
            } catch {
                // Rollback on error
                _ = try? await connection.query("ROLLBACK", logger: self.logger)
                throw error
            }
        }
        
        return programHashes
    }
    
    // MARK: - Execution Management
    
    /// Store multiple executions in batch for better performance
    public func storeExecutionsBatch(executions: [ExecutionInput]) async throws -> [Int] {
        guard !executions.isEmpty else { return [] }
        
        // Use direct connection to avoid connection pool deadlock
        let connection = try await createDirectConnection()
        defer { Task { _ = try? await connection.close() } }
        
        var executionIds: [Int] = []
        var executionValues: [String] = []
        
        // Prepare batch data
        for execution in executions {
            // Map IDs to values for SQL
            let mutatorTypeValue = execution.mutatorTypeId != nil ? "\(execution.mutatorTypeId!)" : "NULL"
            let coverageTotalValue = execution.coverageTotal != nil ? "\(execution.coverageTotal!)" : "NULL"
            let edgesFoundValue = execution.edgesFound != nil ? "\(execution.edgesFound!)" : "NULL"
            let totalEdgesValue = execution.totalEdges != nil ? "\(execution.totalEdges!)" : "NULL"
            let isNewEdgeValue = execution.isNewEdge ? "TRUE" : "FALSE"
            let stdoutValue = execution.stdout != nil ? "'\(execution.stdout!.replacingOccurrences(of: "'", with: "''"))'" : "NULL"
            let stderrValue = execution.stderr != nil ? "'\(execution.stderr!.replacingOccurrences(of: "'", with: "''"))'" : "NULL"
            let fuzzoutValue = execution.fuzzout != nil ? "'\(execution.fuzzout!.replacingOccurrences(of: "'", with: "''"))'" : "NULL"
            let turbofanBitsValue = execution.turbofanOptimizationBits != nil ? "\(execution.turbofanOptimizationBits!)" : "NULL"
            let nexusCountValue = execution.feedbackNexusCount != nil ? "\(execution.feedbackNexusCount!)" : "NULL"
            
            executionValues.append("""
                ('\(execution.programHash)', \(mutatorTypeValue), \(execution.executionOutcomeId), \(coverageTotalValue), 
                \(edgesFoundValue), \(totalEdgesValue), \(isNewEdgeValue),
                \(stdoutValue), \(stderrValue), \(fuzzoutValue), 
                \(turbofanBitsValue), \(nexusCountValue), NOW())
            """)
        }
        
        // Batch insert executions
        if !executionValues.isEmpty {
            let queryString = """
                INSERT INTO execution (
                    program_hash, mutator_type_id, execution_outcome_id, coverage_total,
                    edges_found, total_edges, is_new_edge,
                    stdout, stderr, fuzzout,
                    turbofan_optimization_bits, feedback_nexus_count, created_at
                ) VALUES \(executionValues.joined(separator: ", ")) RETURNING execution_id
            """
            
            let query = PostgresQuery(stringLiteral: queryString)
            let result = try await connection.query(query, logger: self.logger)
            let rows = try await result.collect()
            
            for row in rows {
                let executionId = try row.decode(Int.self, context: PostgresDecodingContext.default)
                executionIds.append(executionId)
            }
        }
        
        return executionIds
    }
    
    /// Extract execution metadata from ExecutionOutcome
    private func extractExecutionMetadata(from outcome: ExecutionOutcome) -> (signalCode: Int?, exitCode: Int?) {
        switch outcome {
        case .crashed(let signal):
            return (signalCode: signal, exitCode: nil)
        case .failed(let exitCode):
            return (signalCode: nil, exitCode: exitCode)
        case .succeeded, .timedOut:
            return (signalCode: nil, exitCode: nil)
        }
    }
    
    // MARK: - Query Operations
    
    /// Fetch new programs from the database that were added after a certain time
    /// - Parameters:
    ///   - since: The timestamp to fetch programs from
    ///   - limit: Maximum number of programs to fetch
    /// - Returns: A list of (Program, ProgramHash) tuples
    public func fetchNewPrograms(since: Date, limit: Int = 100) async throws -> [(Program, String)] {
        if enableLogging {
            logger.info("Fetching new programs since: \(since)")
        }
        
        // Use direct connection to avoid connection pool deadlock
        let connection = try await createDirectConnection()
        defer { Task { _ = try? await connection.close() } }
        
        // Query for programs added after the specified time
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
        
        let result = try await connection.query(query, logger: self.logger)
        let rows = try await result.collect()
        
        var programs: [(Program, String)] = []
        
        for row in rows {
            do {
                let hash = try row.decode(String.self, context: PostgresDecodingContext.default)
                let base64 = try row.decode(String.self, context: PostgresDecodingContext.default)
                
                if let program = try? DatabaseUtils.decodeProgramFromBase64(base64: base64) {
                    programs.append((program, hash))
                }
            } catch {
                if enableLogging {
                    logger.warning("Failed to decode program from database: \(error)")
                }
            }
        }
        
        if enableLogging {
            logger.info("Fetched \(programs.count) new programs")
        }
        
        return programs
    }

    /// PostgreSQL storage errors
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

// MARK: - Coverage Tracking Methods

extension PostgreSQLStorage {
    /// Execute a simple query without expecting results
    public func executeQuery(_ query: PostgresQuery) async throws {
        // Use direct connection to avoid connection pool deadlock
        let connection = try await createDirectConnection()
        defer { Task { _ = try? await connection.close() } }
        
        try await connection.query(query, logger: self.logger)
    }
}