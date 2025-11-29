import Foundation

public class PostgreSQLSync: Module {
    private let storage: PostgreSQLStorage
    private let fuzzerInstanceId: String
    private let enableLogging: Bool
    private var lastSyncTime: Date
    
    private let logger = Logger(withLabel: "PostgreSQLSync")
    
    private var cachedFuzzerId: Int?
    private var covEvaluator: ProgramCoverageEvaluator?  // Cache coverage evaluator for metrics
    
    // Cache for execution outputs to avoid race conditions with REPRL
    // Key: program ID (UUID string), Value: execution outputs
    private var executionCache: [String: (stdout: String, stderr: String, fuzzout: String)] = [:]
    private let maxCacheSize = 1000 

    private var mutatorCache: [String: String] = [:]

    public init(storage: PostgreSQLStorage, fuzzerInstanceId: String, enableLogging: Bool = false) {
        self.storage = storage
        self.fuzzerInstanceId = fuzzerInstanceId
        self.enableLogging = enableLogging
        self.lastSyncTime = Date()
    }
    
    public func initialize(with fuzzer: Fuzzer) {
        // Try to downcast evaluator to ProgramCoverageEvaluator for coverage metrics
        if let coverageEvaluator = fuzzer.evaluator as? ProgramCoverageEvaluator {
            self.covEvaluator = coverageEvaluator
            if enableLogging {
                logger.info("Coverage evaluator detected - will track coverage metrics")
            }
        } else {
            if enableLogging {
                logger.warning("Evaluator is not ProgramCoverageEvaluator - coverage metrics will not be available")
            }
        }
        
        // Register fuzzer and sync corpus
        Task {
            do {
                // Step 1: Register this worker with the database
                let engineArgs = fuzzer.config.arguments
                self.cachedFuzzerId = try await storage.registerFuzzer(engineArguments: engineArgs)
                if enableLogging {
                    logger.info("Fuzzer registered with PostgreSQL database: fuzzerId \(self.cachedFuzzerId ?? -1)")
                }
                
                // Step 2: Sync corpus from database to in-memory basicCorpus
                let programs = try await storage.syncCorpusFromDatabase()
                logger.info("Found: \(programs.count) programs from db")
                if enableLogging {
                    logger.info("Syncing \(programs.count) programs from database to corpus")
                }
                
                // Import each program into the fuzzer's corpus
                for program in programs {
                    fuzzer.async {
                        fuzzer.importProgram(program, origin: .corpusImport(mode: .full), enableDropout: false)
                    }
                }
                
                if enableLogging {
                    logger.info("Corpus synchronization complete: imported \(programs.count) programs")
                }
            } catch {
                logger.error("Failed to register fuzzer with PostgreSQL database: \(String(reflecting: error))")
            }
        }
        
        // Track program ID for each execution to correlate with outputs
        var currentExecutingProgramId: String? = nil
        fuzzer.registerEventListener(for: fuzzer.events.PreExecute) { (program, _) in
            currentExecutingProgramId = program.id.uuidString
        }
        
        // Capture execution outputs immediately after execution completes
        fuzzer.registerEventListener(for: fuzzer.events.PostExecute) { execution in
            guard let programId = currentExecutingProgramId else { return }
            
            // Cache the outputs while they're still valid (before next REPRL execution)
            self.executionCache[programId] = (
                stdout: execution.stdout,
                stderr: execution.stderr,
                fuzzout: execution.fuzzout
            )
            
            // Implement simple LRU-style cleanup to prevent unbounded growth
            if self.executionCache.count > self.maxCacheSize {
                // Remove oldest entries (first 100 entries)
                let keysToRemove = Array(self.executionCache.keys.prefix(100))
                for key in keysToRemove {
                    self.executionCache.removeValue(forKey: key)
                }
            }
        }

        // Cache mutator names from ProgramGenerated event (before minimization)
        // This works around the fact that contributors don't survive protobuf serialization
        fuzzer.registerEventListener(for: fuzzer.events.ProgramGenerated) { program in
            let programId = program.id.uuidString
            
            self.logger.verbose("[ProgramGenerated] Contributors: \(program.contributors.map({ $0.name }).joined(separator: ", "))")

            
            // Extract ALL mutator names from contributors
            let mutators = program.contributors.filter { $0.name.contains("Mutator") }
            if !mutators.isEmpty {
                let mutatorNames = mutators.map { $0.name }.joined(separator: ", ")
                self.logger.verbose("[ProgramGenerated] Found mutators: \(mutatorNames)")
                
                // Cache the first mutator name (or we could cache all of them)
                self.mutatorCache[programId] = mutators.first!.name
                
                // Implement LRU-style cleanup
                if self.mutatorCache.count > self.maxCacheSize {
                    let keysToRemove = Array(self.mutatorCache.keys.prefix(100))
                    for key in keysToRemove {
                        self.mutatorCache.removeValue(forKey: key)
                    }
                }
            }
        }
        
        fuzzer.registerEventListener(for: fuzzer.events.InterestingProgramFound) { ev in
            let program = ev.program
            let aspects = ev.aspects
            let execution = ev.execution
            
            // Retrieve cached execution outputs using program ID
            // If not found (e.g., corpus import), use empty strings
            let programId = program.id.uuidString
            let (stdout, stderr, fuzzout) = self.executionCache[programId] ?? ("", "", "")
            
            // Clean up the cache entry immediately after use
            self.executionCache.removeValue(forKey: programId)
            
            Task {
                guard let fuzzerId = self.cachedFuzzerId else {
                    self.logger.error("Fuzzer ID not set - registration may have failed")
                    return
                }
                
                await self.storage.addProgramToBatch(program, fuzzerId: fuzzerId)
                    
                if let execution = execution {
                    let outcomeId = DatabaseUtils.mapExecutionOutcome(outcome: execution.outcome)

                    // Try to get mutator name from cache first (for locally generated programs)
                    // Fall back to contributors (though they may be empty for imported programs)
                    //self.logger.info("[InterestingProgramFound] Contributors: \(program.contributors.map({ $0.name }).joined(separator: ", "))")
                    let mutatorName = self.mutatorCache[programId] ?? program.contributors.first(where: { contributor in
                        contributor.name.contains("Mutator")
                    })?.name
                    self.mutatorCache.removeValue(forKey: programId)
                    
                    let mutatorTypeId = mutatorName != nil ? DatabaseUtils.mapMutatorNameToId(mutatorName!) : nil
                    
                    // Determine if new edges were found
                    // Check if aspects is a CovEdgeSet to distinguish between:
                    // - New coverage edges (CovEdgeSet with count > 0)
                    // - Feedback/optimization delta only (basic ProgramAspects)
                    let isNewEdge: Bool
                    if let covEdgeSet = aspects as? CovEdgeSet {
                        isNewEdge = covEdgeSet.count > 0
                    } else {
                        isNewEdge = false
                    }

                    var coverageTotal: Double? = nil
                    var edgesFound: Int? = nil
                    var totalEdges: Int? = nil
                    var turbofanOptimizationBits: Int64? = nil
                    var feedbackNexusCount: Int? = nil
                    
                    if let evaluator = self.covEvaluator {
                        let totalEdgesCount = evaluator.getTotalEdgesCount()
                        totalEdges = Int(totalEdgesCount)
                        
                        let foundEdgesCount = evaluator.getFoundEdgesCount()
                        
                        if totalEdgesCount > 0 {
                            coverageTotal = (Double(foundEdgesCount) / Double(totalEdgesCount)) * 100
                        }
                        
                        if let covEdgeSet = aspects as? CovEdgeSet {
                            edgesFound = Int(covEdgeSet.count)
                        } else {
                            edgesFound = 0
                        }
                        
                        turbofanOptimizationBits = Int64(evaluator.getTurbofanOptimizationBits())
                        feedbackNexusCount = Int(evaluator.getFeedbackNexusCount())
                    }
                    
                    let programHash = DatabaseUtils.calculateProgramHash(program: program)
                    let executionInput = PostgreSQLStorage.ExecutionInput(
                        programHash: programHash,
                        mutatorTypeId: mutatorTypeId,
                        executionOutcomeId: outcomeId,
                        coverageTotal: coverageTotal,
                        edgesFound: edgesFound,
                        totalEdges: totalEdges,
                        isNewEdge: isNewEdge,
                        stdout: stdout,
                        stderr: stderr,
                        fuzzout: fuzzout,
                        turbofanOptimizationBits: turbofanOptimizationBits,
                        feedbackNexusCount: feedbackNexusCount,
                        createdAt: Date()
                    )
                    await self.storage.addExecutionToBatch(executionInput)
                    
                    if self.enableLogging {
                        let mutatorInfo = mutatorName.map { " (mutator: \($0))" } ?? ""
                        let edgeInfo = isNewEdge ? " with new edges" : " (feedback/optimization delta only)"
                        self.logger.verbose("Added interesting program and execution to batch\(mutatorInfo)\(edgeInfo)")
                    }
                }
            }
        }
        
        fuzzer.registerEventListener(for: fuzzer.events.CrashFound) { ev in
            let program = ev.program
            let behaviour = ev.behaviour  // .deterministic or .flaky
            let isUnique = ev.isUnique
            
            // Retrieve cached execution outputs using program ID
            // For crashes, stderr will contain the crash stacktrace and signal info
            let programId = program.id.uuidString
            let (stdout, stderr, fuzzout) = self.executionCache[programId] ?? ("", "", "")
            
            // Clean up the cache entry immediately after use
            self.executionCache.removeValue(forKey: programId)
            
            Task {
                guard let fuzzerId = self.cachedFuzzerId else {
                    self.logger.error("Fuzzer ID not set - registration may have failed")
                    return
                }
                
                await self.storage.addProgramToBatch(program, fuzzerId: fuzzerId)
                
                // Extract mutator information from cache or contributors
                let mutatorName = self.mutatorCache[programId] ?? program.contributors.first(where: { contributor in
                    contributor.name.contains("Mutator")
                })?.name
                self.mutatorCache.removeValue(forKey: programId) 

                let mutatorTypeId = mutatorName != nil ? DatabaseUtils.mapMutatorNameToId(mutatorName!) : nil
                
                // Get coverage metrics if available (crashes may still have coverage)
                var coverageTotal: Double? = nil
                var edgesFound: Int? = nil
                var totalEdges: Int? = nil
                var turbofanOptimizationBits: Int64? = nil
                var feedbackNexusCount: Int? = nil
                
                if let evaluator = self.covEvaluator {
                    let totalEdgesCount = evaluator.getTotalEdgesCount()
                    totalEdges = Int(totalEdgesCount)
                    
                    let foundEdgesCount = evaluator.getFoundEdgesCount()
                    
                    if totalEdgesCount > 0 {
                        coverageTotal = (Double(foundEdgesCount) / Double(totalEdgesCount)) * 100
                    }
                    
                    // For crashes, we don't have new edge information in the same way
                    edgesFound = Int(foundEdgesCount)
                    
                    turbofanOptimizationBits = Int64(evaluator.getTurbofanOptimizationBits())
                    feedbackNexusCount = Int(evaluator.getFeedbackNexusCount())
                }
                
                let programHash = DatabaseUtils.calculateProgramHash(program: program)
                
                // Create execution record with outcome_id = 1 (Crashed)
                let executionInput = PostgreSQLStorage.ExecutionInput(
                    programHash: programHash,
                    mutatorTypeId: mutatorTypeId,
                    executionOutcomeId: 1,  // Crashed
                    coverageTotal: coverageTotal,
                    edgesFound: edgesFound,
                    totalEdges: totalEdges,
                    isNewEdge: false,  // Crashes don't contribute new edges
                    stdout: stdout,
                    stderr: stderr,  // Contains crash stacktrace and signal info
                    fuzzout: fuzzout,
                    turbofanOptimizationBits: turbofanOptimizationBits,
                    feedbackNexusCount: feedbackNexusCount,
                    createdAt: Date()
                )
                await self.storage.addExecutionToBatch(executionInput)
                
                if self.enableLogging {
                    let mutatorInfo = mutatorName != nil ? " (mutator: \(mutatorName!))" : ""
                    let behaviourInfo = behaviour == .deterministic ? "deterministic" : "flaky"
                    let uniqueInfo = isUnique ? "unique" : "duplicate"
                    self.logger.info("Added crash to batch: \(behaviourInfo), \(uniqueInfo)\(mutatorInfo)")
                }
            }
        }
        
        // Periodic Flush
        // TODO Aleksi: 1 minute for testing but update later
        fuzzer.timers.scheduleTask(every: 1 * Minutes) {
            Task {
                do {
                    try await self.storage.flushBatches()
                    if self.enableLogging {
                        self.logger.info("Flushed batches to database")
                    }
                } catch {
                    let errorString = String(reflecting: error)
                    self.logger.error("Failed to flush batches: \(errorString)")
                }
            }
        }
        
        // Heartbeat: Update fuzzer activity every 1 minute to prevent being marked as stale
        // TODO Aleksi: 1 minute for testing but update later
        fuzzer.timers.scheduleTask(every: 1 * Minutes) {
            Task {
                if let fuzzerId = self.cachedFuzzerId {
                    do {
                        try await self.storage.updateFuzzerActivity(fuzzerId: fuzzerId)
                        if self.enableLogging {
                            self.logger.info("Heartbeat: Updated fuzzer activity")
                        }
                    } catch {
                        self.logger.error("Failed to update fuzzer activity: \(error)")
                    }
                }
            }
        }
        
        // Periodic Sync (Pull) from Database
        fuzzer.timers.scheduleTask(every: 1 * Minutes) {
            Task {
                await self.syncWithDatabase(fuzzer)
            }
        }
        
        // Shutdown handler - deactivate fuzzer for graceful shutdown
        fuzzer.registerEventListener(for: fuzzer.events.Shutdown) { _ in
            Task {
                if let fuzzerId = self.cachedFuzzerId {
                    do {
                        try await self.storage.deactivateFuzzer(fuzzerId: fuzzerId)
                        if self.enableLogging {
                            self.logger.info("Fuzzer deactivated on shutdown")
                        }
                    } catch {
                        self.logger.error("Failed to deactivate fuzzer on shutdown: \(error)")
                    }
                }
            }
        }
    }
    
    private func syncWithDatabase(_ fuzzer: Fuzzer) async {
        if enableLogging {
            logger.info("Starting periodic sync with database. Last sync time: \(lastSyncTime)")
        }
        do {
            let newPrograms = try await storage.fetchNewPrograms(since: lastSyncTime, limit: 100)
            if !newPrograms.isEmpty {
                if enableLogging {
                    logger.info("Fetched \(newPrograms.count) new programs from database")
                }
                
                lastSyncTime = Date()
                
                for (program, _) in newPrograms {
                    fuzzer.async {
                        fuzzer.importProgram(program, origin: .corpusImport(mode: .full), enableDropout: false)
                    }
                }
            }
        } catch {
            logger.error("Failed to sync with database: \(error)")
        }
    }
}
