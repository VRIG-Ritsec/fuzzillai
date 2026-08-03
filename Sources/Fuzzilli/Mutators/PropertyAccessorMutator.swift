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

/// A mutator that replaces an existing property with a getter and setter accessor.
/// Presumably, an existing operation that defines a property is in many cases also being used if it
/// survived minimization. Therefore, this mutator tries to replace existing property definitions
/// with accessors that can trigger arbitrary side-effects on access (e.g. in a built-in) which can
/// trigger interesting corner cases.
///
/// Example of the logic applied:
///     Original:
///         obj.foo = 42
///     Mutated:
///         Object.defineProperty(obj, 'foo', {
///             get: function() {
///                 /* random code */
///                 return 42;
///             },
///             set: function(v) {
///                 /* random code */
///             },
///         });
///
/// Note that this changes the semantics slightly in the getter which then always returns the
/// initial value, the setter does not update it. This could be changed, however it will change the
/// observable behavior in some way or another (e.g. by introducing a non-enumerable property to
/// store the underlying value).
///
/// Example for methods:
///     Original:
///         let obj = {
///             myMethod(x) {
///                 return x + 42;
///             }
///         };
///     Mutated:
///         let obj = {
///             get myMethod() {
///                 /* random code */
///                 return function(x) {
///                     return x + 42;
///                 };
///             },
///             set myMethod(v) {
///                 /* random code */
///             }
///         };

public class PropertyAccessorMutator: Mutator {

    static let budgetPerAccessor = 3
    static let maxSimultaneousMutations = defaultMaxSimultaneousCodeGenerations

    private enum PropertyTarget {
        case property(String)
        case element(Int64)
    }

    public init() {
        super.init()
    }

    /// 50% for getter & setter, 25% chance for only one of them each.
    private func chooseDefineGetterSetter() -> (getter: Bool, setter: Bool) {
        let defineBoth = probability(0.5)
        let defineGetter = defineBoth || probability(0.5)
        let defineSetter = defineBoth || !defineGetter
        return (defineGetter, defineSetter)
    }

    /// Returns whether the method body is valid for the subroutine context it will be run in.
    private func methodBodyCanBeAdoptedInSubroutine(
        _ program: Program, from startIndex: Int, to endIndex: Int
    ) -> Bool {
        for i in startIndex..<endIndex {
            let required = program.code[i].op.requiredContext
            if required.contains(.method) || required.contains(.classMethod) {
                return false
            }
        }
        return true
    }

    public override func mutate(_ program: Program, using b: ProgramBuilder, for fuzzer: Fuzzer)
        -> Program?
    {
        var candidates = [Int]()
        for instr in program.code {
            switch instr.op.opcode {
            case .setProperty, .setElement, .objectLiteralAddProperty, .objectLiteralAddElement,
                .classAddProperty, .classAddElement:
                candidates.append(instr.index)
            case .beginObjectLiteralMethod, .beginClassMethod:
                if methodBodyCanBeAdoptedInSubroutine(
                    program, from: instr.index + 1,
                    to: program.code.findBlockEnd(head: instr.index))
                {
                    candidates.append(instr.index)
                }
            default:
                break
            }
        }

        guard candidates.count > 0 else {
            return nil
        }

        var toMutate = Set<Int>()
        for _ in 0..<Int.random(in: 1...Self.maxSimultaneousMutations) {
            toMutate.insert(chooseUniform(from: candidates))
        }

        var skipUntilIndex = -1

        b.adopting {
            for instr in program.code {
                if instr.index <= skipUntilIndex {
                    continue
                }

                if toMutate.contains(instr.index) {
                    if case .beginObjectLiteralMethod(let op) = instr.op.opcode {
                        skipUntilIndex = program.code.findBlockEnd(head: instr.index)
                        mutateObjectLiteralMethod(
                            op, originalBlockHead: instr, using: b, program: program)
                    } else if case .beginClassMethod(let op) = instr.op.opcode {
                        skipUntilIndex = program.code.findBlockEnd(head: instr.index)
                        mutateClassMethod(
                            op, originalBlockHead: instr, using: b, program: program)
                    } else {
                        mutateProperty(instr, b)
                    }
                } else {
                    b.adopt(instr)
                }
            }
        }

        return b.finalize()
    }

    private func mutateProperty(_ instr: Instruction, _ b: ProgramBuilder) {
        switch instr.op.opcode {
        case .objectLiteralAddProperty(let op):
            let value = b.adopt(instr.input(0))
            mutateObjectLiteralProperty(op.propertyName, to: value, using: b)
        case .objectLiteralAddElement(let op):
            let value = b.adopt(instr.input(0))
            mutateObjectLiteralProperty(String(op.index), to: value, using: b)
        case .classAddProperty(let op):
            let value = op.hasValue ? b.adopt(instr.input(0)) : nil
            mutateClassProperty(op.propertyName, to: value, isStatic: op.isStatic, using: b)
        case .classAddElement(let op):
            let value = op.hasValue ? b.adopt(instr.input(0)) : nil
            mutateClassProperty(String(op.index), to: value, isStatic: op.isStatic, using: b)
        default:
            let target: PropertyTarget =
                switch instr.op.opcode {
                case .setProperty(let op):
                    .property(op.propertyName)
                case .setElement(let op):
                    .element(op.index)
                default:
                    fatalError(
                        "Instruction is not a supported property/element set or update operation")
                }

            let adoptedInputs = b.adopt(instr.inputs)
            let object = adoptedInputs[0]
            let value = adoptedInputs[1]

            let (defineGetter, defineSetter) = chooseDefineGetterSetter()

            // Build getter function which performs side-effects and returns the original input value.
            let getter =
                defineGetter
                ? b.buildPlainFunction(with: .parameters(n: 0)) { _ in
                    b.build(n: Self.budgetPerAccessor, by: .generating)
                    b.doReturn(value)
                } : nil

            // Build setter function which performs side-effects.
            let setter =
                defineSetter
                ? b.buildPlainFunction(with: .parameters(n: 1)) { _ in
                    b.build(n: Self.budgetPerAccessor, by: .generating)
                } : nil

            let flags = PropertyFlags.randomWithoutWritable()
            let config: ProgramBuilder.PropertyConfiguration =
                if let getter, let setter {
                    .getterSetter(getter, setter)
                } else if let getter {
                    .getter(getter)
                } else {
                    .setter(setter!)
                }

            switch target {
            case .property(let name):
                b.configureProperty(name, of: object, usingFlags: flags, as: config)
            case .element(let index):
                b.configureElement(index, of: object, usingFlags: flags, as: config)
            }
        }
    }

    private func mutateObjectLiteralProperty(
        _ propertyName: String, to value: Variable, using b: ProgramBuilder
    ) {
        let (defineGetter, defineSetter) = chooseDefineGetterSetter()
        if defineGetter {
            b.emit(BeginObjectLiteralGetter(propertyName: propertyName))
            b.build(n: Self.budgetPerAccessor, by: .generating)
            b.doReturn(value)
            b.emit(EndObjectLiteralGetter())
        }
        if defineSetter {
            b.emit(BeginObjectLiteralSetter(propertyName: propertyName))
            b.build(n: Self.budgetPerAccessor, by: .generating)
            b.emit(EndObjectLiteralSetter())
        }
    }

    private func mutateClassProperty(
        _ propertyName: String, to value: Variable?, isStatic: Bool, using b: ProgramBuilder
    ) {
        let (defineGetter, defineSetter) = chooseDefineGetterSetter()
        if defineGetter {
            b.emit(BeginClassGetter(propertyName: propertyName, isStatic: isStatic))
            b.build(n: Self.budgetPerAccessor, by: .generating)
            b.doReturn(value ?? b.loadUndefined())
            b.emit(EndClassGetter())
        }
        if defineSetter {
            b.emit(BeginClassSetter(propertyName: propertyName, isStatic: isStatic))
            b.build(n: Self.budgetPerAccessor, by: .generating)
            b.emit(EndClassSetter())
        }
    }

    /// Creates the inner function for the method replacement that contains the body of the
    /// original method without changes.
    private func buildInnerFunction(
        parameters: Parameters,
        isAsync: Bool,
        isGenerator: Bool,
        originalBlockHead instr: Instruction,
        getterThis: Variable,
        using b: ProgramBuilder,
        program: Program
    ) -> Variable {
        assert(program.code[instr.index].isBlockStart)
        b.build(n: Self.budgetPerAccessor, by: .generating)

        // Create a function with the same parameters as the original one.
        let descriptor = ProgramBuilder.SubroutineDescriptor.parameters(parameters)
        let defaultValues = b.adopt(instr.inputs)

        let buildBlock: ([Variable]) -> Void = { params in
            // Map original method parameters (without `this`) to the new function parameters.
            for (newParam, originalParam) in zip(params, instr.innerOutputs.dropFirst()) {
                b.setAdoptionMap(for: originalParam, to: newParam)
            }

            // Adopt original method body instructions (excluding start and end).
            let endIndex = program.code.findBlockEnd(head: instr.index)
            for i in (instr.index + 1)..<endIndex {
                b.adopt(program.code[i])
            }
        }

        return
            if isAsync && isGenerator
        {
            b.buildAsyncGeneratorFunction(
                with: descriptor, defaultValues: defaultValues, buildBlock)
        } else if isAsync {
            b.buildAsyncFunction(with: descriptor, defaultValues: defaultValues, buildBlock)
        } else if isGenerator {
            b.buildGeneratorFunction(
                with: descriptor, defaultValues: defaultValues, buildBlock)
        } else {
            b.buildPlainFunction(with: descriptor, defaultValues: defaultValues, buildBlock)
        }
    }

    private func mutateObjectLiteralMethod(
        _ op: BeginObjectLiteralMethod, originalBlockHead instr: Instruction,
        using b: ProgramBuilder, program: Program
    ) {
        // We must always define a getter to preserve the original content.
        let getterInstr = b.emit(BeginObjectLiteralGetter(propertyName: op.methodName))
        let getterThis = getterInstr.innerOutput(0)
        // Map the original method's `this` (first inner output) to the getter's `this`.
        b.setAdoptionMap(for: instr.innerOutput(0), to: getterThis)

        let innerFunction = buildInnerFunction(
            parameters: op.parameters,
            isAsync: op.isAsync,
            isGenerator: op.isGenerator,
            originalBlockHead: instr,
            getterThis: getterThis,
            using: b,
            program: program
        )
        b.doReturn(innerFunction)
        b.emit(EndObjectLiteralGetter())

        if probability(0.5) {
            b.emit(BeginObjectLiteralSetter(propertyName: op.methodName))
            b.build(n: Self.budgetPerAccessor, by: .generating)
            b.emit(EndObjectLiteralSetter())
        }
    }

    private func mutateClassMethod(
        _ op: BeginClassMethod, originalBlockHead instr: Instruction,
        using b: ProgramBuilder, program: Program
    ) {
        // We must always define a getter to preserve the original content.
        let getterInstr = b.emit(
            BeginClassGetter(propertyName: op.methodName, isStatic: op.isStatic))
        let getterThis = getterInstr.innerOutput(0)
        // Map the original method's `this` (first inner output) to the getter's `this`.
        // Note that the meaning of `this` changes depending on whether it's a static method or
        // not, still as the getter has the same "staticness", the semantics is preserved.
        b.setAdoptionMap(for: instr.innerOutput(0), to: getterThis)

        let innerFunction = buildInnerFunction(
            parameters: op.parameters,
            isAsync: op.isAsync,
            isGenerator: op.isGenerator,
            originalBlockHead: instr,
            getterThis: getterThis,
            using: b,
            program: program
        )
        b.doReturn(innerFunction)
        b.emit(EndClassGetter())

        if probability(0.5) {
            b.emit(BeginClassSetter(propertyName: op.methodName, isStatic: op.isStatic))
            b.build(n: Self.budgetPerAccessor, by: .generating)
            b.emit(EndClassSetter())
        }
    }
}
