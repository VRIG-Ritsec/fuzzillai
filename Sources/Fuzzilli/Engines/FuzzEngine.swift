// Copyright 2020 Google LLC
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

public class FuzzEngine: ComponentBase {
    private(set) var postProcessor: FuzzingPostProcessor? = nil

    override init(name: String) {
        super.init(name: name)
    }

    // Install a post-processor that is executed for every generated program and can modify it (by returning a different program).
    public func registerPostProcessor(_ postProcessor: FuzzingPostProcessor) {
        assert(self.postProcessor == nil)
        self.postProcessor = postProcessor
    }

    // Performs a single round of fuzzing using the engine.
    public func fuzzOne(_ group: DispatchGroup) {
        fatalError("Must be implemented by child classes")
    }

}

extension FuzzEngine {
    public func execute(_ program: Program, withTimeout timeout: UInt32? = nil) -> ExecutionOutcome {
        let program = postProcessor?.process(program, for: fuzzer) ?? program

        fuzzer.dispatchEvent(fuzzer.events.ProgramGenerated, data: program)

        let postprocessedProgram: Program
        postprocessedProgram = program

        let execution = fuzzer.execute(postprocessedProgram, withTimeout: timeout, purpose: .fuzzing, differentialExecute: .force)

        switch execution.outcome {
            case .crashed(let termsig):
                fuzzer.processCrash(postprocessedProgram, withSignal: termsig, withStderr: execution.stderr, withStdout: execution.stdout, origin: .local, withExectime: execution.execTime)
                postprocessedProgram.contributors.generatedCrashingSample()

            case .differential:
            fuzzer.processDifferential(postprocessedProgram, withStderr: execution.stderr, withStdout: execution.unOptStdout!, origin: .local, optStdout: execution.optStdout!)
                postprocessedProgram.contributors.generatedDifferentialSample()

            case .succeeded:
                fuzzer.dispatchEvent(fuzzer.events.ValidProgramFound, data: program)
                var isInteresting = false
                if let aspects = fuzzer.evaluator.evaluate(execution) {
                    if fuzzer.config.enableInspection {
                        program.comments.add("Program may be interesting due to \(aspects)", at: .footer)
                        program.comments.add("RUNNER ARGS: \(fuzzer.runner.processArguments.joined(separator: " "))", at: .header)
                    }
                    isInteresting = fuzzer.processMaybeInteresting(program, havingAspects: aspects, origin: .local)
                }

                if isInteresting {
                    program.contributors.generatedInterestingSample()
                } else {
                    program.contributors.generatedValidSample()
                }

            case .failed(_):
                if fuzzer.config.enableDiagnostics {
                    postprocessedProgram.comments.add("Stdout:\n" + execution.stdout, at: .footer)
                }
                fuzzer.dispatchEvent(fuzzer.events.InvalidProgramFound, data: postprocessedProgram)
                postprocessedProgram.contributors.generatedInvalidSample()

            case .timedOut:
                fuzzer.dispatchEvent(fuzzer.events.TimeOutFound, data: postprocessedProgram)
                postprocessedProgram.contributors.generatedTimeOutSample()
        }

        if fuzzer.config.enableDiagnostics {
            // Ensure deterministic execution behaviour. This can for example help detect and debug REPRL issues.
            ensureDeterministicExecutionOutcomeForDiagnostic(of: postprocessedProgram)
        }

        return execution.outcome
    }

    private final func ensureDeterministicExecutionOutcomeForDiagnostic(of program: Program) {
        let execution1 = fuzzer.execute(program, purpose: .other)
        let stdout1 = execution1.stdout, stderr1 = execution1.stderr
        let execution2 = fuzzer.execute(program, purpose: .other)
        switch (execution1.outcome, execution2.outcome) {
        case (.succeeded, .failed(_)),
             (.failed(_), .succeeded):
            let stdout2 = execution2.stdout, stderr2 = execution2.stderr
            logger.warning("""
                Non-deterministic execution detected for program
                \(fuzzer.lifter.lift(program))
                // Stdout of first execution
                \(stdout1)
                // Stderr of first execution
                \(stderr1)
                // Stdout of second execution
                \(stdout2)
                // Stderr of second execution
                \(stderr2)
                """)
        default:
            break
        }
    }
}
