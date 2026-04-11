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

import XCTest

@testable import Fuzzilli

class RuntimeAssistedMutatorTests: XCTestCase {
    class CrashMockScriptRunner: ScriptRunner {
        var processArguments: [String] = []
        var env: [(String, String)] = []
        func run(_ script: String, withTimeout timeout: UInt32) -> Execution {
            // Minimization can change quotation marks. Checking for both here to be on the safe side.
            if script.contains("fuzzilli('FUZZILLI_CRASH', 0)")
                || script.contains("fuzzilli(\"FUZZILLI_CRASH\", 0)")
            {
                return MockExecution(
                    outcome: .crashed(9), stdout: "", stderr: "", fuzzout: "", execTime: 0.1)
            } else {
                return MockExecution(
                    outcome: .succeeded, stdout: "", stderr: "", fuzzout: "", execTime: 0.1)
            }
        }
        func setEnvironmentVariable(_ key: String, to value: String) {}
        func initialize(with fuzzer: Fuzzer) {}
        var isInitialized: Bool { true }
    }

    // This test checks that if *both* the instrumented and the processed programs crash,
    // we report the processed program.

    func testInstrumentedAndProcessedProgramsCrash() {
        let runner = CrashMockScriptRunner()
        let config = Configuration(logLevel: .error)
        let fuzzer = makeMockFuzzer(config: config, runner: runner)

        let mutator = CrashingInstrumentationMutator()
        let b = fuzzer.makeBuilder()
        b.loadInt(42)
        let program = b.finalize()

        let crashEventTriggered = self.expectation(
            description: "Crash reported on processed program")
        fuzzer.registerEventListener(for: fuzzer.events.CrashFound) { ev in
            let lifted = fuzzer.lifter.lift(ev.program)
            if lifted.contains("This is the processed program") {
                crashEventTriggered.fulfill()
            }
        }

        _ = mutator.mutate(program, using: b, for: fuzzer)
        waitForExpectations(timeout: 5, handler: nil)
    }

    // This test checks that if *only* the instrumented program crashes, we report it.

    func testOnlyInstrumentedProgramCrashes() {
        let runner = CrashMockScriptRunner()
        let config = Configuration(logLevel: .error)
        let fuzzer = makeMockFuzzer(config: config, runner: runner)

        let mutator = CrashingInstrumentationMutator(shouldProcessedProgramCrash: false)
        let b = fuzzer.makeBuilder()
        b.loadInt(42)
        let program = b.finalize()

        let crashEventTriggered = self.expectation(
            description: "Crash reported on instrumented program")
        fuzzer.registerEventListener(for: fuzzer.events.CrashFound) { ev in
            let lifted = fuzzer.lifter.lift(ev.program)
            if !lifted.contains("This is the processed program") {
                crashEventTriggered.fulfill()
            }
        }

        _ = mutator.mutate(program, using: b, for: fuzzer)
        waitForExpectations(timeout: 5, handler: nil)
    }

    // This test checks that if *only* the instrumented program crashes, this program is minimized.

    func testOnlyInstrumentedProgramCrashesAndIsMinimized() throws {
        guard
            let nodejs = JavaScriptExecutor(
                type: .nodejs, withArguments: ["--allow-natives-syntax"])
        else {
            throw XCTSkip(
                "Could not find NodeJS executable. See Sources/Fuzzilli/Compiler/Parser/README.md for details on how to set up the parser."
            )
        }

        // Initialize the parser. This can fail if no node.js executable is found or if the
        // parser's node.js dependencies are not installed. In that case, skip these tests.
        guard JavaScriptParser(executor: nodejs) != nil else {
            throw XCTSkip(
                "The JavaScript parser does not appear to be working. See Sources/Fuzzilli/Compiler/Parser/README.md for details on how to set up the parser."
            )
        }

        class CrashEvaluator: MockEvaluator {
            override func hasAspects(_ execution: Execution, _ aspects: ProgramAspects) -> Bool {
                return execution.outcome.isCrash()
            }
        }

        let runner = CrashMockScriptRunner()
        let config = Configuration(logLevel: .error)
        let fuzzer = makeMockFuzzer(config: config, runner: runner, evaluator: CrashEvaluator())

        let expectedProgramBuilder = fuzzer.makeBuilder()
        let v0 = expectedProgramBuilder.loadString("FUZZILLI_CRASH")
        let v1 = expectedProgramBuilder.loadInt(0)
        let v2 = expectedProgramBuilder.createNamedVariable(forBuiltin: "fuzzilli")
        expectedProgramBuilder.callFunction(v2, withArgs: [v0, v1])
        let expectedProgram = expectedProgramBuilder.finalize()

        let mutator = CrashingInstrumentationMutator(shouldProcessedProgramCrash: false)
        let b = fuzzer.makeBuilder()
        b.loadInt(42)
        let program = b.finalize()

        let crashEventTriggered = self.expectation(
            description: "Crash reported on instrumented program")
        fuzzer.registerEventListener(for: fuzzer.events.CrashFound) { ev in
            let actualProgram = ev.program
            XCTAssertEqual(
                expectedProgram, actualProgram,
                "The reported program should be the minimized version of the instrumented program.\n"
                    + "Expected:\n\(FuzzILLifter().lift(expectedProgram.code))\n\n"
                    + "Actual:\n\(FuzzILLifter().lift(actualProgram.code))")
            crashEventTriggered.fulfill()
        }

        _ = mutator.mutate(program, using: b, for: fuzzer)
        waitForExpectations(timeout: 5, handler: nil)
    }

}
