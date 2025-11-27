import Foundation

public class PostgreSQLSync: Module {
    private let storage: PostgreSQLStorage
    private let fuzzerInstanceId: String
    private let enableLogging: Bool
    private var lastSyncTime: Date
    
    private let logger = Logger(withLabel: "PostgreSQLSync")
    
    private var cachedFuzzerId: Int?
    private var covEvaluator: ProgramCoverageEvaluator?  // Cache coverage evaluator for metrics
    
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
                self.cachedFuzzerId = try await storage.registerFuzzer()
                if enableLogging {
                    logger.info("Fuzzer registered with PostgreSQL database: ID \(self.cachedFuzzerId ?? -1)")
                }
                
                // Step 2: Sync corpus from database to in-memory basicCorpus
                let programs = try await storage.syncCorpusFromDatabase()
                if enableLogging {
                    logger.info("Syncing \(programs.count) programs from database to corpus")
                }
                
                // Import each program into the fuzzer's corpus
                for program in programs {
                    fuzzer.async {
                        // Use .corpusImport origin to indicate it came from the database
                        fuzzer.importProgram(program, origin: .corpusImport(mode: .interestingOnly(shouldMinimize: false)))
                    }
                }
                
                if enableLogging {
                    logger.info("Corpus synchronization complete: imported \(programs.count) programs")
                }
            } catch {
                logger.error("Failed to register fuzzer with PostgreSQL database: \(error)")
            }
        }
        
        
        fuzzer.registerEventListener(for: fuzzer.events.InterestingProgramFound) { ev in
            let program = ev.program
            let aspects = ev.aspects
            let execution = ev.execution
            
            // Only sync programs found locally to avoid cycles
            guard ev.origin == .local else { return }
            
            // Capture execution outputs synchronously to avoid race conditions and thread safety issues
            // Accessing these properties triggers event dispatching which must happen on the Fuzzer queue
            let stdout = execution?.stdout ?? ""
            let stderr = execution?.stderr ?? ""
            let fuzzout = execution?.fuzzout ?? ""
            
            Task {
                // Ensure we have a fuzzer ID (should already be set from initialization)
                guard let fuzzerId = self.cachedFuzzerId else {
                    self.logger.error("Fuzzer ID not set - registration may have failed")
                    return
                }
                
                // Store program and execution data
                await self.storage.addProgramToBatch(program, fuzzerId: fuzzerId)
                    
                // Only store execution if we have execution data
                if let execution = execution {
                    let outcomeId = DatabaseUtils.mapExecutionOutcome(outcome: execution.outcome)
                    
                    let mutatorName = program.contributors.first(where: { contributor in
                        // Check if this contributor's name matches known mutator patterns
                        contributor.name.contains("Mutator")
                    })?.name
                    
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
                        
                        // Calculate coverage percentage
                        if totalEdgesCount > 0 {
                            coverageTotal = Double(foundEdgesCount) / Double(totalEdgesCount)
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
                        let mutatorInfo = mutatorName != nil ? " (mutator: \(mutatorName!))" : ""
                        let edgeInfo = isNewEdge ? " with new edges" : " (feedback/optimization delta only)"
                        self.logger.info("Added interesting program and execution to batch\(mutatorInfo)\(edgeInfo)")
                    }
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
        do {
            let newPrograms = try await storage.fetchNewPrograms(since: lastSyncTime)
            if !newPrograms.isEmpty {
                if enableLogging {
                    logger.info("Fetched \(newPrograms.count) new programs from database")
                }
                
                lastSyncTime = Date()
                
                for (program, _) in newPrograms {
                    fuzzer.async {
                        // Use .corpusImport origin to indicate it came from the database
                        fuzzer.importProgram(program, origin: .corpusImport(mode: .interestingOnly(shouldMinimize: false)))
                    }
                }
            }
        } catch {
            logger.error("Failed to sync with database: \(error)")
        }
    }
}
