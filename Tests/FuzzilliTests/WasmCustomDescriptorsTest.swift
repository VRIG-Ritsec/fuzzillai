import Algorithms
import Foundation
import Testing

@testable import Fuzzilli

@Suite(.enabled { JavaScriptExecutor() != nil })
struct WasmCustomDescriptorsTests {
    @Test func testDescriptorAndDescribes() throws {
        let runner = JavaScriptExecutor(withArguments: ["--wasm-custom-descriptors"])!
        let jsProg = buildAndLiftProgram { b in
            _ = b.wasmDefineTypeGroup(recursiveGenerator: {
                let v2 = b.wasmDefineStructType(
                    fields: [WasmStructTypeDescription.Field(type: .wasmi32, mutability: true)],
                    indexTypes: []
                )
                _ = b.wasmDefineStructType(
                    fields: [WasmStructTypeDescription.Field(type: .wasmi32, mutability: true)],
                    indexTypes: [],
                    describes: v2
                )
            })

            let module = b.buildWasmModule { wasmModule in
                let _ = wasmModule.addWasmFunction(with: [] => []) { function, _, _ in
                    return []
                }
            }
            let _ = module.loadExports()
        }
        testForOutput(program: jsProg, runner: runner, outputString: "")
    }
}
