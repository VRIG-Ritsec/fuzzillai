import Foundation

public class PostgreSQLSync: Module {
    private let storage: PostgreSQLStorage
    private let fuzzerInstanceId: String
    private let enableLogging: Bool
    private var lastSyncTime: Date
    
    // Logger
    private let logger = Logger(withLabel: "PostgreSQLSync")
    
    private var cachedFuzzerId: Int?
    private var programExecutionMap: [String: Execution] = [:]  // Track executions by program hash
    private let executionMapLock = NSLock()
    
    public init(storage: PostgreSQLStorage, fuzzerInstanceId: String, enableLogging: Bool = false) {
        self.storage = storage
        self.fuzzerInstanceId = fuzzerInstanceId
        self.enableLogging = enableLogging
        self.lastSyncTime = Date()
    }
    
    public func initialize(with fuzzer: Fuzzer) {
        // Register fuzzer
        Task {
            do {
                self.cachedFuzzerId = try await storage.registerFuzzer(name: fuzzerInstanceId, engineType: "v8")
                if enableLogging {
                    logger.info("Fuzzer registered with PostgreSQL database: ID \(self.cachedFuzzerId ?? -1)")
                }
            } catch {
                logger.error("Failed to register fuzzer with PostgreSQL database: \(error)")
            }
        }
        
        // Listen for executions to track them
        fuzzer.registerEventListener(for: fuzzer.events.PostExecute) { execution in
            let program = execution.program
            let programHash = DatabaseUtils.calculateProgramHash(program: program)
            
            self.executionMapLock.lock()
            self.programExecutionMap[programHash] = execution
            self.executionMapLock.unlock()
        }
        
        // Listen for new interesting programs found by this fuzzer
        fuzzer.registerEventListener(for: fuzzer.events.InterestingProgramFound) { ev in
            let program = ev.program
            let aspects = ev.aspects  // Now available directly from the event!
            // Only sync programs found locally to avoid cycles
            guard ev.origin == .local else { return }
            
            Task {
                do {
                    // Ensure we have a fuzzer ID
                    if self.cachedFuzzerId == nil {
                        self.cachedFuzzerId = try await self.storage.registerFuzzer(name: self.fuzzerInstanceId, engineType: "v8")
                    }
                    
                    if let fuzzerId = self.cachedFuzzerId {
                        let programHash = DatabaseUtils.calculateProgramHash(program: program)
                        
                        // Add program to batch
                        self.storage.addProgramToBatch(program, fuzzerId: fuzzerId)
                        
                        // Retrieve execution data
                        self.executionMapLock.lock()
                        let execution = self.programExecutionMap[programHash]
                        self.executionMapLock.unlock()

                        // Add execution data to batch
                        if let execution = execution {
                            let outcomeId = DatabaseUtils.mapExecutionOutcome(outcome: execution.outcome)
                            
                            // Extract mutator information from program contributors
                            // Contributors is a Set<Contributor>, we look for Mutator instances
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
                                // ProgramAspects but not CovEdgeSet = feedback/optimization delta only
                                isNewEdge = false
                            }
                            
                            let executionInput = PostgreSQLStorage.ExecutionInput(
                                programHash: programHash,
                                mutatorTypeId: mutatorTypeId,
                                executionOutcomeId: outcomeId,
                                coverageTotal: nil,  // TODO: Phase 2 - Extract from evaluator
                                edgesFound: nil,     // TODO: Phase 2 - Extract from evaluator
                                totalEdges: nil,     // TODO: Phase 2 - Extract from evaluator
                                isNewEdge: isNewEdge,
                                stdout: execution.stdout,
                                stderr: execution.stderr,
                                fuzzout: execution.fuzzout,
                                turbofanOptimizationBits: nil,  // TODO: Phase 3 - Parse from fuzzout
                                feedbackNexusCount: nil,        // TODO: Phase 3 - Parse from fuzzout
                                createdAt: Date()
                            )
                            self.storage.addExecutionToBatch(executionInput)
                            
                            if self.enableLogging {
                                let mutatorInfo = mutatorName != nil ? " (mutator: \(mutatorName!))" : ""
                                let edgeInfo = isNewEdge ? " with new edges" : " (feedback/optimization delta only)"
                                self.logger.info("Added interesting program and execution to batch\(mutatorInfo)\(edgeInfo)")
                            }
                        } else {
                            if self.enableLogging {
                                self.logger.warning("No execution data found for program \(programHash)")
                            }
                        }
                    }
                } catch {
                    self.logger.error("Failed to store program: \(error)")
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
                    
                    // Clean up old execution map entries to prevent memory leak
                    self.executionMapLock.lock()
                    if self.programExecutionMap.count > 10000 {
                        self.programExecutionMap.removeAll()
                    }
                    self.executionMapLock.unlock()
                } catch {
                    self.logger.error("Failed to flush batches: \(error)")
                }
            }
        }
        
        // Periodic Sync (Pull) from Database
        fuzzer.timers.scheduleTask(every: 15 * Minutes) {
            Task {
                await self.syncWithDatabase(fuzzer)
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
                
                lastSyncTime = Date() // Update timestamp
                
                for (program, _) in newPrograms {
                    // Import into fuzzer
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
