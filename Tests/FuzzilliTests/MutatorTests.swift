// Copyright 2025 Google LLC
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

import Testing

@testable import Fuzzilli

struct MutatorTests {
    @Test func testSpliceMutatorWasmTypeGroups() {
        let env = JavaScriptEnvironment()
        let config = Configuration(logLevel: .error)
        let fuzzer = makeMockFuzzer(config: config, environment: env)
        fuzzer.sync {
            let b = fuzzer.makeBuilder()

            // Insert another sample that has a typegroup into the corpus
            b.wasmDefineTypeGroup(recursiveGenerator: {
                b.wasmDefineArrayType(elementType: .wasmi64, mutability: true)
            })

            fuzzer.corpus.add(b.finalize(), ProgramAspects(outcome: .succeeded))

            // Now try the splice mutator

            b.wasmDefineTypeGroup(recursiveGenerator: {
                b.wasmDefineArrayType(elementType: .wasmi32, mutability: true)
            })

            let prog = b.finalize()

            let originalEndTypeGroupInstructions = prog.code.filter { instr in
                instr.op is WasmEndTypeGroup
            }

            #expect(originalEndTypeGroupInstructions.count == 1)
            #expect(originalEndTypeGroupInstructions[0].numInputs == 1)

            let spliceMutator = SpliceMutator()

            let candidates = prog.code.filter { instr in spliceMutator.canMutate(instr) == true }
            #expect(candidates.count == 1)

            let mutatedProg = spliceMutator.mutate(prog, using: b, for: fuzzer)!

            let newEndTypeGroupInstructions = mutatedProg.code.filter { instr in
                instr.op is WasmEndTypeGroup
            }

            #expect(newEndTypeGroupInstructions.count == 1)
            #expect(newEndTypeGroupInstructions[0].numInputs > 1)
        }
    }

    @Test func testCodeGenMutatorWasmTypeGroups() {
        let env = JavaScriptEnvironment()
        let config = Configuration(logLevel: .error, enableCustomDescriptors: true)
        let fuzzer = makeMockFuzzer(config: config, environment: env)
        fuzzer.sync {
            let b = fuzzer.makeBuilder()

            // We need a minimum number of visible variables for codeGeneration.
            b.loadInt(1)
            b.loadInt(2)

            b.wasmDefineTypeGroup(recursiveGenerator: {
                b.wasmDefineArrayType(elementType: .wasmi32, mutability: true)
            })

            let prog = b.finalize()

            let originalEndTypeGroupInstructions = prog.code.filter { instr in
                instr.op is WasmEndTypeGroup
            }

            #expect(originalEndTypeGroupInstructions.count == 1)
            #expect(originalEndTypeGroupInstructions[0].numInputs == 1)

            let codeGenMutator = CodeGenMutator()

            let candidates = prog.code.filter { instr in codeGenMutator.canMutate(instr) == true }
            #expect(candidates.count == 1)

            let mutatedProg = codeGenMutator.mutate(prog, using: b, for: fuzzer)!

            let newEndTypeGroupInstructions = mutatedProg.code.filter { instr in
                instr.op is WasmEndTypeGroup
            }

            #expect(newEndTypeGroupInstructions.count == 1)
            #expect(newEndTypeGroupInstructions[0].numInputs > 1)
        }
    }

    @Test func testCodeGenMutatorNamedStrings() {
        // A generator that deterministically generates a different value each time.
        var called = false
        func generateString() -> String {
            if called {
                return "newValue"
            } else {
                called = true
                return "originalValue"
            }
        }
        let mockNamedString = ILType.namedString(ofName: "NamedString")

        let env = JavaScriptEnvironment()
        env.addNamedStringGenerator(forType: mockNamedString, with: generateString)

        let config = Configuration(logLevel: .error)
        let fuzzer = makeMockFuzzer(config: config, environment: env)
        fuzzer.sync {
            let b = fuzzer.makeBuilder()

            // We need a minimum number of visible variables for codeGeneration.
            b.loadInt(1)
            b.loadInt(2)

            let _ = b.findOrGenerateType(mockNamedString)
            #expect(called)

            let prog = b.finalize()

            let originalLoadInstruction = prog.code.filter { instr in
                instr.op is LoadString
            }

            #expect(originalLoadInstruction.count == 1)
            let originalLoad = originalLoadInstruction[0].op as! LoadString
            #expect(originalLoad.value == "originalValue")

            // Mutator is probabalistic, try 10 times to ensure we are very likely
            // to hit the generateString call.
            let mutator = OperationMutator()
            for _ in 1...10 {
                let newBuilder = fuzzer.makeBuilder()
                newBuilder.adopting {
                    mutator.mutate(originalLoadInstruction[0], newBuilder)
                }

                let mutatedProg = newBuilder.finalize()

                let newLoadInstruction = mutatedProg.code.filter { instr in
                    instr.op is LoadString
                }

                #expect(newLoadInstruction.count == 1)
                let newLoad = newLoadInstruction[0].op as! LoadString
                if newLoad.value == "newValue" {
                    return
                }
            }
            Issue.record("Mutator ran 10 times without rerunning custom string generator")
        }
    }

    @Test func testCodeGenMutatorNamedIntegers() {
        // A generator that deterministically generates a different value each time.
        var called = false
        func generateInteger() -> Int64 {
            if called {
                return 42
            } else {
                called = true
                return 301
            }
        }
        let mockNamedInteger = ILType.namedInteger(ofName: "NamedInteger")

        let env = JavaScriptEnvironment()
        env.addNamedIntegerGenerator(forType: mockNamedInteger, with: generateInteger)

        let config = Configuration(logLevel: .error)
        let fuzzer = makeMockFuzzer(config: config, environment: env)
        fuzzer.sync {
            let b = fuzzer.makeBuilder()

            // We need a minimum number of visible variables for codeGeneration.
            b.loadString("a")
            b.loadString("b")

            let _ = b.findOrGenerateType(mockNamedInteger)
            #expect(called)

            let prog = b.finalize()

            let originalLoadInstruction = prog.code.filter { instr in
                instr.op is LoadInteger
            }

            #expect(originalLoadInstruction.count == 1)
            let originalLoad = originalLoadInstruction[0].op as! LoadInteger
            #expect(originalLoad.value == 301)

            // Mutator is probabalistic, try 10 times to ensure we are very likely
            // to hit the generateInteger call.
            let mutator = OperationMutator()
            for _ in 1...10 {
                let newBuilder = fuzzer.makeBuilder()
                newBuilder.adopting {
                    mutator.mutate(originalLoadInstruction[0], newBuilder)
                }

                let mutatedProg = newBuilder.finalize()

                let newLoadInstruction = mutatedProg.code.filter { instr in
                    instr.op is LoadInteger
                }

                #expect(newLoadInstruction.count == 1)
                let newLoad = newLoadInstruction[0].op as! LoadInteger
                if newLoad.value == 42 {
                    return
                }
            }
            Issue.record("Mutator ran 10 times without rerunning custom integer generator")
        }
    }

    @Test func testConcatMutatorBundleHostWithBundleCorpus() {
        let env = JavaScriptEnvironment()
        let config = Configuration(logLevel: .error, generateBundle: true)
        let fuzzer = makeMockFuzzer(config: config, environment: env)

        fuzzer.sync {
            do {
                let b = fuzzer.makeBuilder()
                b.emit(BeginBundleScript())
                b.callFunction(
                    b.createNamedVariable(forBuiltin: "print"), withArgs: [b.loadString("corpus")])
                b.emit(EndBundleScript())
                fuzzer.corpus.add(b.finalize(), ProgramAspects(outcome: .succeeded))
            }

            let mutator = ConcatMutator()
            var hostBundle: Program? = nil
            do {
                let b = fuzzer.makeBuilder()
                b.emit(BeginBundleScript())
                b.callFunction(
                    b.createNamedVariable(forBuiltin: "print"), withArgs: [b.loadString("host")])
                b.emit(EndBundleScript())
                hostBundle = b.finalize()
            }
            let builder = fuzzer.makeBuilder()
            let mutated = mutator.mutate(hostBundle!, using: builder, for: fuzzer)!

            #expect(mutated.code.isBundle)
            #expect(mutated.code.isStaticallyValid())
            let actual = fuzzer.lifter.lift(mutated)
            let expected = """
                // JS_BUNDLE_SCRIPT
                print("host");
                // JS_BUNDLE_SCRIPT
                print("corpus");

                """
            #expect(actual == expected)
        }
    }

    @Test func testConcatMutatorNonBundleHostWithNonBundleCorpus() {
        let env = JavaScriptEnvironment()
        let config = Configuration(logLevel: .error, generateBundle: true)
        let fuzzer = makeMockFuzzer(config: config, environment: env)

        fuzzer.sync {
            let b = ProgramBuilder(for: fuzzer, parent: nil, isBundle: false)
            b.callFunction(
                b.createNamedVariable(forBuiltin: "print"), withArgs: [b.loadString("corpus")])
            fuzzer.corpus.add(b.finalize(), ProgramAspects(outcome: .succeeded))

            let mutator = ConcatMutator()

            let b2 = ProgramBuilder(for: fuzzer, parent: nil, isBundle: false)
            b2.callFunction(
                b2.createNamedVariable(forBuiltin: "print"), withArgs: [b2.loadString("host")])
            let hostNonBundle = b2.finalize()

            let builder = ProgramBuilder(for: fuzzer, parent: nil, isBundle: false)
            let mutated = mutator.mutate(hostNonBundle, using: builder, for: fuzzer)!

            #expect(!mutated.code.isBundle)
            #expect(mutated.code.isStaticallyValid())
            let actual = fuzzer.lifter.lift(mutated)
            let expected = """
                print("host");
                print("corpus");

                """
            #expect(actual == expected)
        }
    }

    @Test func testSpliceMutatorBundleHostWithBundleCorpus() {
        let env = JavaScriptEnvironment()
        let config = Configuration(logLevel: .error, generateBundle: true)
        let fuzzer = makeMockFuzzer(config: config, environment: env)

        fuzzer.sync {
            let b = fuzzer.makeBuilder()
            b.emit(BeginBundleScript())
            b.callFunction(
                b.createNamedVariable(forBuiltin: "print"), withArgs: [b.loadString("corpus")])
            b.emit(EndBundleScript())
            fuzzer.corpus.add(b.finalize(), ProgramAspects(outcome: .succeeded))

            let mutator = SpliceMutator()

            let b2 = fuzzer.makeBuilder()
            b2.emit(BeginBundleScript())
            b2.callFunction(
                b2.createNamedVariable(forBuiltin: "print"), withArgs: [b2.loadString("host")])
            b2.emit(EndBundleScript())
            let hostBundle = b2.finalize()

            let builder = fuzzer.makeBuilder()
            let mutated = mutator.mutate(hostBundle, using: builder, for: fuzzer)!

            #expect(mutated.code.isBundle)
            #expect(mutated.code.isStaticallyValid())
            let actual = fuzzer.lifter.lift(mutated)

            // Splicing is not deterministic, so we cannot assert the exact output.
            #expect(actual.contains("corpus"))
            #expect(actual.contains("host"))
            #expect(actual.contains("// JS_BUNDLE_SCRIPT"))
        }
    }

    @Test func testSpliceMutatorNonBundleHostWithNonBundleCorpus() {
        let env = JavaScriptEnvironment()
        let config = Configuration(logLevel: .error, generateBundle: true)
        let fuzzer = makeMockFuzzer(config: config, environment: env)

        fuzzer.sync {
            let b = ProgramBuilder(for: fuzzer, parent: nil, isBundle: false)
            b.callFunction(
                b.createNamedVariable(forBuiltin: "print"), withArgs: [b.loadString("corpus")])
            fuzzer.corpus.add(b.finalize(), ProgramAspects(outcome: .succeeded))

            let mutator = SpliceMutator()

            let b2 = ProgramBuilder(for: fuzzer, parent: nil, isBundle: false)
            b2.callFunction(
                b2.createNamedVariable(forBuiltin: "print"), withArgs: [b2.loadString("host")])
            let hostNonBundle = b2.finalize()

            let builder = ProgramBuilder(for: fuzzer, parent: nil, isBundle: false)
            let mutated = mutator.mutate(hostNonBundle, using: builder, for: fuzzer)!

            #expect(!mutated.code.isBundle)
            #expect(mutated.code.isStaticallyValid())
            let actual = fuzzer.lifter.lift(mutated)

            // Splicing is not deterministic, so we cannot assert the exact output.
            #expect(actual.contains("corpus"))
            #expect(actual.contains("host"))
            #expect(!actual.contains("// JS_BUNDLE"))
        }
    }

    @Test func testCombineMutatorNonBundle() {
        let env = JavaScriptEnvironment()
        let config = Configuration(logLevel: .error)
        let fuzzer = makeMockFuzzer(config: config, environment: env)

        fuzzer.sync {
            // Corpus program:
            let b = ProgramBuilder(for: fuzzer, parent: nil, isBundle: false)
            b.callFunction(
                b.createNamedVariable(forBuiltin: "print"), withArgs: [b.loadString("corpus start")]
            )
            b.callFunction(
                b.createNamedVariable(forBuiltin: "print"), withArgs: [b.loadString("corpus end")])
            fuzzer.corpus.add(b.finalize(), ProgramAspects(outcome: .succeeded))

            let mutator = CombineMutator()

            // Host program:
            let b2 = ProgramBuilder(for: fuzzer, parent: nil, isBundle: false)
            b2.callFunction(
                b2.createNamedVariable(forBuiltin: "print"), withArgs: [b2.loadString("host start")]
            )
            b2.callFunction(
                b2.createNamedVariable(forBuiltin: "print"), withArgs: [b2.loadString("host end")])
            let hostProg = b2.finalize()

            let builder = ProgramBuilder(for: fuzzer, parent: nil, isBundle: false)
            let mutated = mutator.mutate(hostProg, using: builder, for: fuzzer)!

            #expect(!mutated.code.isBundle)
            #expect(mutated.code.isStaticallyValid())
            let actual = fuzzer.lifter.lift(mutated)

            let expectedPattern1 = """
                print("corpus start");
                print("corpus end");
                print("host start");
                print("host end");

                """

            let expectedPattern2 = """
                print("host start");
                print("host end");
                print("corpus start");
                print("corpus end");

                """
            let expectedPattern3 = """
                print("host start");
                print("corpus start");
                print("corpus end");
                print("host end");

                """

            #expect(
                actual == expectedPattern1 || actual == expectedPattern2
                    || actual == expectedPattern3,
                "Output does not match expected patterns. Actual:\n\(actual)")
        }
    }

    @Test func testCombineMutatorBundle() {
        let env = JavaScriptEnvironment()
        let config = Configuration(logLevel: .error, generateBundle: true)
        let fuzzer = makeMockFuzzer(config: config, environment: env)

        fuzzer.sync {
            // Corpus: a bundle script
            let b = fuzzer.makeBuilder()
            b.emit(BeginBundleScript())
            b.callFunction(
                b.createNamedVariable(forBuiltin: "print"), withArgs: [b.loadString("corpus start")]
            )
            b.callFunction(
                b.createNamedVariable(forBuiltin: "print"), withArgs: [b.loadString("corpus end")])
            b.emit(EndBundleScript())
            fuzzer.corpus.add(b.finalize(), ProgramAspects(outcome: .succeeded))

            let mutator = CombineMutator()

            // Host: two bundle scripts
            let b2 = fuzzer.makeBuilder()
            b2.emit(BeginBundleScript())
            b2.callFunction(
                b2.createNamedVariable(forBuiltin: "print"),
                withArgs: [b2.loadString("host1 start")])
            b2.callFunction(
                b2.createNamedVariable(forBuiltin: "print"), withArgs: [b2.loadString("host1 end")])
            b2.emit(EndBundleScript())

            b2.emit(BeginBundleScript())
            b2.callFunction(
                b2.createNamedVariable(forBuiltin: "print"),
                withArgs: [b2.loadString("host2 start")])
            b2.callFunction(
                b2.createNamedVariable(forBuiltin: "print"), withArgs: [b2.loadString("host2 end")])
            b2.emit(EndBundleScript())
            let hostBundle = b2.finalize()

            let builder = fuzzer.makeBuilder()
            let mutated = mutator.mutate(hostBundle, using: builder, for: fuzzer)!

            #expect(mutated.code.isBundle)
            #expect(mutated.code.isStaticallyValid())
            let actual = fuzzer.lifter.lift(mutated)

            // The mutation can happen at the first EndBundleScript or the second EndBundleScript.
            // "corpus" should be either between host1 and host2, or after host2.
            let expectedPattern1 = """
                // JS_BUNDLE_SCRIPT
                print("host1 start");
                print("host1 end");
                // JS_BUNDLE_SCRIPT
                print("corpus start");
                print("corpus end");
                // JS_BUNDLE_SCRIPT
                print("host2 start");
                print("host2 end");

                """
            let expectedPattern2 = """
                // JS_BUNDLE_SCRIPT
                print("host1 start");
                print("host1 end");
                // JS_BUNDLE_SCRIPT
                print("host2 start");
                print("host2 end");
                // JS_BUNDLE_SCRIPT
                print("corpus start");
                print("corpus end");

                """

            #expect(
                actual == expectedPattern1 || actual == expectedPattern2,
                "Output does not match expected patterns. Actual:\n\(actual)")
        }
    }

    @Test func testWasmArrayNewFixedExtension() {
        let fuzzer = makeMockFuzzer()
        fuzzer.sync {
            let b = fuzzer.makeBuilder()

            b.loadInt(1)  // dummy prefix

            let arrayDef = b.wasmDefineTypeGroup {
                return [b.wasmDefineArrayType(elementType: ILType.wasmi32, mutability: true)]
            }[0]
            b.buildWasmModule { module in
                module.addWasmFunction(with: [] => []) { fn, label, params in
                    let i1 = fn.consti32(1)
                    let array = fn.wasmArrayNewFixed(arrayType: arrayDef, elements: [i1])
                    fn.wasmArrayGet(array: array, index: fn.consti32(0))
                    return []
                }
            }

            let prog = b.finalize()
            let instr = prog.code.filter { $0.op is WasmArrayNewFixed }[0]
            #expect((instr.op as! WasmArrayNewFixed).size == 1)

            let mutator = OperationMutator()
            let newBuilder = fuzzer.makeBuilder()

            newBuilder.adopting {
                for i in 0..<instr.index {
                    newBuilder.adopt(prog.code[i])
                }
                mutator.mutate(instr, newBuilder)
                for i in (instr.index + 1)..<prog.code.count {
                    newBuilder.adopt(prog.code[i])
                }
            }

            let mutatedProg = newBuilder.finalize()
            let mutatedInstr = mutatedProg.code.first(where: { $0.op is WasmArrayNewFixed })!
            let newOp = mutatedInstr.op as! WasmArrayNewFixed
            #expect(newOp.size > 1)
        }
    }

    @Test func testWasmArrayNewFixedMutationGeneratesNewVariable() {
        let fuzzer = makeMockFuzzer()
        fuzzer.sync {
            let b = fuzzer.makeBuilder()

            // Dummy prefix to pass `hasVisibleJsVariables()` check in `extendVariadicOperationByOneInput()`
            b.loadInt(1)

            let arrayDef = b.wasmDefineTypeGroup {
                return [b.wasmDefineArrayType(elementType: ILType.wasmi64, mutability: true)]
            }[0]

            b.buildWasmModule { module in
                module.addWasmFunction(with: [] => []) { fn, label, params in
                    fn.wasmArrayNewFixed(arrayType: arrayDef, elements: [])
                    return []
                }
            }

            let prog = b.finalize()
            let instr = prog.code.first(where: { $0.op is WasmArrayNewFixed })!
            let mutator = OperationMutator()

            b.adopting {
                for i in 0..<instr.index {
                    b.adopt(prog.code[i])
                }
                mutator.mutate(instr, b)
                for i in (instr.index + 1)..<prog.code.count {
                    b.adopt(prog.code[i])
                }
            }

            let mutatedProg = b.finalize()
            let actual = FuzzILLifter().lift(mutatedProg)
            let expectedPattern = #"""
                        v5 <- .+
                        v6 <- WasmArrayNewFixed \[v2(, v5)+\]
                """#

            #expect(
                actual.range(of: expectedPattern, options: .regularExpression) != nil,
                "Lifted program did not match expected pattern. Actual:\n\(actual)")
        }
    }

    @Test func testInputMutatorPendingBundleModule() {
        let env = JavaScriptEnvironment()
        let config = Configuration(logLevel: .error, generateBundle: true)
        let fuzzer = makeMockFuzzer(config: config, environment: env)
        fuzzer.sync {
            let b = fuzzer.makeBuilder()

            let moduleVariable = b.declarePendingBundleModule(name: "testModule", exportNames: [])
            b.buildPendingBundleModule(moduleVariable: moduleVariable) {
            }

            let prog = b.finalize()
            let inputMutator = InputMutator(typeAwareness: .loose)

            let mutatedProg = inputMutator.mutate(prog, using: fuzzer.makeBuilder(), for: fuzzer)
            // Can't mutate the program:
            #expect(mutatedProg == nil)
        }
    }

    @Test func testInputMutatorFallbackWhenReplacementIsNil() {
        let env = JavaScriptEnvironment(additionalBuiltins: ["dummy": .wasmi32])
        let config = Configuration(logLevel: .error)
        let fuzzer = makeMockFuzzer(config: config, environment: env)
        fuzzer.sync {
            let b = fuzzer.makeBuilder()

            let v0 = b.createNamedVariable(forBuiltin: "dummy")
            b.hide(v0)

            let _ = b.getProperty("foo", of: v0)
            let _ = b.loadInt(20)

            let prog = b.finalize()
            let inputMutator = InputMutator(typeAwareness: .loose)

            let mutatedProg = inputMutator.mutate(prog, using: fuzzer.makeBuilder(), for: fuzzer)

            #expect(mutatedProg != nil)
            mutatedProg?.checkOrDie(onFailure: "Program must be statically valid")
        }
    }

    struct PropertyAccessorTestCase: Sendable, CustomStringConvertible {
        let name: String
        let build: @Sendable (ProgramBuilder) -> Void
        let verify: @Sendable (Program) throws -> Void
        var description: String {
            return name
        }
    }

    static let propertyAccessorTestCases = [
        PropertyAccessorTestCase(
            name: "setProperty",
            build: { b in
                let obj = b.createObject(with: [:])
                let val = b.loadInt(42)
                b.setProperty("foo", of: obj, to: val)
            },
            verify: { mutatedProg in
                #expect(
                    mutatedProg.code.contains { instr in
                        if case .configureProperty(let op) = instr.op.opcode {
                            return op.propertyName == "foo"
                                && (op.type == .getter || op.type == .setter
                                    || op.type == .getterSetter)
                        }
                        return false
                    },
                    "Missing ConfigureProperty operation for 'foo' with getter, setter, or getterSetter type"
                )
            }
        ),
        PropertyAccessorTestCase(
            name: "setElement",
            build: { b in
                let obj = b.createArray(with: [])
                let val = b.loadInt(42)
                b.setElement(5, of: obj, to: val)
            },
            verify: { mutatedProg in
                #expect(
                    mutatedProg.code.contains { instr in
                        if case .configureElement(let op) = instr.op.opcode {
                            return op.index == 5
                                && (op.type == .getter || op.type == .setter
                                    || op.type == .getterSetter)
                        }
                        return false
                    },
                    "Missing ConfigureElement operation for index 5 with getter, setter, or getterSetter type"
                )
            }
        ),
        PropertyAccessorTestCase(
            name: "objectLiteralAddProperty",
            build: { b in
                let val = b.loadInt(42)
                let _ = b.buildObjectLiteral { obj in
                    obj.addProperty("foo", as: val)
                }
            },
            verify: { mutatedProg in
                let hasGetter = mutatedProg.code.contains { instr in
                    if case .beginObjectLiteralGetter(let op) = instr.op.opcode {
                        return op.propertyName == "foo"
                    }
                    return false
                }
                let hasSetter = mutatedProg.code.contains { instr in
                    if case .beginObjectLiteralSetter(let op) = instr.op.opcode {
                        return op.propertyName == "foo"
                    }
                    return false
                }
                #expect(
                    hasGetter || hasSetter,
                    "Missing BeginObjectLiteralGetter or BeginObjectLiteralSetter operation for 'foo'"
                )
            }
        ),
        PropertyAccessorTestCase(
            name: "objectLiteralAddElement",
            build: { b in
                let val = b.loadInt(42)
                let _ = b.buildObjectLiteral { obj in
                    obj.addElement(9, as: val)
                }
            },
            verify: { mutatedProg in
                let hasGetter = mutatedProg.code.contains { instr in
                    if case .beginObjectLiteralGetter(let op) = instr.op.opcode {
                        return op.propertyName == "9"
                    }
                    return false
                }
                let hasSetter = mutatedProg.code.contains { instr in
                    if case .beginObjectLiteralSetter(let op) = instr.op.opcode {
                        return op.propertyName == "9"
                    }
                    return false
                }
                #expect(
                    hasGetter || hasSetter,
                    "Missing BeginObjectLiteralGetter or BeginObjectLiteralSetter operation for '9'"
                )
            }
        ),
        PropertyAccessorTestCase(
            name: "objectLiteralAddMethod",
            build: { b in
                let _ = b.buildObjectLiteral { obj in
                    obj.addMethod("foo", with: .parameters(n: 1)) { params in
                        let r = b.loadInt(42)
                        b.doReturn(r)
                    }
                }
            },
            verify: { mutatedProg in
                let hasGetter = mutatedProg.code.contains { instr in
                    if case .beginObjectLiteralGetter(let op) = instr.op.opcode {
                        return op.propertyName == "foo"
                    }
                    return false
                }
                #expect(
                    hasGetter,
                    "Missing BeginObjectLiteralGetter operation for method 'foo'"
                )
            }
        ),
        PropertyAccessorTestCase(
            name: "objectLiteralAddMethodWithDefaults",
            build: { b in
                let defaultVal = b.loadInt(100)
                let _ = b.buildObjectLiteral { obj in
                    obj.addMethod(
                        "foo",
                        with: .parameters(n: 1, defaultParameterIndices: [0]),
                        defaultValues: [defaultVal]
                    ) { params in
                        let r = b.loadInt(42)
                        b.doReturn(r)
                    }
                }
            },
            verify: { mutatedProg in
                let hasGetter = mutatedProg.code.contains { instr in
                    if case .beginObjectLiteralGetter(let op) = instr.op.opcode {
                        return op.propertyName == "foo"
                    }
                    return false
                }
                #expect(hasGetter)

                var maybeLoadInt: Instruction? = nil
                for instr in mutatedProg.code {
                    if case .loadInteger(let op) = instr.op.opcode, op.value == 100 {
                        maybeLoadInt = instr
                        break
                    }
                }
                let loadInt100Instr = try #require(maybeLoadInt)
                let defaultValueVar = loadInt100Instr.output

                var maybeFunctionStart: Instruction? = nil
                for instr in mutatedProg.code {
                    switch instr.op.opcode {
                    case .beginPlainFunction, .beginAsyncFunction, .beginGeneratorFunction,
                        .beginAsyncGeneratorFunction:
                        if instr.inputs.contains(defaultValueVar) {
                            maybeFunctionStart = instr
                        }
                        break
                    default:
                        break
                    }
                }
                #expect(
                    maybeFunctionStart != nil,
                    "Missing function definition with parameter with matching default value")
            }
        ),
        PropertyAccessorTestCase(
            name: "classAddProperty",
            build: { b in
                let val = b.loadInt(42)
                let _ = b.buildClassDefinition { cls in
                    cls.addInstanceProperty("foo", value: val)
                }
            },
            verify: { mutatedProg in
                let hasGetter = mutatedProg.code.contains { instr in
                    if case .beginClassGetter(let op) = instr.op.opcode {
                        return op.propertyName == "foo" && !op.isStatic
                    }
                    return false
                }
                let hasSetter = mutatedProg.code.contains { instr in
                    if case .beginClassSetter(let op) = instr.op.opcode {
                        return op.propertyName == "foo" && !op.isStatic
                    }
                    return false
                }
                #expect(
                    hasGetter || hasSetter,
                    "Missing BeginClassGetter or BeginClassSetter operation for 'foo'")
            }
        ),
        PropertyAccessorTestCase(
            name: "classAddElement",
            build: { b in
                let val = b.loadInt(42)
                let _ = b.buildClassDefinition { cls in
                    cls.addInstanceElement(9, value: val)
                }
            },
            verify: { mutatedProg in
                let hasGetter = mutatedProg.code.contains { instr in
                    if case .beginClassGetter(let op) = instr.op.opcode {
                        return op.propertyName == "9" && !op.isStatic
                    }
                    return false
                }
                let hasSetter = mutatedProg.code.contains { instr in
                    if case .beginClassSetter(let op) = instr.op.opcode {
                        return op.propertyName == "9" && !op.isStatic
                    }
                    return false
                }
                #expect(
                    hasGetter || hasSetter,
                    "Missing BeginClassGetter or BeginClassSetter operation for '9'")
            }
        ),
        PropertyAccessorTestCase(
            name: "classAddStaticProperty",
            build: { b in
                let val = b.loadInt(42)
                let _ = b.buildClassDefinition { cls in
                    cls.addStaticProperty("foo", value: val)
                }
            },
            verify: { mutatedProg in
                let hasGetter = mutatedProg.code.contains { instr in
                    if case .beginClassGetter(let op) = instr.op.opcode {
                        return op.propertyName == "foo" && op.isStatic
                    }
                    return false
                }
                let hasSetter = mutatedProg.code.contains { instr in
                    if case .beginClassSetter(let op) = instr.op.opcode {
                        return op.propertyName == "foo" && op.isStatic
                    }
                    return false
                }
                #expect(
                    hasGetter || hasSetter,
                    "Missing static BeginClassGetter or BeginClassSetter operation for 'foo'")
            }
        ),
        PropertyAccessorTestCase(
            name: "classAddMethod",
            build: { b in
                let _ = b.buildClassDefinition { cls in
                    cls.addInstanceMethod("foo", with: .parameters(n: 1)) { params in
                        let r = b.loadInt(42)
                        b.doReturn(r)
                    }
                }
            },
            verify: { mutatedProg in
                let hasGetter = mutatedProg.code.contains { instr in
                    if case .beginClassGetter(let op) = instr.op.opcode {
                        return op.propertyName == "foo" && !op.isStatic
                    }
                    return false
                }
                #expect(
                    hasGetter,
                    "Missing BeginClassGetter operation for method 'foo'"
                )
            }
        ),
        PropertyAccessorTestCase(
            name: "classAddStaticMethod",
            build: { b in
                let _ = b.buildClassDefinition { cls in
                    cls.addStaticMethod("foo", with: .parameters(n: 1)) { params in
                        let r = b.loadInt(42)
                        b.doReturn(r)
                    }
                }
            },
            verify: { mutatedProg in
                let hasGetter = mutatedProg.code.contains { instr in
                    if case .beginClassGetter(let op) = instr.op.opcode {
                        return op.propertyName == "foo" && op.isStatic
                    }
                    return false
                }
                #expect(
                    hasGetter,
                    "Missing static BeginClassGetter operation for static method 'foo'"
                )
            }
        ),
        PropertyAccessorTestCase(
            name: "classAddPropertyNoValue",
            build: { b in
                let _ = b.buildClassDefinition { cls in
                    cls.addInstanceProperty("foo")
                }
            },
            verify: { mutatedProg in
                let hasGetter = mutatedProg.code.contains { instr in
                    if case .beginClassGetter(let op) = instr.op.opcode {
                        return op.propertyName == "foo" && !op.isStatic
                    }
                    return false
                }
                let hasSetter = mutatedProg.code.contains { instr in
                    if case .beginClassSetter(let op) = instr.op.opcode {
                        return op.propertyName == "foo" && !op.isStatic
                    }
                    return false
                }
                #expect(
                    hasGetter || hasSetter,
                    "Missing BeginClassGetter or BeginClassSetter operation for 'foo'")
            }
        ),
    ]

    @Test(arguments: propertyAccessorTestCases)
    func testPropertyAccessorMutator(testCase: PropertyAccessorTestCase) throws {
        let env = JavaScriptEnvironment()
        let config = Configuration(logLevel: .error)
        let fuzzer = makeMockFuzzer(config: config, environment: env)
        try fuzzer.sync {
            for _ in 0..<10 {
                let b = fuzzer.makeBuilder()
                testCase.build(b)
                let program = b.finalize()

                let mutator = PropertyAccessorMutator()
                let mutatedProg = try #require(
                    mutator.mutate(program, using: fuzzer.makeBuilder(), for: fuzzer))

                mutatedProg.checkOrDie(onFailure: "Program must be statically valid")
                try testCase.verify(mutatedProg)
            }
        }
    }

    @Test func testPropertyAccessorMutatorExcludesSuperMethods() throws {
        let fuzzer = makeMockFuzzer()
        fuzzer.sync {
            let b = fuzzer.makeBuilder()
            let superClass = b.buildClassDefinition { cls in }
            let _ = b.buildClassDefinition(withSuperclass: superClass) { cls in
                cls.addInstanceMethod("foo", with: .parameters(n: 0)) { params in
                    let _ = b.getSuperProperty("bar")
                    b.doReturn(b.loadUndefined())
                }
            }
            let program = b.finalize()
            let mutatedProg =
                PropertyAccessorMutator().mutate(program, using: fuzzer.makeBuilder(), for: fuzzer)
            #expect(mutatedProg == nil, "The mutator should not modify the program due to super")
        }
    }

    @Test func testOperationMutatorPreservesGeneratorAndAsyncFlagsOnClassMethod() {
        let fuzzer = makeMockFuzzer()
        fuzzer.sync {
            let b = fuzzer.makeBuilder()

            b.buildClassDefinition { cls in
                cls.addInstanceMethod(
                    "m", with: .parameters(n: 0), isGenerator: true, isAsync: true
                ) { _ in }
            }
            b.buildObjectLiteral { obj in
                obj.addMethod("m2", with: .parameters(n: 0), isGenerator: true, isAsync: true) {
                    _ in
                }
            }

            let prog = b.finalize()

            let mutator = OperationMutator()
            let newBuilder = fuzzer.makeBuilder()

            newBuilder.adopting {
                for instr in prog.code {
                    if instr.op is BeginClassMethod || instr.op is BeginObjectLiteralMethod {
                        mutator.mutate(instr, newBuilder)
                    } else {
                        newBuilder.adopt(instr)
                    }
                }
            }

            let mutatedProg = newBuilder.finalize()
            let mutatedClassMethod =
                mutatedProg.code.first(where: { $0.op is BeginClassMethod })!.op
                as! BeginClassMethod
            #expect(mutatedClassMethod.isAsync == true)
            #expect(mutatedClassMethod.isGenerator == true)

            let mutatedObjMethod =
                mutatedProg.code.first(where: { $0.op is BeginObjectLiteralMethod })!.op
                as! BeginObjectLiteralMethod
            #expect(mutatedObjMethod.isAsync == true)
            #expect(mutatedObjMethod.isGenerator == true)

            mutatedProg.checkOrDie(onFailure: "Program must be statically valid")
        }
    }

    @Test func testOperationMutatorDestructPatternCrash() throws {
        let fuzzer = makeMockFuzzer()
        try fuzzer.sync {
            let pattern = DestructuringPattern.array(
                .init(
                    elements: [
                        .init(target: .flatBinding, hasDefaultValue: true)
                    ],
                    restTarget: nil
                )
            )

            let b = fuzzer.makeBuilder()
            let source = b.loadInt(1)
            let defaultValue = b.loadInt(2)
            b.destruct(source, using: pattern, defaultValues: [defaultValue])
            let prog = b.finalize()

            let mutator = OperationMutator()

            let newBuilder = fuzzer.makeBuilder()
            newBuilder.adopting {
                for instr in prog.code {
                    if instr.op is Destruct {
                        mutator.mutate(instr, newBuilder)
                    } else {
                        newBuilder.adopt(instr)
                    }
                }
            }

            let mutatedProg = newBuilder.finalize()
            // The program should be valid and lifting should not crash.
            try mutatedProg.code.check()
            _ = FuzzILLifter().lift(mutatedProg)
        }
    }

    @Test func testOperationMutatorObjectDestructAndReassign() throws {
        let fuzzer = makeMockFuzzer()
        try fuzzer.sync {
            let b = fuzzer.makeBuilder()
            let v0 = b.loadInt(42)
            let v1 = b.loadFloat(13.37)
            let v2 = b.loadString("Hello")
            let v3 = b.createObject(with: ["foo": v0, "bar": v1])
            b.destruct(v3, selecting: ["foo", "bar"], into: [v2, v0], hasRestElement: false)
            b.destruct(v3, selecting: ["foo", "bar"], into: [v2, v0, v1], hasRestElement: true)
            let prog = b.finalize()

            let mutator = OperationMutator()
            let newBuilder = fuzzer.makeBuilder()
            newBuilder.adopting {
                for instr in prog.code {
                    // TODO(rherouart): Find a way to make these tests deterministic
                    if instr.op is DestructAndReassign {
                        mutator.mutate(instr, newBuilder)
                    } else {
                        newBuilder.adopt(instr)
                    }
                }
            }
            let mutatedProg = newBuilder.finalize()
            try mutatedProg.code.check()
            _ = FuzzILLifter().lift(mutatedProg)
        }
    }

    @Test func testPrivatePropertyAndMethodMutation() throws {
        let env = JavaScriptEnvironment()
        let config = Configuration(logLevel: .error)
        let fuzzer = makeMockFuzzer(config: config, environment: env)
        fuzzer.sync {
            let b = fuzzer.makeBuilder()
            let _ = b.buildClassDefinition { cls in
                cls.addPrivateInstanceProperty("foo")
                cls.addPrivateInstanceProperty("bar")
                cls.addPrivateInstanceMethod("m", with: .parameters(n: 0)) { params in
                    let this = params[0]
                    b.getPrivateProperty("foo", of: this)
                    b.setPrivateProperty("foo", of: this, to: b.loadInt(42))
                }
            }
            let program = b.finalize()

            let mutator = OperationMutator()
            var mutated = false
            for _ in 0..<100 {
                if let mutatedProg = mutator.mutate(
                    program, using: fuzzer.makeBuilder(), for: fuzzer)
                {
                    mutatedProg.checkOrDie(
                        onFailure: "Mutated program with private members must be statically valid")
                    mutated = true
                }
            }
            #expect(mutated)
        }
    }
}
