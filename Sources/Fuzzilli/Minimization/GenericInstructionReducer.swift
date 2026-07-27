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

/// Removes simple instructions from a program if they are not required.
struct GenericInstructionReducer: Reducer {
    func reduce(with helper: MinimizationHelper) {
        for instr in helper.code.reversed() {
            if !instr.isSimple || instr.isNop {
                continue
            }

            // TODO(bettscheider): Don't skip once we fix this properly.
            // Do not remove index-typed global definitions as they are implicitly
            // exported and possibly used outside the Wasm module.
            // Removing them could lead to dangling unowned references.
            if let op = instr.op as? WasmDefineGlobal, case .indexRef = op.wasmGlobal {
                continue
            }

            helper.tryNopping(instructionAt: instr.index)
        }
    }
}
