import Algorithms
import Foundation
import Testing

@testable import Fuzzilli

@Suite(.enabled { JavaScriptExecutor() != nil })
struct WasmCustomDescriptorsTests {
    let config = Configuration(
        logLevel: .error, enableInspection: true, enableCustomDescriptors: true)

    @Test func testDescriptorAndDescribes() throws {
        let runner = JavaScriptExecutor(withArguments: ["--wasm-custom-descriptors"])!
        let jsProg = buildAndLiftProgram(config: config) { b in
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
        let jsProg = buildAndLiftProgram(config: config) { b in
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
        let jsProg = buildAndLiftProgram(config: config) { b in
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
        let jsProg = buildAndLiftProgram(config: config) { b in
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

    @Test func testExactTypeOutputs() throws {
        let runner = JavaScriptExecutor(withArguments: ["--wasm-custom-descriptors"])!
        let jsProg = buildAndLiftProgram(config: config) { b in
            let types = b.wasmDefineTypeGroup {
                let structType = b.wasmDefineStructType(
                    fields: [WasmStructTypeDescription.Field(type: .wasmi32, mutability: true)],
                    indexTypes: []
                )
                let arrayType = b.wasmDefineArrayType(
                    elementType: .wasmi32,
                    mutability: true
                )
                return [structType, arrayType]
            }
            let structType = types[0]
            let arrayType = types[1]

            let module = b.buildWasmModule { wasmModule in
                let funcRef = wasmModule.addWasmFunction(with: [] => []) { function, _, _ in
                    let i32 = function.consti32(42)

                    let structInst = function.wasmStructNew(structType: structType, fields: [i32])
                    #expect(b.type(of: structInst).wasmReferenceType?.kind.isExact == true)

                    let structInstDefault = function.wasmStructNewDefault(structType: structType)
                    #expect(b.type(of: structInstDefault).wasmReferenceType?.kind.isExact == true)

                    let nonNullRef = function.wasmRefAsNonNull(structInstDefault)
                    #expect(b.type(of: nonNullRef).wasmReferenceType?.kind.isExact == true)

                    let arrayInstFixed = function.wasmArrayNewFixed(
                        arrayType: arrayType, elements: [i32, i32])
                    #expect(b.type(of: arrayInstFixed).wasmReferenceType?.kind.isExact == true)

                    let arrayInstDefault = function.wasmArrayNewDefault(
                        arrayType: arrayType, size: i32)
                    #expect(b.type(of: arrayInstDefault).wasmReferenceType?.kind.isExact == true)

                    return []
                }

                let _ = wasmModule.addWasmFunction(with: [] => []) { function, _, _ in
                    let ref = function.wasmRefFunc(funcRef)
                    #expect(b.type(of: ref).wasmReferenceType?.kind.isExact == true)

                    let nullRef = function.wasmRefNull(typeDef: structType)
                    #expect(b.type(of: nullRef).wasmReferenceType?.kind.isExact == true)

                    return []
                }
            }
            let _ = module.loadExports()
        }
        testForOutput(program: jsProg, runner: runner, outputString: "")
    }

    @Test func testGlobalIndexExactTyped() throws {
        let runner = JavaScriptExecutor(withArguments: ["--wasm-custom-descriptors"])!
        let liveTestConfig = Configuration(
            logLevel: .error, enableInspection: true, enableCustomDescriptors: true)

        let fuzzer = makeMockFuzzer(config: liveTestConfig, environment: JavaScriptEnvironment())
        let jsProg = fuzzer.sync {
            let b = fuzzer.makeBuilder()

            let structType = b.wasmDefineTypeGroup {
                [
                    b.wasmDefineStructType(
                        fields: [WasmStructTypeDescription.Field(type: .wasmi32, mutability: true)],
                        indexTypes: [],
                        isFinal: true
                    )
                ]
            }[0]

            let module = b.buildWasmModule { wasmModule in
                let global = wasmModule.addGlobal(
                    wasmGlobal: .indexExactRef, isMutable: true, typeDef: structType)

                wasmModule.addWasmFunction(with: [] => [.wasmi32]) { function, _, _ in
                    let i32 = function.consti32(42)
                    let structInst = function.wasmStructNew(structType: structType, fields: [i32])
                    function.wasmStoreGlobal(globalVariable: global, to: structInst)

                    let loadedStruct = function.wasmLoadGlobal(globalVariable: global)
                    let loadedI32 = function.wasmStructGet(theStruct: loadedStruct, fieldIndex: 0)
                    return [loadedI32]
                }
            }

            let exports = module.loadExports()
            let res = b.callMethod(module.getExportedMethod(at: 0), on: exports)

            let outputFunc = b.createNamedVariable(forBuiltin: "output")
            b.callFunction(outputFunc, withArgs: [res])

            let prog = b.finalize()
            return fuzzer.lifter.lift(prog)
        }

        testForOutput(program: jsProg, runner: runner, outputString: "42\n")
    }
}
