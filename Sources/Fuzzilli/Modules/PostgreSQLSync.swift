import Foundation

public class PostgreSQLSync: Module {
    private let storage: PostgresSQLStorage
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

    private var mutatorCache: [String: [String]] = [:]
    private var contributorCache: [String: [String]] = [:]

    public init(storage: PostgresSQLStorage, fuzzerInstanceId: String, enableLogging: Bool = false) {
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
        
        // Register fuzzer synchronously before setting up event listeners
        let semaphore = DispatchSemaphore(value: 0)
        Task {
            do {
                let engineArgs = fuzzer.config.arguments
                self.cachedFuzzerId = try await storage.registerFuzzer(engineArguments: engineArgs)
                if enableLogging {
                    logger.info("Fuzzer registered with PostgreSQL database: fuzzerId \(self.cachedFuzzerId ?? -1)")
                }
                semaphore.signal()

                // Sync corpus from database to in-memory basicCorpus
                let jitter = TimeInterval.random(in: 0...60)
                try? await Task.sleep(nanoseconds: UInt64(jitter * 1_000_000_000))
                let programs = try await storage.syncCorpusFromDatabase()
                for program in programs {
                    fuzzer.async { fuzzer.importProgram(program, origin: .corpusImport(mode: .databaseSync), enableDropout: false) }
                }
            } catch {
                logger.error("Failed to register fuzzer with PostgreSQL database: \(String(reflecting: error))")
                semaphore.signal()
            }
        }
        semaphore.wait()
        
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

        // Cache all mutator names and contributor names from ProgramGenerated event (before minddimization)
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
            self.executionCache.removeValue(forKey: programId)

            guard let fuzzerId = self.cachedFuzzerId else {
                self.logger.error("Fuzzer ID not set - registration may have failed")
                return
            }

            let programInput: PostgresSQLStorage.ProgramInput
            do {
                programInput = try self.prepareProgramInput(program: program, fuzzerId: fuzzerId, programId: programId)
            } catch {
                self.logger.error("Failed to prepare program input: \(error)")
                return
            }

            var executionInput: PostgresSQLStorage.ExecutionInput? = nil
            if let execution = execution {
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

                executionInput = PostgresSQLStorage.ExecutionInput(
                    programHash: programInput.programHash,
                    executionOutcomeId: DatabaseUtils.mapExecutionOutcome(outcome: execution.outcome),
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
            }
            Task {
                await self.storage.addProgramToBatch(programInput)
                if let executionInput = executionInput {
                    await self.storage.addExecutionToBatch(executionInput)
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
            self.executionCache.removeValue(forKey: programId)
            
            guard let fuzzerId = self.cachedFuzzerId else {
                self.logger.error("Fuzzer ID not set - registration may have failed")
                return
            }

            let programInput: PostgresSQLStorage.ProgramInput
            do {
                programInput = try self.prepareProgramInput(program: program, fuzzerId: fuzzerId, programId: programId)
            } catch {
                self.logger.error("Failed to prepare program input: \(error)")
                return
            }

            // Get coverage metrics if available (crashes may still have coverage)
            var coverageTotal: Double? = nil
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
                turbofanOptimizationBits = Int64(evaluator.getTurbofanOptimizationBits())
                feedbackNexusCount = Int(evaluator.getFeedbackNexusCount())
            }
                
            let executionInput = PostgresSQLStorage.ExecutionInput(
                programHash: programInput.programHash,
                executionOutcomeId: 1, // Crashed 
                coverageTotal: coverageTotal,
                edgesFound: nil, // Crashes don't contribute new edges
                totalEdges: totalEdges,
                isNewEdge: false, // Crashes don't contribute new edges
                stdout: stdout,
                stderr: stderr, // Contains crash stacktrace and signal info
                fuzzout: fuzzout,
                turbofanOptimizationBits: turbofanOptimizationBits,
                feedbackNexusCount: feedbackNexusCount,
                createdAt: Date()
            )
            // TODO Aleksi: Store the crash on disk as well
            Task {
                await self.storage.addProgramToBatch(programInput)
                await self.storage.addExecutionToBatch(executionInput)
                
                if self.enableLogging {
                    let behaviourInfo = behaviour == .deterministic ? "deterministic" : "flaky"
                    let uniqueInfo = isUnique ? "unique" : "duplicate"
                    self.logger.info("Added crash to batch: \(behaviourInfo), \(uniqueInfo)")
                }
            }
        }
        
        // Periodic Flush
        fuzzer.timers.scheduleTask(every: 2 * Minutes) {
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
        
        // Periodic Sync (Pull) from Database
        fuzzer.timers.scheduleTask(every: 15 * Minutes) {
            Task {
                await self.syncWithDatabase(fuzzer)
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
    
    private func syncWithDatabase(_ fuzzer: Fuzzer) async {
        if enableLogging {
            logger.info("Starting periodic sync with database. Last sync time: \(lastSyncTime)")
        }
        do {
            let newPrograms = try await storage.fetchNewPrograms(since: lastSyncTime, limit: 2000)
            if !newPrograms.isEmpty {
                if enableLogging {
                    logger.info("Fetched \(newPrograms.count) new programs from database")
                }
                
                lastSyncTime = Date()
                
                for (program, _) in newPrograms {
                    fuzzer.async {
                        fuzzer.importProgram(program, origin: .corpusImport(mode: .databaseSync), enableDropout: false)
                    }
                }
            }
        } catch {
            logger.error("Failed to sync with database: \(String(reflecting: error))")
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

    private func prepareProgramInput(program: Program, fuzzerId: Int, programId: String) throws -> PostgresSQLStorage.ProgramInput {
        let mutatorNames =  self.mutatorCache[programId] ?? [] 
        self.mutatorCache.removeValue(forKey: programId)

        let contributorNames = self.contributorCache[programId] ?? []
        self.contributorCache.removeValue(forKey: programId)
                
        let (programHash, programBase64) = try DatabaseUtils.prepareProgram(program: program)
        let parentHash: String?
        if let parentProgram = program.parent {
            parentHash = try? DatabaseUtils.prepareProgram(program: parentProgram).hash
        } else {
            parentHash = nil
        }

        return PostgresSQLStorage.ProgramInput(
            program: program,
            programHash: programHash,
            programBase64: programBase64,
            fuzzerId: fuzzerId,
            mutatorNames: mutatorNames,
            contributorNames: contributorNames,
            parentHash: parentHash
        )
    }
}
