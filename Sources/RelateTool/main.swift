import Foundation
import Fuzzilli


func printRunningCmd(_ process: Process) {
    print("Running command: \(process.executableURL!.path) \(process.arguments!.joined(separator: " "))")
}

//
// Process commandline arguments.
//
let args = Arguments.parse(from: CommandLine.arguments)

let jsShellPath = args["--d8"] ?? "/root/localTools/v8/out/fuzzbuild/d8"
var poc = args["--poc"]!

let logLevel: OracleLogLevel
if args["--logLevel"] == nil || args["--logLevel"] == "info" {
    logLevel = .info
} else if args["--logLevel"] == "debug" {
    logLevel = .debug
} else {
    logLevel = .none
}

let validate = args["--validate"]

let usePrepend = args["--prepend"]

let progOutput = args["--progOutput"]

if usePrepend != nil {
    var prependPath = URL(fileURLWithPath: #file)
    for _ in 0...2 {
        prependPath.deleteLastPathComponent()
    }
    prependPath = prependPath.appendingPathComponent("prepend.js")
    print(prependPath.path)
    if !FileManager.default.fileExists(atPath: prependPath.path) {
        print("prepend.js not found!")
        exit(-1)
    }

    let prependJS = try! String(contentsOfFile: prependPath.path)

    let filename = "prepend_" + URL(fileURLWithPath: poc).lastPathComponent
    let fileURL = URL(fileURLWithPath: "/tmp").appendingPathComponent(filename)

    let prependedScript = prependJS + (try! String(contentsOfFile: poc))

    do {
        try prependedScript.write(to: fileURL, atomically: false, encoding: String.Encoding.utf8)
    } catch {
        print("Failed to write file \(fileURL): \(error)")
        exit(-1)
    }

    poc = fileURL.path
}

let optRun = Process()

let optOutput = Pipe()
optRun.standardOutput = optOutput
optRun.standardError = optOutput


let config = V8DifferentialConfig()

var g = SystemRandomNumberGenerator()
let differentialFuzzingDumpSeed = UInt32(Int.random(in: 1...9999, using: &g));

optRun.executableURL = URL(fileURLWithPath: jsShellPath)
optRun.arguments = config.commonArgs + config.differentialArgs + ["--dumping-seed=\(differentialFuzzingDumpSeed)", poc]

printRunningCmd(optRun)
try! optRun.run()
optRun.waitUntilExit()
if progOutput != nil {
    print(String(data: optOutput.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8)!)
}
let optDumps = try? String(contentsOfFile: "/tmp/\(differentialFuzzingDumpSeed)_output_dump.txt", encoding: .utf8)
if optDumps == nil {
    if validate == nil {
        print("Produced no opt Dumps")
    } else {
        exit(1)
    }
} else {
    let unOptRun = Process()
    let unOptOutput = Pipe()
    unOptRun.standardOutput = unOptOutput
    unOptRun.standardError = unOptOutput
    unOptRun.executableURL = URL(fileURLWithPath: jsShellPath)
    unOptRun.arguments = config.commonArgs + config.referenceArgs + ["--dumping-seed=\(differentialFuzzingDumpSeed)", poc]
    printRunningCmd(unOptRun)
    try! unOptRun.run()
    unOptRun.waitUntilExit()
    if progOutput != nil {
        print(String(data: unOptOutput.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8)!)
    }
    let unOptDumps = try! String(contentsOfFile: "/tmp/\(differentialFuzzingDumpSeed)_output_dump.txt", encoding: .utf8)
    let result = relate(optDumps!, with: unOptDumps, logLevel)

    if (usePrepend != nil) {
        try? FileManager.default.removeItem(atPath: poc)
    }

    if validate == nil {
        print(result)
    } else if !result {
        exit(1)
    }
}
