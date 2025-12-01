import Foundation
import SwiftProtobuf
import Crypto

public class DatabaseUtils {

    public static func encodeProgramToBase64(program: Program) throws -> String {
        let data = try encodeProgramToProtobuf(program: program)
        return data.base64EncodedString()
    }

    public static func decodeProgramFromBase64(base64: String) throws -> Program {
        guard let data = Data(base64Encoded: base64) else {
            throw DatabaseUtilsError.invalidBase64String
        }
        return try decodeProgramFromProtobuf(data: data)
    }

    public static func encodeProgramToProtobuf(program: Program) throws -> Data {
        // Make sure the program does not contain internal operations
        if program.code.contains(where: { $0.op is JsInternalOperation }) {
            throw DatabaseUtilsError.programContainsInternalOperations
        }
        
        var proto = program.asProtobuf()
        // We don't want to store the parent history in the blob as it's stored by reference in the DB
        // This prevents the blob from growing indefinitely and hitting size limits
        proto.clearParent()
        
        return try proto.serializedData()
    }

    public static func decodeProgramFromProtobuf(data: Data) throws -> Program {
        let proto = try Fuzzilli_Protobuf_Program(serializedBytes: data)
        return try Program(from: proto)
    }
    
    public static func calculateProgramHash(program: Program) throws -> String {
        // Make sure the program does not contain internal operations
        if program.code.contains(where: { $0.op is JsInternalOperation }) {
            throw DatabaseUtilsError.programContainsInternalOperations
        }

        var proto = program.asProtobuf()
        // We don't want to store the parent history in the blob as it's stored by reference in the DB
        // This prevents the blob from growing indefinitely and hitting size limits
        proto.clearParent()
        
        let data = try proto.serializedData()

        // Use SHA-256 for deterministic, stable hashing
        // Swift's hashValue is non-deterministic and can produce different values for the same data
        var hasher = SHA256()
        hasher.update(data: data)
        let digest = hasher.finalize()
        
        // Convert to hex string (64 characters for SHA-256)
        return digest.map { String(format: "%02x", $0) }.joined()
    }
    
    public static func mapExecutionOutcome(outcome: ExecutionOutcome) -> Int {
        switch outcome {
        case .succeeded:
            return 3
        case .failed:
            return 2
        case .crashed:
            return 1
        case .timedOut:
            return 4
        }
    }
    
    public static func mapMutatorNameToId(_ name: String) -> Int? {
        switch name {
        case "ExplorationMutator":
            return 1
        case "CodeGenMutator":
            return 2
        case "SpliceMutator":
            return 3
        case "ProbingMutator":
            return 4
        case "InputMutator":
            return 5
        case "OperationMutator":
            return 6
        case "CombineMutator":
            return 7
        case "ConcatMutator":
            return 8
        case "FixupMutator":
            return 9
        case "RuntimeAssistedMutator":
            return 10
        default:
            return nil
        }
    }
}

public enum DatabaseUtilsError: Error, LocalizedError {
    case invalidBase64String
    case invalidProgramData
    case serializationFailed
    case deserializationFailed
    case invalidHash
    case invalidMetadata
    case programContainsInternalOperations
    
    public var errorDescription: String? {
        switch self {
        case .invalidBase64String:
            return "Invalid base64 string"
        case .invalidProgramData:
            return "Invalid program data"
        case .serializationFailed:
            return "Failed to serialize data"
        case .deserializationFailed:
            return "Failed to deserialize data"
        case .invalidHash:
            return "Invalid hash format"
        case .invalidMetadata:
            return "Invalid metadata format"
        case .programContainsInternalOperations:
            return "Program contains internal operations and cannot be serialized"
        }
    }
}
