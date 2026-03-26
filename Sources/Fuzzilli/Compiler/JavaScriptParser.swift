// Copyright 2023 Google LLC
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

import Foundation

/// Parses JavaScript code into an AST.
///
/// Frontent to the node.js/babel based parser in the Parser/ subdirectory.
public class JavaScriptParser {
    public typealias AST = Compiler_Protobuf_AST

    /// The JavaScriptExecutor executable wrapper that we are using to run the parse.js script.
    public let executor: JavaScriptExecutor

    // Simple error enum for errors that are displayed to the user.
    public enum ParserError: Error {
        case parsingFailed(String)
    }

    /// The path to the parse.js script that implements the actual parsing using babel.js.
    private let parserScriptPath: String

    static func parserScriptPathForTesting() -> String? {
        locateResource(
            named: "parser",
            withExtension: "js",
            preferredDirectories: ["Parser", "Compiler/Parser", ""]
        )
    }

    static func astProtobufDefinitionPathForTesting() -> String? {
        locateResource(
            named: "ast",
            withExtension: "proto",
            preferredDirectories: ["Protobuf", ""]
        )
    }

    public init?(executor: JavaScriptExecutor) {
        self.executor = executor

        // This will only work if the executor is node as we will need to use node modules.
        guard let parserScriptPath = Self.parserScriptPathForTesting() else {
            return nil
        }
        self.parserScriptPath = parserScriptPath

        // Check if the parser works. If not, it's likely because its node.js dependencies have not been installed.
        do {
            try runParserScript(withArguments: [])
        } catch {
            return nil
        }
    }

    public func parse(_ path: String) throws -> AST {
        guard let astProtobufDefinitionPath = Self.astProtobufDefinitionPathForTesting() else {
            throw ParserError.parsingFailed("Could not locate ast.proto in the Fuzzilli module bundle")
        }
        let outputFilePath = FileManager.default.temporaryDirectory.path + "/" + UUID().uuidString + ".ast.proto"
        try runParserScript(withArguments: [astProtobufDefinitionPath, path, outputFilePath])
        let data = try Data(contentsOf: URL(fileURLWithPath: outputFilePath))
        try FileManager.default.removeItem(atPath: outputFilePath)
        return try AST(serializedBytes: data)
    }

    private func runParserScript(withArguments arguments: [String]) throws {
        let output = Pipe()
        let task = Process()
        // Don't set standardOutput: we only need stderr for error reporting and
        // capturing stdout here may cause a deadlock if the pipe becomes full.
        task.standardOutput = FileHandle.nullDevice
        task.standardError = output
        task.arguments = [parserScriptPath] + arguments
        task.currentDirectoryURL = URL(fileURLWithPath: parserScriptPath).deletingLastPathComponent()
        // TODO: move this method into the NodeJS class instead of manually invoking the node.js binary here
        task.executableURL = URL(fileURLWithPath: executor.executablePath)
        try task.run()
        task.waitUntilExit()

        let data = output.fileHandleForReading.readDataToEndOfFile()
        guard task.terminationStatus == 0 else {
            throw ParserError.parsingFailed(String(data: data, encoding: .utf8)!)
        }
    }

    private static func locateResource(named name: String, withExtension ext: String, preferredDirectories: [String]) -> String? {
        let bundle = Bundle.module

        for directory in preferredDirectories {
            let normalizedDirectory = directory.isEmpty ? nil : directory
            if let path = bundle.path(forResource: name, ofType: ext, inDirectory: normalizedDirectory) {
                return path
            }
        }

        guard let resourceURL = bundle.resourceURL else {
            return nil
        }

        let fm = FileManager.default
        for directory in preferredDirectories {
            let candidateURL = directory.isEmpty
                ? resourceURL.appendingPathComponent("\(name).\(ext)")
                : resourceURL.appendingPathComponent(directory).appendingPathComponent("\(name).\(ext)")
            if fm.fileExists(atPath: candidateURL.path) {
                return candidateURL.path
            }
        }

        return nil
    }

}
