---
name: fuzziltool-compiler-js-feature
description: >-
  Standard Operating Procedure for adding support for a new JavaScript feature in the FuzzILTool Compiler.
  It details a "diffing" approach against an already-supported baseline syntax to implement all stages: from Babel AST, to ast.proto, to FuzzIL representation, before finally lifting back into JS and human-readable FuzzIL. It also details how to add new FuzzIL operations when needed.
---
# Add Seed Parsing Support and FuzzIL Operations for JS Features

When the user asks to add support for a specific JavaScript feature in the FuzzILTool Compiler, follow this exact step-by-step checklist. Do not skip steps unless explicitly instructed.

## CRITICAL RULES

- **IMPORTANT**: NEVER modify any code without asking the user to review the diff first. You are explicitly in an interactive request-review mode.
- **CONVENTION**: `.proto` files define a protobuf format validation (similar to an XML Schema). `[name].xxx.protobuf` defines a binary file containing the raw data that can be validated against the schema (similar to an XML file that validates against an XSD schema).
- **TESTING ENV**: All standard `swift test` executions MUST have the dynamic prefix `FUZZILLI_TEST_SHELL={fuzzbuild}/d8`. Ask the user if they have a fuzzing-enabled V8 build. If so, ask them to input the path to set the `{fuzzbuild}` variable.
  - If they don't have one, ask them to provide their local V8 directory path, and instruct them to run the following in that directory to create one (and then set `{fuzzbuild}` to `out/fuzzbuild` within that path):

    ```bash
    gn gen out/fuzzbuild --args='is_debug=false dcheck_always_on=true v8_static_library=true v8_enable_verify_heap=true v8_enable_partition_alloc=false v8_fuzzilli=true sanitizer_coverage_flags="trace-pc-guard" target_cpu="x64" v8_symbol_level=2 use_remoteexec=true' && autoninja -C out/fuzzbuild
    ```

## Step 1: Feature Extraction

- Find a (set of) test262 cases failing due to the missing JS feature. Ask the user if they already have some.
- Write a minimal but complete compiler test inside `{Fuzzilli_Root}/Tests/FuzzilliTests/CompilerTests/[feature_name].js`.
- Create a baseline passing equivalent test that has the smallest possible diff *WITHOUT* the feature at `{Fuzzilli_Root}/Tests/FuzzilliTests/CompilerTests/[feature_name_diff].js`.
- **Follow TDD**: Run `FUZZILLI_TEST_SHELL={fuzzbuild}/d8 swift test --filter CompilerTests`.
- **USER VERIFICATION**: Give the user the exact command to run the tests from `{Fuzzilli_Root}` and ask them explicitly if they agree with the baseline failure of `[feature_name].js` and the success of `[feature_name_diff].js`.

## Step 2: AST Diffing

- Run the following node.js snippet to parse both files via babel and strip out noisy `start`/`end`/`loc` identifiers. Save this snippet locally as `/tmp/snippet.js`:

  ```javascript
  const Parser = require("@babel/parser");
  const fs = require('fs');
  const file = process.argv[2];
  let ast = Parser.parse(fs.readFileSync(file, 'utf8'), { plugins: ["explicitResourceManagement", "v8intrinsic"] });

  // Custom replacer to clean AST output and remove noisy layout elements
  function replacer(key, value) {
    if (key === 'start' || key === 'end' || key === 'loc' || key === 'extra' || key === 'comments' || key === 'errors') {
      return undefined;
    }
    return value;
  }

  console.log(JSON.stringify(ast, replacer, 2));
  ```

- Run it using the parser's local Babel installation:

  ```bash
  cd {Fuzzilli_Root}/Sources/Fuzzilli/Compiler/Parser
  NODE_PATH=./node_modules node /tmp/snippet.js {Fuzzilli_Root}/Tests/FuzzilliTests/CompilerTests/[feature_name].js > /tmp/[feature_name].babel.json
  NODE_PATH=./node_modules node /tmp/snippet.js {Fuzzilli_Root}/Tests/FuzzilliTests/CompilerTests/[feature_name_diff].js > /tmp/[feature_name_diff].babel.json
  ```

- **USER VERIFICATION**: Tell the user the absolute paths of `/tmp/[feature_name].babel.json` and `/tmp/[feature_name_diff].babel.json`. Explain that these are the clean, diffable Babel representations and ask them to diff the clean copies locally in VS Code or their CLI.

## Step 3: Parser Updates

- Review and choose the appropriate `ast.proto` representation for the nodes identified in the JSON AST diff.
- Edit `parser.js` to implement the translation logic from `[name].babel.json --> [name].ast.protobuf`.
- Generate the Fuzzilli-specific binary protobuf definitions of the feature and diff files using `parser.js`:

  ```bash
  # Execute parser.js logic to output binary protocol buffers
  node parser.js {Fuzzilli_Root}/Tests/FuzzilliTests/CompilerTests/[feature_name].js /tmp/out.[feature_name].ast.protobuf
  node parser.js {Fuzzilli_Root}/Tests/FuzzilliTests/CompilerTests/[feature_name_diff].js /tmp/out.[feature_name_diff].ast.protobuf
  ```

- **USER VERIFICATION**: Give the user the node commands executed above. Provide the `protoc` command line string so they can deserialize the output to human-readable format (`protoc --decode=...`). Ask them to evaluate the diff of the protobuf output, and verify they agree with the proposed modifications to `parser.js` and `ast.proto`.

## Step 4: Compiler and Operations

- Identify and choose the correct representation in `opcodes.swift` (this will generate `program.proto`) and `operations.proto`.
- Modify `compiler.swift` to handle the translations from `/tmp/out.[feature_name].ast.protobuf` into `/tmp/out.[feature_name].program.protobuf` (FuzzIL).
  - *Note: Determine if `Instruction.swift` (specifically the "analyze" function) also requires modification for this feature.*
- Regenerate the core protocol buffers and Swift files using the python generator scripts:

  ```bash
  cd {Fuzzilli_Root}/Sources/Fuzzilli/Protobuf/
  python3 gen_programproto.py
  protoc --swift_opt=Visibility=Public --swift_out=. program.proto operations.proto sync.proto ast.proto
  ```

- **USER VERIFICATION**: Tell the user how to run `swift run FuzzILTool --compile /tmp/out.[feature_name].ast.protobuf /tmp/out.[feature_name].program.protobuf` to compile the AST to FuzzIL. Instruct them on using `protoc` to dump the resulting binary FuzzIL files so they can confirm the human readable diff.

## Step 5: Lifters (FuzzIL -> "FuzzIL Textual Representation" and FuzzIL -> JS)

- Edit `FuzzILLifter.swift` to handle `/tmp/out.[feature_name].program.protobuf` to `/tmp/out.[feature_name].fzil.text`.
- Edit `JavaScriptLifter.swift` to handle `/tmp/out.[feature_name].program.protobuf` to `/tmp/out.[feature_name].relifted.js`.
- **USER VERIFICATION**: Give the user the command lines to run the lifter commands `swift run FuzzILTool --LiftToFuzzIL` and `swift run FuzzILTool --Lift[Corpus]ToJS`. Have them run them, then ask them to diff `/tmp/out.[feature_name].human.fzil` with `/tmp/out.[feature_name_diff].human.fzil`, and `/tmp/out.[feature_name].relifted.js` with `/tmp/out.[feature_name_diff].relifted.js`.

## Step 6: Testing & Verification

- Verify that both `[feature_name].js` and `[feature_name_diff].js` tests now pass within the FuzzIL pipeline.
- Give the exact swift test command for the user to execute themselves securely.
- Add additional deep test coverage in `LifterTests.swift` (update `ProgramBuilder.swift` helpers as needed) and ensure all tests pass cleanly.

## Step 7: Final Touches

- Execute the Fuzzilli formatting and standard rebuild pipeline:

  ```bash
  cd {Fuzzilli_Root}
  swift format . --recursive --in-place --parallel
  find . -type f \( -name "*.proto" -o -name "*.js" \) -exec sed -i 's/[ \t]*$//' {} +
  Tools/presubmit.py --regenerate-proto --format
  swift build
  FUZZILLI_TEST_SHELL={fuzzbuild}/d8 swift test
  ```

- Run `git diff origin/main` and `swift format lint --recursive .` and make sure no wranings or errors from swift lint falls in the range of the current modifications.
- Ask the user to have a 10 mins minimum run of FuzzilliCLI with the FuzzIL of `swift run FuzzILTool --compile {Fuzzilli_Root}/Tests/FuzzilliTests/CompilerTests/[feature_name].js` as a seed, eventually playing with the generators weight. Ask them to compare the stats (correctness/coverage/...) against an equivalent run in `origin/main`
