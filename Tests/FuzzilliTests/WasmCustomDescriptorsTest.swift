import Algorithms
import Foundation
import Testing

@testable import Fuzzilli

@Suite(.enabled { JavaScriptExecutor() != nil })
struct WasmCustomDescriptorsTests {
    @Test func testDescriptorAndDescribes() throws {
        let runner = JavaScriptExecutor(withArguments: ["--wasm-custom-descriptors"])!
        let jsProg = buildAndLiftProgram { b in
            let types = b.wasmDefineTypeGroup {
                let described = b.wasmDefineStructType(
                    fields: [WasmStructTypeDescription.Field(type: .wasmi32, mutability: true)],
                    indexTypes: []
                )
                let descriptor = b.wasmDefineStructType(
                    fields: [WasmStructTypeDescription.Field(type: .wasmi32, mutability: true)],
                    indexTypes: [],
                    describes: described
                )
                return [described, descriptor]
            }

            let module = b.buildWasmModule { wasmModule in
                let _ = wasmModule.addWasmFunction(with: [] => []) { function, _, _ in
                    for type in types {
                        _ = function.wasmRefNull(typeDef: type)
                    }
                    return []
                }
            }
            let _ = module.loadExports()
        }
        testForOutput(program: jsProg, runner: runner, outputString: "")
    }

    @Test func testSubtypeValidation() throws {
        let runner = JavaScriptExecutor(withArguments: ["--wasm-custom-descriptors"])!
        let jsProg = buildAndLiftProgram { b in
            let types = b.wasmDefineTypeGroup {
                let described = b.wasmDefineStructType(
                    fields: [WasmStructTypeDescription.Field(type: .wasmi32, mutability: true)],
                    indexTypes: []
                )
                let descriptor = b.wasmDefineStructType(
                    fields: [WasmStructTypeDescription.Field(type: .wasmi32, mutability: true)],
                    indexTypes: [],
                    describes: described
                )

                let describedSub = b.wasmDefineStructType(
                    fields: [
                        WasmStructTypeDescription.Field(type: .wasmi32, mutability: true),
                        WasmStructTypeDescription.Field(type: .wasmf64, mutability: false),
                    ],
                    indexTypes: [],
                    superTypeDef: described
                )
                let descriptorSub = b.wasmDefineStructType(
                    fields: [
                        WasmStructTypeDescription.Field(type: .wasmi32, mutability: true),
                        WasmStructTypeDescription.Field(type: .wasmf64, mutability: false),
                    ],
                    indexTypes: [],
                    superTypeDef: descriptor,
                    describes: describedSub
                )
                return [described, descriptor, describedSub, descriptorSub]
            }

            let module = b.buildWasmModule { wasmModule in
                let _ = wasmModule.addWasmFunction(with: [] => []) { function, _, _ in
                    for type in types {
                        _ = function.wasmRefNull(typeDef: type)
                    }
                    return []
                }
            }
            let _ = module.loadExports()
        }
        testForOutput(program: jsProg, runner: runner, outputString: "")
    }

    @Test func testGenerateSubtypeOnDescribed() throws {
        let runner = JavaScriptExecutor(withArguments: ["--wasm-custom-descriptors"])!
        let jsProg = buildAndLiftProgram { b in
            let types = b.wasmDefineTypeGroup {
                let described = b.wasmDefineStructType(
                    fields: [WasmStructTypeDescription.Field(type: .wasmi32, mutability: true)],
                    indexTypes: []
                )
                let descriptor = b.wasmDefineStructType(
                    fields: [WasmStructTypeDescription.Field(type: .wasmi32, mutability: true)],
                    indexTypes: [],
                    describes: described
                )

                let describedSub = b.generateSubtype(for: described)
                return [described, descriptor, describedSub]
            }

            let module = b.buildWasmModule { wasmModule in
                let _ = wasmModule.addWasmFunction(with: [] => []) { function, _, _ in
                    for type in types {
                        _ = function.wasmRefNull(typeDef: type)
                    }
                    return []
                }
            }
            let _ = module.loadExports()
        }
        testForOutput(program: jsProg, runner: runner, outputString: "")
    }

    @Test func testGenerateSubtypeOnDescriptor() throws {
        let runner = JavaScriptExecutor(withArguments: ["--wasm-custom-descriptors"])!
        let jsProg = buildAndLiftProgram { b in
            let types = b.wasmDefineTypeGroup {
                let described = b.wasmDefineStructType(
                    fields: [WasmStructTypeDescription.Field(type: .wasmi32, mutability: true)],
                    indexTypes: []
                )
                let descriptor = b.wasmDefineStructType(
                    fields: [WasmStructTypeDescription.Field(type: .wasmi32, mutability: true)],
                    indexTypes: [],
                    describes: described
                )

                let descriptorSub = b.generateSubtype(for: descriptor)
                return [described, descriptor, descriptorSub]
            }

            let module = b.buildWasmModule { wasmModule in
                let _ = wasmModule.addWasmFunction(with: [] => []) { function, _, _ in
                    for type in types {
                        _ = function.wasmRefNull(typeDef: type)
                    }
                    return []
                }
            }
            let _ = module.loadExports()
        }
        testForOutput(program: jsProg, runner: runner, outputString: "")
    }
}
