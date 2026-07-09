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

/// Lifts a FuzzIL program to the target language.
public protocol Lifter {
    func lift(_ program: Program, withOptions options: LiftingOptions) -> String
}

extension Lifter {
    public func lift(_ program: Program) -> String {
        return lift(program, withOptions: [])
    }
}

class Ref<T> {
    var val: T
    init(_ val: T) { self.val = val }
}

public struct LiftingOptions: OptionSet {
    public let rawValue: Int
    public init(rawValue: Int) {
        self.rawValue = rawValue
    }

    public static let includeComments = LiftingOptions(rawValue: 1 << 0)
    public static let includeLineNumbers = LiftingOptions(rawValue: 1 << 1)
}

extension DestructuringPattern {
    func lift(
        formatStringKey: (String) -> String,
        formatComputedKey: () -> String,
        formatTarget: (Target) -> String,
        formatDefaultValue: () -> String
    ) -> String {
        switch self {
        case .object(let obj):
            var props = [String]()
            for prop in obj.properties {
                let keyStr =
                    switch prop.key {
                    case .string(let s): formatStringKey(s)
                    case .computed: "[\(formatComputedKey())]"
                    }
                let targetStr = formatTarget(prop.target)

                let defStr = prop.hasDefaultValue ? "=\(formatDefaultValue())" : ""

                props.append("\(keyStr):\(targetStr)\(defStr)")
            }
            if obj.hasRestElement {
                let restStr = formatTarget(.flatBinding)
                props.append("...\(restStr)")
            }
            return "{\(props.joined(separator: ","))}"
        case .array(let arr):
            var elems = [String]()
            for elem in arr.elements {
                if let target = elem.target {
                    let targetStr = formatTarget(target)
                    if elem.hasDefaultValue {
                        elems.append("\(targetStr)=\(formatDefaultValue())")
                    } else {
                        elems.append(targetStr)
                    }
                } else {
                    if elem.hasDefaultValue { _ = formatDefaultValue() }
                    elems.append("")
                }
            }
            if let restTarget = arr.restTarget {
                let restStr = formatTarget(restTarget)
                elems.append("...\(restStr)")
            }
            // Preserve trailing elision formatting for FuzzIL/JS compatibility
            if let last = arr.elements.last, last.target == nil, arr.restTarget == nil {
                elems.append("")
            }
            return "[\(elems.joined(separator: ","))]"
        }
    }
}
