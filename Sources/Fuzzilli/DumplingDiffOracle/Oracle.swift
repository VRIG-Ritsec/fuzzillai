// Copyright 2026 Google LLC
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
// https://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

// The tool implemented in this file compares two Dumpling dumps for
// equality. Each Dumpling dump consists of multiple frame dumps.
// Example frame dump:
// ---I
// b:34
// f:500
// n:3
// m:5
// x:40
// r0:30
// r3:some_string
// a0:1
// a1:2
//
// A frame dump always starts with a header which is one of
// ---I (for interpreter), ---S (for Sparkplug), ---M (for Maglev),
// ---T (for Turbofan), ---D (deopt Turbofan: this is frame produced by actual
// Turbofan deopt, not by Dumpling).
// Next line with 'b' might follow (see when it and other lines might get omitted
// in the NOTE below), it denotes bytecode offset of a dump (within a function).
// Next line with 'f' might follow, it denotes JS function id of a dump.
// Next line with 'x' might follow, it denotes the dump of the accumulator.
// Next lines with 'n' and 'm' might follow, which denote parameters of a function
// and register count of a frame respectfully.
// Next multiple lines 'ai' might follow, where 'a' denotes that this is a dump
// of a function parameter and 'i' denotes the number of the parameter.
// Next multiple lines 'ri' might follow, where 'r' denotes that this is a dump
// of a register and 'i' denotes the number of the register.
// Lastly an empty line always ends the frame dump.
//
// NOTE: frame dumps are implemented incrementally to not write too much data
// to the file.
// IOW let's say we dumped 2 frames with full format as follows:
// ---I
// b:30
// f:40
// a0:1
// a1:2
//
// ---I
// b:35
// f:40
// a0:1
// a1:1
//
// In order to save space a file will contain just:
// ---I
// b:30
// f:40
// a0:1
// a1:2
//
// ---I
// b:35
// a1:1

import Foundation

// This class is implementing one public function relate(optimizedOutput, unoptimizedOutput).
// `relate` compares optimizedOutput and unoptimizedOutput for equality.
public final class DiffOracle {
    private enum FrameType {
        case interpreter
        case sparkplug
        case maglev
        case turbofan
        case deoptTurbofan
    }

    private struct Frame: Equatable, CustomStringConvertible {
        let bytecodeOffset: Int
        let accumulator: String
        let arguments: [String]
        let registers: [String]
        let functionId: Int
        let frameType: FrameType

        // This is only needed for debug output.
        var description: String {
            var lines: [String] = []

            lines.append("")
            lines.append("╔══════════════════════════════════════")
            lines.append("║ Frame: \(frameType) (FuncID: \(functionId), Offset: \(bytecodeOffset))")
            lines.append("╠══════════════════════════════════════")
            lines.append("║ Accumulator: \(accumulator)")

            if arguments.isEmpty {
                lines.append("║ Arguments: []")
            } else {
                lines.append("║ Arguments:")
                for (index, arg) in arguments.enumerated() {
                    lines.append("║   [\(index)] \(arg)")
                }
            }

            if registers.isEmpty {
                lines.append("║ Registers: []")
            } else {
                lines.append("║ Registers:")
                for (index, reg) in registers.enumerated() {
                    lines.append("║   [\(index)] \(reg)")
                }
            }

            lines.append("╚══════════════════════════════════════")

            return lines.joined(separator: "\n")
        }

        // 'reference' is the value from Unoptimized frame.
        func matches(reference: Frame) -> Bool {

            guard self.bytecodeOffset == reference.bytecodeOffset,
                self.functionId == reference.functionId,
                self.arguments.count == reference.arguments.count,
                self.registers.count == reference.registers.count
            else {
                return false
            }

            // Logic: 'self' is the Optimized frame. It is allowed to have "<optimized_out>" or "<non-materialized>".
            func isMatch(_ optValue: String, unoptValue: String) -> Bool {
                return optValue == "<optimized_out>" || optValue == "<non-materialized>"
                    || optValue == unoptValue
            }

            if !isMatch(self.accumulator, unoptValue: reference.accumulator) {
                return false
            }

            if !zip(self.arguments, reference.arguments).allSatisfy(isMatch) {
                return false
            }

            if !zip(self.registers, reference.registers).allSatisfy(isMatch) {
                return false
            }

            return true
        }
    }

    private static func parseDiffFrame(
        _ frameArr: ArraySlice<Substring>,
        _ prevFrame: Frame?,
        _ runningArgs: inout [String],
        _ runningRegs: inout [String]
    ) -> Frame {

        func parseValue<T>(
            prefix: String, defaultValue: T, index: inout Int, conversion: (Substring) -> T
        ) -> T {
            if index < frameArr.endIndex && frameArr[index].starts(with: prefix) {
                let value = conversion(frameArr[index].dropFirst(prefix.count))
                index += 1
                return value
            }
            return defaultValue
        }
        var i = frameArr.startIndex
        func parseFrameType(_ type: Substring) -> FrameType {
            switch type {
            case "---I": .interpreter
            case "---S": .sparkplug
            case "---M": .maglev
            case "---T": .turbofan
            case "---D": .deoptTurbofan
            default:
                fatalError("Unknown frame type")
            }
        }

        let frameType: FrameType = parseFrameType(frameArr[i])

        i += 1

        let bytecodeOffset = parseValue(
            prefix: "b:", defaultValue: prevFrame?.bytecodeOffset ?? -1, index: &i
        ) { Int($0)! }
        let functionId = parseValue(
            prefix: "f:", defaultValue: prevFrame?.functionId ?? -1, index: &i
        ) { Int($0)! }
        let accumulator = parseValue(
            prefix: "x:", defaultValue: prevFrame?.accumulator ?? "", index: &i
        ) { String($0) }
        let argCount = parseValue(
            prefix: "n:", defaultValue: prevFrame?.arguments.count ?? -1, index: &i
        ) { Int($0)! }
        let regCount = parseValue(
            prefix: "m:", defaultValue: prevFrame?.registers.count ?? -1, index: &i
        ) { Int($0)! }

        func updateValues(prefix: String, requiredCount: Int, buffer: inout [String]) -> [String] {

            if buffer.count < requiredCount {
                let missingCount = requiredCount - buffer.count
                let defaults = Array(repeating: "<missing>", count: missingCount)
                buffer.append(contentsOf: defaults)
            }

            while i < frameArr.endIndex && frameArr[i].starts(with: prefix) {
                let data = frameArr[i].dropFirst(1).split(
                    separator: ":", maxSplits: 1, omittingEmptySubsequences: false)
                let number = Int(data[0])!
                let value = String(data[1])

                buffer[number] = value
                i += 1
            }

            return Array(buffer.prefix(requiredCount))
        }

        let arguments = updateValues(prefix: "a", requiredCount: argCount, buffer: &runningArgs)
        let registers = updateValues(prefix: "r", requiredCount: regCount, buffer: &runningRegs)

        let frame = Frame(
            bytecodeOffset: bytecodeOffset,
            accumulator: accumulator,
            arguments: arguments,
            registers: registers,
            functionId: functionId,
            frameType: frameType)
        return frame
    }

    private static func parseFullFrames(_ stdout: String) -> [Frame] {
        var frameArray: [Frame] = []
        var prevFrame: Frame? = nil

        var runningArgs: [String] = []
        var runningRegs: [String] = []

        let split = stdout.split(separator: "\n", omittingEmptySubsequences: false)
        let frames = split.split(separator: "")

        for frame in frames {
            assert(
                frame.first?.starts(with: "---") == true,
                "Invalid frame header found: \(frame.first ?? "nil")")

            prevFrame = parseDiffFrame(frame, prevFrame, &runningArgs, &runningRegs)
            frameArray.append(prevFrame!)
        }
        return frameArray
    }

    public static func relate(_ optIn: String, with unoptIn: String) -> Bool {
        let optFrames = parseFullFrames(optIn)
        let unoptFrames = parseFullFrames(unoptIn)
        var unoptFramesLeft = ArraySlice(unoptFrames)

        for optFrame in optFrames {
            guard let unoptIndex = unoptFramesLeft.firstIndex(where: optFrame.matches) else {
                print(optFrame)
                print("--------------------------")
                print("[")
                for unoptFrame in unoptFrames {
                    if unoptFrame.bytecodeOffset == optFrame.bytecodeOffset
                        && unoptFrame.functionId == optFrame.functionId
                    {
                        print(unoptFrame)
                    }
                }
                print("]")
                return false
            }
            // Remove all skipped frames but keep the found frame, because we might have multiple deopt points on same offset.
            unoptFramesLeft = unoptFramesLeft[unoptIndex...]
        }
        return true
    }
}
