import Foundation

public class PostgreSQLSync: Module {
    public enum SyncMode: String {
        case shared = "shared"
        case generated = "generated"
        case push = "push"
    }

    private let storage: PostgresSQLStorage
    private let fuzzerInstanceId: String
    private let enableLogging: Bool
    private let syncMode: SyncMode
    private var lastSyncCursor: (insertedAt: Date, hash: String)
    
    private let logger = Logger(withLabel: "PostgreSQLSync")
    
    private var cachedFuzzerId: Int?
    private var covEvaluator: ProgramCoverageEvaluator?  // Cache coverage evaluator for metrics
    
    // Cache for execution outputs to avoid race conditions with REPRL
    // Key: program ID (UUID string), Value: execution outputs
    private var executionCache: [String: (stdout: String, stderr: String, fuzzout: String)] = [:]
    private let maxCacheSize = 1000 

    private var mutatorCache: [String: [String]] = [:]
    private var contributorCache: [String: [String]] = [:]

    public init(storage: PostgresSQLStorage, fuzzerInstanceId: String, enableLogging: Bool = false, syncMode: SyncMode = .generated) {
        self.storage = storage
        self.fuzzerInstanceId = fuzzerInstanceId
        self.enableLogging = enableLogging
        self.syncMode = syncMode
        self.lastSyncCursor = (insertedAt: Date(timeIntervalSince1970: 0), hash: "")
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
        
        // Register fuzzer synchronously before setting up event listeners
        // This ensures cachedFuzzerId is set before any events start firing
        // Use a semaphore to block until registration completes
        let registrationSemaphore = DispatchSemaphore(value: 0)
        
        Task {
            do {
                // Sleep for a random amount of time to avoid stampedes
                let jitter = TimeInterval.random(in: 0...60)
                try? await Task.sleep(nanoseconds: UInt64(jitter * 1_000_000_000))

                // Step 1: Register this worker with the database (blocking via semaphore)
                let engineArgs = fuzzer.config.arguments
                self.cachedFuzzerId = try await storage.registerFuzzer(engineArguments: engineArgs)
                if enableLogging {
                    logger.info("Fuzzer registered with PostgreSQL database: fuzzerId \(self.cachedFuzzerId ?? -1)")
                }
                
                // Registration complete
                registrationSemaphore.signal()

                // Step 2: Perform the selected pull behavior.
                switch self.syncMode {
                case .push:
                    if enableLogging {
                        logger.info("Push-only mode: skipping initial corpus pull from database")
                    }
                case .shared:
                    let programs = try await storage.syncCorpusFromDatabase()
                    logger.info("Found: \(programs.count) programs from db")
                    if enableLogging {
                        logger.info("Syncing \(programs.count) programs from database to corpus")
                    }

                    if let latestCursor = try await storage.fetchLatestProgramSyncCursor() {
                        self.lastSyncCursor = latestCursor
                    }

                    // Import each program into the fuzzer's corpus
                    for program in programs {
                        fuzzer.async {
                            // changing from full to interestingOnly(shouldMinimize: true)
                            // watch results for this and change back to .full if needed
                            fuzzer.importProgram(program, origin: .corpusImport(mode: .interestingOnly(shouldMinimize: true)), enableDropout: false)
                        }
                    }

                    if enableLogging {
                        logger.info("Corpus synchronization complete: imported \(programs.count) programs")
                    }
                case .generated:
                    await self.syncGeneratedQueue(fuzzer)
                }
            } catch {
                logger.error("Failed to register fuzzer with PostgreSQL database: \(String(reflecting: error))")
                // Signal even on error so we don't deadlock
                registrationSemaphore.signal()
            }
        }
        
        // Wait for registration to complete before continuing with event listener setup
        registrationSemaphore.wait()
        
        if enableLogging {
            logger.info("Fuzzer registration complete, proceeding with event listener setup")
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
            
            self.cleanupCache(&self.executionCache)
        }

        // Cache all mutator names and contributor names from ProgramGenerated event (before minimization)
        // This works around the fact that contributors don't survive protobuf serialization
        fuzzer.registerEventListener(for: fuzzer.events.ProgramGenerated) { program in
            let programId = program.id.uuidString

            let allContributorNames = program.contributors.map { $0.name }
            let mutatorNames = allContributorNames.filter { $0.contains("Mutator") }
            let contributorNames = allContributorNames.filter { $0.contains("Contributor") }

            if !mutatorNames.isEmpty {
                self.mutatorCache[programId] = mutatorNames
                self.cleanupCache(&self.mutatorCache)
            }
            if !contributorNames.isEmpty {
                self.contributorCache[programId] = contributorNames
                self.cleanupCache(&self.contributorCache)
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

            guard !ev.origin.isFromCorpusImport() else {
                return
            }
            
            Task {
                guard let fuzzerId = self.cachedFuzzerId else {
                    self.logger.error("Fuzzer ID not set - registration may have failed")
                    return
                }

                let mutatorNames =  self.mutatorCache[programId] ?? [] 
                let contributorNames = self.contributorCache[programId] ?? []
                
                let programInput = PostgresSQLStorage.ProgramInput(
                    program: program,
                    fuzzerId: fuzzerId,
                    mutatorNames: mutatorNames,
                    contributorNames: contributorNames
                )
                await self.storage.addProgramToBatch(programInput)
                    
                if let execution = execution {
                    let outcomeId = DatabaseUtils.mapExecutionOutcome(outcome: execution.outcome)

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
                    
                    let programHash: String
                    do {
                        programHash = try DatabaseUtils.calculateProgramHash(program: program)
                    } catch {
                        if self.enableLogging {
                            self.logger.warning("Failed to calculate program hash, skipping execution record: \(error)")
                        }
                        return
                    }
                    
                    let executionInput = PostgresSQLStorage.ExecutionInput(
                        programHash: programHash,
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
                    
                    // TODO Aleksi: This is a bit too verbose, maybe only log every x times
                    //if self.enableLogging {
                    //    let edgeInfo = isNewEdge ? " with new edges" : " (feedback/optimization delta only)"
                    //    self.logger.verbose("Added interesting program and execution to batch\(edgeInfo)")
                    //}
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

            guard !ev.origin.isFromCorpusImport() else {
                return
            }
            
            Task {
                guard let fuzzerId = self.cachedFuzzerId else {
                    self.logger.error("Fuzzer ID not set - registration may have failed")
                    return
                }

                let mutatorNames =  self.mutatorCache[programId] ?? [] 
                let contributorNames = self.contributorCache[programId] ?? []
                
                let programInput = PostgresSQLStorage.ProgramInput(
                    program: program,
                    fuzzerId: fuzzerId,
                    mutatorNames: mutatorNames,
                    contributorNames: contributorNames
                )
                await self.storage.addProgramToBatch(programInput)
                
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
                
                let programHash: String
                do {
                    programHash = try DatabaseUtils.calculateProgramHash(program: program)
                } catch {
                    if self.enableLogging {
                        self.logger.warning("Failed to calculate program hash for crash, skipping execution record: \(error)")
                    }
                    return
                }
                
                // Create execution record with outcome_id = 1 (Crashed)
                let executionInput = PostgresSQLStorage.ExecutionInput(
                    programHash: programHash,
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
                    let behaviourInfo = behaviour == .deterministic ? "deterministic" : "flaky"
                    let uniqueInfo = isUnique ? "unique" : "duplicate"
                    self.logger.info("Added crash to batch: \(behaviourInfo), \(uniqueInfo)")
                }
            }
        }
        
        // Periodic Flush
        fuzzer.timers.scheduleTask(every: 15 * Minutes) {
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
        
        // Periodic pull from PostgreSQL, according to the selected sync mode.
        switch syncMode {
        case .shared:
            fuzzer.timers.scheduleTask(every: 15 * Minutes) {
                Task {
                    await self.syncSharedCorpus(fuzzer)
                }
            }
        case .generated:
            fuzzer.timers.scheduleTask(every: 15 * Minutes) {
                Task {
                    await self.syncGeneratedQueue(fuzzer)
                }
            }
        case .push:
            if enableLogging {
                logger.info("Push-only mode: periodic corpus pull from database disabled")
            }
        }

        fuzzer.timers.scheduleTask(every: 30 * Minutes) {
            Task {
                await self.syncMutatorStats(fuzzer)
            }
        }

        fuzzer.timers.scheduleTask(every: 35 * Minutes) {
            Task {
                do {
                    try await self.storage.refreshMaterializedViews()
                    if self.enableLogging {
                        self.logger.info("Refreshed materialized views")
                    }
                } catch {
                    self.logger.error("Failed to refresh materialized views: \(error)")
                }
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
    
    private func syncSharedCorpus(_ fuzzer: Fuzzer) async {
        if enableLogging {
            logger.info("Starting shared corpus sync with database. Last sync cursor: \(lastSyncCursor.insertedAt) / \(lastSyncCursor.hash)")
        }
        do {
            while true {
                let newPrograms = try await storage.fetchNewPrograms(since: lastSyncCursor.insertedAt, after: lastSyncCursor.hash, limit: 2000)
                guard !newPrograms.isEmpty else { return }

                if enableLogging {
                    logger.info("Fetched \(newPrograms.count) new programs from database")
                }

                if let newestRecord = newPrograms.last {
                    lastSyncCursor = (insertedAt: newestRecord.insertedAt, hash: newestRecord.hash)
                }

                for record in newPrograms {
                    fuzzer.async {
                        fuzzer.importProgram(record.program, origin: .corpusImport(mode: .full), enableDropout: false)
                    }
                }

                if newPrograms.count < 2000 {
                    return
                }
            }
        } catch {
            logger.error("Failed to sync shared corpus with database: \(String(reflecting: error))")
        }
    }

    private func syncGeneratedQueue(_ fuzzer: Fuzzer) async {
        guard let fuzzerId = self.cachedFuzzerId else {
            logger.error("Cannot sync generated queue before fuzzer registration completes")
            return
        }

        if enableLogging {
            logger.info("Starting generated queue sync for fuzzer_id \(fuzzerId)")
        }

        do {
            while true {
                let generatedPrograms = try await storage.dequeueGeneratedPrograms(targetFuzzerId: fuzzerId, limit: 2000)
                guard !generatedPrograms.isEmpty else { return }

                if enableLogging {
                    logger.info("Pulled \(generatedPrograms.count) generated programs for fuzzer_id \(fuzzerId)")
                }

                for record in generatedPrograms {
                    fuzzer.async {
                        fuzzer.importProgram(record.program, origin: .corpusImport(mode: .full), enableDropout: false)
                    }
                }

                if generatedPrograms.count < 2000 {
                    return
                }
            }
        } catch {
            logger.error("Failed to sync generated queue: \(String(reflecting: error))")
        }
    }

    private func syncMutatorStats(_ fuzzer: Fuzzer) async {
        guard let fuzzerId = self.cachedFuzzerId else { return }

        var statsInputs: [PostgresSQLStorage.MutatorStatsInput] = []

        for mutator in fuzzer.mutators {
            guard let mutatorTypeId = DatabaseUtils.mapMutatorNameToId(mutator.name) else {
                if enableLogging {
                    logger.warning("Unknown mutator name: \(mutator.name), skipping stats sync")
                }
                continue
            }

            let statsInput = PostgresSQLStorage.MutatorStatsInput(
                fuzzerId: fuzzerId,
                mutatorTypeId: mutatorTypeId,
                totalSamples: mutator.totalSamples,
                crashesFound: mutator.crashesFound,
                timeouts: mutator.timedOutSamples,
                interestingSamples: mutator.interestingSamples,
                invalidSamples: mutator.invalidSamples,
                validSamples: mutator.validSamples,
                totalInstructionsAdded: mutator.totalInstructionProduced,
                correctnessRate: mutator.correctnessRate,
                failureRate: mutator.failureRate,
                timeoutRate: mutator.timeoutRate,
                interestingSamplesRate: mutator.interestingSamplesRate,
                avgInstructionsAdded: mutator.avgNumberOfInstructionsGenerated
            )

            statsInputs.append(statsInput)
        }

        do {
            try await storage.updateMutatorStats(statsInputs)
            if enableLogging {
                logger.info("Synced mutator statistics for \(statsInputs.count) mutators")
            }
        } catch {
            logger.error("Failed to sync mutator stats: \(String(reflecting: error))")
        }
    }

    private func cleanupCache<K: Hashable, V>(_ cache: inout [K: V]) {
        if cache.count > self.maxCacheSize {
            let keysToRemove = Array(cache.keys.prefix(100))
            for key in keysToRemove {
                cache.removeValue(forKey: key)
            }
        }
    }
}
