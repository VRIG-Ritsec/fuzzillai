import Foundation

public enum FrameType {
    case interpreter
    case sparkplug
    case maglev
    case turbofan
    case deopt
    case jitReturn
}


public struct Frame: Equatable {
    let bytecode_offset: Int
    let acc: String
    let args: [String]
    let regs: [String]
    let function_id: Int
    let frame_type: FrameType
}

public enum OracleLogLevel {
    case none
    case info
    case debug
}

/// Compare two frames for equality
/// Warning: It is not communtative! The opt frame must be lhs.
/// A string value of "<optimized_out>" in lhs is equal to every other string
/// value in acc, args, and regs
/// - Parameters:
///  - lhs: The unopt frame
///  - rhs: The opt frame
public func == (lhs: Frame, rhs: Frame) -> Bool {
    return lhs.bytecode_offset == rhs.bytecode_offset &&
           (lhs.acc == rhs.acc || lhs.acc == "<optimized_out>") &&
           lhs.args.count == rhs.args.count &&
           lhs.regs.count == rhs.regs.count &&
           (0..<lhs.args.count).allSatisfy { lhs.args[$0] == rhs.args[$0] || lhs.args[$0] == "<optimized_out>" } &&
           (0..<lhs.regs.count).allSatisfy { lhs.regs[$0] == rhs.regs[$0] || lhs.regs[$0] == "<optimized_out>" } &&
           lhs.function_id == rhs.function_id
}




public func parseDiffFrame(_ frameArr: ArraySlice<Substring>, _ lastFrame: inout Frame?,
                           _ prevRegs: inout [String], _ prevArgs: inout [String]) -> Frame {
    func parseValue<T>(prefix: String, defaultValue: T, index: inout Int, conversion: (Substring) -> T) -> T {
        if index < frameArr.endIndex, frameArr[index].starts(with: prefix) {
            let value = conversion(frameArr[index].dropFirst(prefix.count))
            index += 1
            return value
        }
        return defaultValue
    }

    let frameType: FrameType
    var i = frameArr.startIndex
    switch frameArr[i] {
        case "---I":
            frameType = .interpreter
        case "---S":
            frameType = .sparkplug
        case "---M":
            frameType = .maglev
        case "---T":
            frameType = .turbofan
        case "---D":
            frameType = .deopt
        case "---R":
            frameType = .jitReturn
        default:
            fatalError("Unknown frame type")
    }
    i += 1

    let bytecode_offset = parseValue(prefix: "b:", defaultValue: lastFrame?.bytecode_offset ?? 4242, index: &i){ Int($0)! }
    let function_id = parseValue(prefix: "f:", defaultValue: lastFrame?.function_id ?? 4242, index: &i){ Int($0)! }
    let arg_count = parseValue(prefix: "n:", defaultValue: lastFrame?.args.count ?? 4242, index: &i){ Int($0)! }
    let reg_count = parseValue(prefix: "m:", defaultValue: lastFrame?.regs.count ?? 4242, index: &i){ Int($0)! }
    let acc = parseValue(prefix: "x:", defaultValue: lastFrame?.acc ?? "", index: &i){ String($0) }

    func updateValues(prefix: String, totalCount: Int, oldValues: [String], prevValues: inout [String]) -> [String] {
        var newValues: [String]
        // performance improvement to use Swifts copy on write
        if oldValues.count == totalCount {
            newValues = oldValues
        }
        else {
            newValues = [String]()
            newValues.reserveCapacity(totalCount)
            // copy up to totalCount from prevValues into newValues
            for j in 0..<min(prevValues.count, totalCount) {
                newValues.append(prevValues[j])
            }
            // make sure newValues has totalCount elements
            while newValues.count < totalCount {
                newValues.append("")
            }
        }

        // make sure prevValues is at least totalCount elements in size
        while prevValues.count < totalCount {
            prevValues.append("")
        }

        while i < frameArr.endIndex && frameArr[i].starts(with: prefix) {
            let data = frameArr[i].dropFirst(1).split(separator: ":", maxSplits: 1)
            let number = Int(data[0])!
            let value = String(data[1])
            newValues[number] = value
            prevValues[number] = value
            i += 1
        }
        return newValues

    }

    let args = updateValues(prefix: "a", totalCount: arg_count, oldValues: lastFrame?.args ?? [], prevValues: &prevArgs)
    let regs = updateValues(prefix: "r", totalCount: reg_count, oldValues: lastFrame?.regs ?? [], prevValues: &prevRegs)


    let frame = Frame(bytecode_offset: bytecode_offset,
                      acc: acc,
                      args: args,
                      regs: regs,
                      function_id: function_id,
                      frame_type: frameType)
    return frame
}

public func parseToLinear(_ stdout: String) -> [[Frame]] {
    var stack: [[Frame]] = [[], []]
    var linearized: [[Frame]] = []
    var lastFrame: Frame? = nil

    var prevArgs: [String] = [String]()
    var prevRegs: [String] = [String]()
    prevArgs.reserveCapacity(64)
    prevRegs.reserveCapacity(128)

    let split = stdout.split(separator: "\n", omittingEmptySubsequences: false)
    var i = 0

    while i < split.count {
        if split[i] == ">" {
            stack.append([])
        }
        else if split[i] == "<" {
            linearized.append(stack.removeLast())
        }
        else if split[i].starts(with: "---") {
            let start = i
            while (split[i] != "") {
                i += 1
            }
            let end = i-1
            let frame = split[start...end]
            // append to last stack frame
            lastFrame = parseDiffFrame(frame, &lastFrame, &prevArgs, &prevRegs)
            stack[stack.count - 1].append(lastFrame!)
        }
        i += 1
    }
    for _ in stack {
        linearized.append(stack.removeLast())
    }
    return linearized
}

public func relate(_ optIn: String, with unoptIn: String, _ logLevel: OracleLogLevel = .none) -> Bool {
    // Quick and dirty way to not get NaN vs <uninitialized_value> spam
    let optChunks = parseToLinear(optIn.replacingOccurrences(of: "<uninitialized_value>", with: "NaN"))
    let unoptChunks = parseToLinear(unoptIn.replacingOccurrences(of: "<uninitialized_value>", with: "NaN"))


    if logLevel == .debug {
            print("opt Chunks:")
            print(optChunks as AnyObject)
            print("unopt Chunks:")
            print(unoptChunks as AnyObject)
        }
    if optChunks.count != unoptChunks.count {
        if logLevel != .none {
            print("Difference in chunk count \(optChunks.count) != \(unoptChunks.count)")
        }
        return false
    }
    for (optChunk, unoptChunk) in zip(optChunks, unoptChunks) {
        for optFrame in optChunk {
            // check if optFrame is in unoptChunk
            if !unoptChunk.contains(where: {optFrame == $0}) {
                if logLevel != .none {
                    print(optFrame as AnyObject)
                    print("--------------------------")
                    print("[")
                    for unoptFrame in unoptChunk {
                        if unoptFrame.bytecode_offset == optFrame.bytecode_offset {
                            print(unoptFrame as AnyObject)
                        }
                    }
                    print("]")
                }
                return false
            }
        }
    }
    return true
}
