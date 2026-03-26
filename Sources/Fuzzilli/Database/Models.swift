import Foundation

// MARK: - Database Models

// This is the main table in reality
/// Represents a fuzzer instance in the main table
public struct FuzzerInstance: Codable {
    public let fuzzerId: Int
    public let createdAt: Date
    public let status: String
    public let lastActivity: Date
    public let engineArguments: [String]?
    
    public init(fuzzerId: Int, createdAt: Date, status: String, lastActivity: Date, engineArguments: [String]?) {
        self.fuzzerId = fuzzerId
        self.createdAt = createdAt
        self.status = status
        self.lastActivity = lastActivity
        self.engineArguments = engineArguments
    }
}

/// Represents a program in the fuzzer table (corpus)
public struct FuzzerCorpusProgram: Codable {
    public let programHash: String
    public let fuzzerId: Int
    public let insertedAt: Date
    public let programBase64: String
    
    public init(programHash: String, fuzzerId: Int, insertedAt: Date, programBase64: String) {
        self.programHash = programHash
        self.fuzzerId = fuzzerId
        self.insertedAt = insertedAt
        self.programBase64 = programBase64
    }
}

/// Represents a program in the program table (execution metadata)
public struct ProgramMetadata: Codable {
    public let programHash: String
    public let fuzzerId: Int
    public let createdAt: Date
    public let sourceMutator: String?
    public let parentProgramHash: String?
    
    public init(programHash: String, fuzzerId: Int, createdAt: Date, sourceMutator: String?, parentProgramHash: String?) {
        self.programHash = programHash
        self.fuzzerId = fuzzerId
        self.createdAt = createdAt
        self.sourceMutator = sourceMutator
        self.parentProgramHash = parentProgramHash
    }
}

/// Represents an execution record in the execution table
public struct ExecutionRecord: Codable {
    public let executionId: Int
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
    
    public init(executionId: Int, programHash: String, mutatorTypeId: Int?, executionOutcomeId: Int, coverageTotal: Double?, edgesFound: Int?, totalEdges: Int?, isNewEdge: Bool, stdout: String?, stderr: String?, fuzzout: String?, turbofanOptimizationBits: Int64?, feedbackNexusCount: Int?, createdAt: Date) {
        self.executionId = executionId
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

// MARK: - Lookup Tables

/// Mutator type lookup table
public struct MutatorType: Codable {
    public let id: Int
    public let name: String
    public let category: String?
    
    public init(id: Int, name: String, category: String?) {
        self.id = id
        self.name = name
        self.category = category
    }
}

/// Execution outcome lookup table
public struct DatabaseExecutionOutcome: Codable {
    public let id: Int
    public let outcome: String
    
    public init(id: Int, outcome: String) {
        self.id = id
        self.outcome = outcome
    }
}

// MARK: - In-Memory Execution Metadata

/// Execution metadata for in-memory tracking
public struct ExecutionMetadata: Codable {
    public var executionCount: Int
    public var lastExecutionTime: Date
    public var lastCoverage: Double
    public var lastOutcome: DatabaseExecutionOutcome
    public var recentExecutions: [ExecutionRecord] // Last 10 executions
    public var coverageEdges: Set<Int>
    
    public init(executionCount: Int = 0, lastExecutionTime: Date = Date(), lastCoverage: Double = 0.0, lastOutcome: DatabaseExecutionOutcome, recentExecutions: [ExecutionRecord] = [], coverageEdges: Set<Int> = []) {
        self.executionCount = executionCount
        self.lastExecutionTime = lastExecutionTime
        self.lastCoverage = lastCoverage
        self.lastOutcome = lastOutcome
        self.recentExecutions = recentExecutions
        self.coverageEdges = coverageEdges
    }
    
    /// Add a new execution to the recent executions list, keeping only the last 10
    public mutating func addExecution(_ execution: ExecutionRecord) {
        recentExecutions.append(execution)
        if recentExecutions.count > 10 {
            recentExecutions.removeFirst()
        }
        
        // Update metadata
        executionCount += 1
        lastExecutionTime = execution.createdAt
        if let coverage = execution.coverageTotal {
            lastCoverage = coverage
        }
        
        // Update outcome (we'll need to map from executionOutcomeId)
        // This will be handled by the caller who has access to the lookup table
    }
    
    /// Update the last outcome (called after adding execution)
    public mutating func updateLastOutcome(_ outcome: DatabaseExecutionOutcome) {
        self.lastOutcome = outcome
    }
}

/// Mutator names for mapping to mutator_type_id
public enum MutatorName: String, CaseIterable {
    case explorationMutator = "ExplorationMutator"
    case codeGenMutator = "CodeGenMutator"
    case spliceMutator = "SpliceMutator"
    case probingMutator = "ProbingMutator"
    case inputMutator = "InputMutator"
    case operationMutator = "OperationMutator"
    case combineMutator = "CombineMutator"
    case concatMutator = "ConcatMutator"
    case fixupMutator = "FixupMutator"
    case runtimeAssistedMutator = "RuntimeAssistedMutator"
    
    public var description: String {
        switch self {
        case .explorationMutator:
            return "Explores new code paths through runtime-assisted mutations"
        case .codeGenMutator:
            return "Generates new code and inserts it into programs"
        case .spliceMutator:
            return "Splices instructions from one program into another"
        case .probingMutator:
            return "Probes for new behaviors through runtime-assisted mutations"
        case .inputMutator:
            return "Changes input variables of instructions"
        case .operationMutator:
            return "Mutates operation parameters"
        case .combineMutator:
            return "Combines programs by inserting one into another"
        case .concatMutator:
            return "Concatenates programs together"
        case .fixupMutator:
            return "Fixes up programs through runtime-assisted mutations"
        case .runtimeAssistedMutator:
            return "Base class for runtime-assisted mutations"
        }
    }
    
    public var category: String {
        switch self {
        case .explorationMutator, .probingMutator, .fixupMutator, .runtimeAssistedMutator:
            return "runtime_assisted"
        case .codeGenMutator, .spliceMutator, .inputMutator, .operationMutator, .combineMutator:
            return "instruction"
        case .concatMutator:
            return "base"
        }
    }
}
