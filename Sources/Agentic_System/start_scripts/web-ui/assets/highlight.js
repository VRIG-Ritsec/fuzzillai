/* Tiny JavaScript syntax highlighter.
 * Hand-rolled, no dependencies. Handles comments, strings, template literals,
 * regex, numbers, keywords, booleans, and identifiers. Not a full parser —
 * good enough for lifted Fuzzilli programs, which are small snippets.
 *
 * Usage:
 *   const html = HL.highlight(source);
 *   element.innerHTML = html;
 */
(function (global) {
  "use strict";

  const KEYWORDS = new Set([
    "var", "let", "const", "function", "return", "if", "else", "for", "while",
    "do", "switch", "case", "break", "continue", "new", "delete", "typeof",
    "instanceof", "in", "of", "try", "catch", "finally", "throw", "class",
    "extends", "super", "this", "import", "export", "from", "as", "async",
    "await", "yield", "void", "with", "default", "static", "get", "set",
  ]);

  const LITERALS = new Set(["true", "false", "null", "undefined", "NaN", "Infinity"]);

  const BUILTINS = new Set([
    "Array", "Object", "String", "Number", "Boolean", "Symbol", "BigInt",
    "Math", "Date", "JSON", "RegExp", "Map", "Set", "WeakMap", "WeakSet",
    "Promise", "Proxy", "Reflect", "Error", "TypeError", "RangeError",
    "SyntaxError", "console", "globalThis", "Function", "ArrayBuffer",
    "Int8Array", "Uint8Array", "Int16Array", "Uint16Array", "Int32Array",
    "Uint32Array", "Float32Array", "Float64Array", "DataView",
  ]);

  const ESCAPE_MAP = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  function esc(s) {
    return String(s).replace(/[&<>"']/g, ch => ESCAPE_MAP[ch]);
  }

  function span(cls, text) {
    return '<span class="tok-' + cls + '">' + esc(text) + "</span>";
  }

  // Contexts where a `/` starts a regex literal rather than division.
  // Cheap heuristic: after any token that wouldn't end an expression.
  const REGEX_AFTER = /[=(,;:!&|?+\-*/%^{}[\]~]|\breturn\b|\btypeof\b|\binstanceof\b|\bin\b|\bof\b|\bnew\b|\bdelete\b|\bvoid\b/;

  function highlight(source) {
    if (source == null) return "";
    const src = String(source);
    let out = "";
    let i = 0;
    let lastSignificant = ""; // trailing non-whitespace we emitted, used for regex detection
    const len = src.length;

    while (i < len) {
      const c = src[i];
      const next = src[i + 1];

      // line comment
      if (c === "/" && next === "/") {
        let j = i + 2;
        while (j < len && src[j] !== "\n") j++;
        out += span("comment", src.slice(i, j));
        i = j;
        continue;
      }

      // block comment
      if (c === "/" && next === "*") {
        let j = i + 2;
        while (j < len - 1 && !(src[j] === "*" && src[j + 1] === "/")) j++;
        j = Math.min(len, j + 2);
        out += span("comment", src.slice(i, j));
        i = j;
        continue;
      }

      // string: single or double quoted
      if (c === '"' || c === "'") {
        const quote = c;
        let j = i + 1;
        while (j < len) {
          if (src[j] === "\\") { j += 2; continue; }
          if (src[j] === quote) { j++; break; }
          if (src[j] === "\n") break;
          j++;
        }
        out += span("string", src.slice(i, j));
        lastSignificant = '"';
        i = j;
        continue;
      }

      // template literal — keep it simple: treat the whole thing as a string
      if (c === "`") {
        let j = i + 1;
        while (j < len) {
          if (src[j] === "\\") { j += 2; continue; }
          if (src[j] === "`") { j++; break; }
          j++;
        }
        out += span("string", src.slice(i, j));
        lastSignificant = "`";
        i = j;
        continue;
      }

      // regex literal
      if (c === "/" && (lastSignificant === "" || REGEX_AFTER.test(lastSignificant))) {
        let j = i + 1;
        let inClass = false;
        while (j < len) {
          const cj = src[j];
          if (cj === "\\") { j += 2; continue; }
          if (cj === "[") inClass = true;
          else if (cj === "]") inClass = false;
          else if (cj === "/" && !inClass) { j++; break; }
          else if (cj === "\n") break;
          j++;
        }
        // flags
        while (j < len && /[gimsuy]/.test(src[j])) j++;
        out += span("regex", src.slice(i, j));
        lastSignificant = "/";
        i = j;
        continue;
      }

      // number
      if ((c >= "0" && c <= "9") || (c === "." && next >= "0" && next <= "9")) {
        let j = i + 1;
        if (c === "0" && (next === "x" || next === "X")) {
          j++;
          while (j < len && /[0-9a-fA-F_]/.test(src[j])) j++;
        } else if (c === "0" && (next === "b" || next === "B")) {
          j++;
          while (j < len && /[01_]/.test(src[j])) j++;
        } else if (c === "0" && (next === "o" || next === "O")) {
          j++;
          while (j < len && /[0-7_]/.test(src[j])) j++;
        } else {
          while (j < len && /[0-9_.]/.test(src[j])) j++;
          if (j < len && (src[j] === "e" || src[j] === "E")) {
            j++;
            if (src[j] === "+" || src[j] === "-") j++;
            while (j < len && /[0-9]/.test(src[j])) j++;
          }
        }
        if (j < len && src[j] === "n") j++; // BigInt
        out += span("number", src.slice(i, j));
        lastSignificant = "0";
        i = j;
        continue;
      }

      // identifier / keyword
      if (/[A-Za-z_$]/.test(c)) {
        let j = i + 1;
        while (j < len && /[A-Za-z0-9_$]/.test(src[j])) j++;
        const word = src.slice(i, j);
        let cls;
        if (KEYWORDS.has(word)) cls = "keyword";
        else if (LITERALS.has(word)) cls = "literal";
        else if (BUILTINS.has(word)) cls = "builtin";
        // function call? peek past whitespace for '('
        else {
          let k = j;
          while (k < len && (src[k] === " " || src[k] === "\t")) k++;
          cls = src[k] === "(" ? "function" : "ident";
        }
        out += cls === "ident" ? esc(word) : span(cls, word);
        lastSignificant = "a";
        i = j;
        continue;
      }

      // whitespace passes through unchanged, doesn't alter lastSignificant
      if (c === " " || c === "\t" || c === "\n" || c === "\r") {
        out += c;
        i++;
        continue;
      }

      // punctuation / operators
      out += span("punct", c);
      lastSignificant = c;
      i++;
    }

    return out;
  }

  global.HL = { highlight };
})(typeof window !== "undefined" ? window : globalThis);
