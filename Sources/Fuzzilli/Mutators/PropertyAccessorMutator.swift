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
public class PropertyAccessorMutator: BaseInstructionMutator {

    static let budgetPerAccessor = 3

    private enum PropertyTarget {
        case property(String)
        case element(Int64)
    }

    public init() {
        // Use the lower generation budget as this mutator also performs arbitrary code generation.
        super.init(maxSimultaneousMutations: defaultMaxSimultaneousCodeGenerations)
    }

    public override func canMutate(_ instr: Instruction) -> Bool {
        switch instr.op.opcode {
        case .setProperty, .setElement, .objectLiteralAddProperty, .objectLiteralAddElement,
            .classAddProperty, .classAddElement:
            true
        default:
            false
        }
    }

    // 50% for getter & setter, 25% chance for only one of them each.
    private func chooseDefineGetterSetter() -> (getter: Bool, setter: Bool) {
        let defineBoth = probability(0.5)
        let defineGetter = defineBoth || probability(0.5)
        let defineSetter = defineBoth || !defineGetter
        return (defineGetter, defineSetter)
    }

    public override func mutate(_ instr: Instruction, _ b: ProgramBuilder) {
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
}
