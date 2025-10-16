// Copyright 2019 Google LLC
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

public struct V8DifferentialConfig {
    public let commonArgs: [String] = [
        "--expose-gc",
        "--omit-quit",
        "--allow-natives-for-differential-fuzzing",
        "--fuzzing",
        "--future",
        "--harmony",
        "--predictable",
        "--trace",
        "--correctness-fuzzer-suppressions",
        "--no-lazy-feedback-allocation",
    ]

    public let differentialArgs: [String] = [
        "--turbofan-dumping",
        "--generate-dump-positions",
        // "--verify-heap-on-jit-dump",
        "--turbofan-dumping-print-deopt-frames",
        "--jit-return-dump",
        "--maglev-dumping",
        "--no-sparkplug",
        "--jit-fuzzing",
    ]

    public let referenceArgs: [String] = ["--no-turbofan", "--no-maglev", "--load-dump-positions", "--sparkplug-dumping", "--interpreter-dumping"]

    public init() {}
}


public struct Configuration {
    /// The commandline arguments used by this instance.
    public let arguments: [String]

    /// Timeout in milliseconds after which child processes will be killed.
    public let timeout: UInt32

    /// Log level to use.
    public let logLevel: LogLevel

    /// Code snippets that are be executed during startup and then checked to lead to the expected result.
    ///
    /// These can for example be used to:
    ///   - Check that (dummy) crashes are detected correctly (with `.shouldCrash`)
    ///   - Check that certain features or builtins exist (with `.shouldSucceed`)
    ///   - Check that known-safe crashes are ignored (with `.shouldNotCrash`)
    public let startupTests: [(String, ExpectedStartupTestResult)]

    /// The fraction of instruction to keep from the original program when minimizing.
    /// This setting is useful to avoid "over-minimization", which can negatively impact the fuzzer's
    /// performance if program features are removed that could later be mutated to trigger new
    /// interesting behaviour or crashes.
    /// See Minimizer.swift for the exact algorithm used to implement this.
    public let minimizationLimit: Double

    /// When receiving programs from another node during distributed fuzzing, discard this percentage of samples.
    ///
    /// Dropout can provide a way to make multiple instances less "similar" to each
    /// other as it forces them to (re)discover edges in a different way.
    public let dropoutRate: Double

    /// Enable the saving of programs that failed or timed-out during execution.
    public let enableDiagnostics: Bool

    /// Whether to enable inspection for generated programs. If enabled, a full record
    /// of the steps that led to a particular program will be kept. In particular, a programs
    /// ancestor chain (the programs that were mutated to arrive at the current program)
    /// is recorded as well as the exact list of mutations and code generations, as well
    /// as the reductions performed by the minimizer.
    public let enableInspection: Bool

    /// Determines if we want to have a static corpus, i.e. we don't add any
    /// programs to the corpus even if they find new coverage.
    public let staticCorpus: Bool

    /// Additional string that will be stored in the settings.json file and
    /// also appended as a comment in the footer of crashing samples.
    public let tag: String?

    /// The path to the relate tool.
    public let relateToolPath: String?

    /// The depth of the dumpling tree.
    public let dumplingDepth: UInt32

    /// The number of properties to sample for each dumpling node.
    public let dumplingPropCount: UInt32

    // Whether the fuzzer is running with wasm features or without. If false,
    // this disables all wasm-related code generators.
    public let isWasmEnabled: Bool

    // The directory in which the corpus and additional diagnostics files are stored.
    public let storagePath: String?

    // Advises the fuzzer to generate cases that are more suitable for differential fuzzing.
    // Right now this only leads to the JavaScriptLifter emitting more local variables which
    // differential fuzzers can inspect (via mutating the JS program to print defined variables).
    public let forDifferentialFuzzing: Bool

    /// Code snippets that cause an observable difference of output
    /// in the target engine. Used to verify that crashes can be detected.
    public let differentialTests: [String]

    /// Code snippets that must not cause an observable difference of output
    /// in the target engine. Used to verify that common sources of
    /// entropy (Math.random, ...) are deterministic.
    public let differentialTestsInvariant: [String]

    // The subdirectory in {config.storagePath} at which all programs are stored which could not
    // be imported due to disabled wasm capabilities in the fuzzer.
    public static let excludedWasmDirectory = "excluded_wasm_programs"

    public init(arguments: [String] = [],
                timeout: UInt32 = 250,
                skipStartupTests: Bool = false,
                logLevel: LogLevel = .info,
                differentialTests: [String] = [],
                differentialTestsInvariant: [String] = [],
                startupTests: [(String, ExpectedStartupTestResult)] = [],
                minimizationLimit: Double = 0.0,
                dropoutRate: Double = 0,
                collectRuntimeTypes: Bool = false,
                enableDiagnostics: Bool = false,
                enableInspection: Bool = false,
                staticCorpus: Bool = false,
                tag: String? = nil,
                relateToolPath: String? = nil,
                dumplingDepth: UInt32 = 3,
                dumplingPropCount: UInt32 = 5,
                isWasmEnabled: Bool = false,
                storagePath: String? = nil,
                forDifferentialFuzzing: Bool = false) {
        self.arguments = arguments
        self.timeout = timeout
        self.logLevel = logLevel
        self.startupTests = startupTests
        self.dropoutRate = dropoutRate
        self.minimizationLimit = minimizationLimit
        self.enableDiagnostics = enableDiagnostics
        // If we have enabledDiagnostics we should also enable inspection.
        self.enableInspection = enableDiagnostics || enableInspection
        self.staticCorpus = staticCorpus
        self.tag = tag
        self.isWasmEnabled = isWasmEnabled
        self.storagePath = storagePath
        self.forDifferentialFuzzing = forDifferentialFuzzing
        self.differentialTests = differentialTests
        self.differentialTestsInvariant = differentialTestsInvariant
        self.relateToolPath = relateToolPath
        self.dumplingDepth = dumplingDepth
        self.dumplingPropCount = dumplingPropCount
    }
}

public enum ExpectedStartupTestResult {
    case shouldSucceed
    case shouldCrash
    case shouldNotCrash
}

public struct InspectionOptions: OptionSet {
    public let rawValue: Int
    public init(rawValue: Int) {
        self.rawValue = rawValue
    }

    // When writing programs to disk, their "history", describing in detail
    // how the program was generated through mutations, code generation, and
    // minimization, is included in .fuzzil.history files.
    public static let history = InspectionOptions(rawValue: 1 << 0)

    public static let all = InspectionOptions([.history])
}
