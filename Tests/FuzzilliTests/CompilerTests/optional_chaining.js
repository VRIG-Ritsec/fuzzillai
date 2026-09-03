if (typeof output === 'undefined') output = console.log;

// 1. Valid receiver and method
let valid = {
    m(x) { output("m called with:", x); return "m_ret"; },
    0(x) { output("0 called with:", x); return "0_ret"; }
};
output(valid.m(1));
output(valid?.m(2));
output(valid.m?.(3));
output(valid?.m?.(4));
output(valid[0](5));
output(valid?.[0](6));
output(valid[0]?.(7));
output(valid?.[0]?.(8));

output(valid.m(...[9]));
output(valid?.m(...[10]));
output(valid.m?.(...[11]));
output(valid?.m?.(...[12]));
output(valid[0](...[13]));
output(valid?.[0](...[14]));
output(valid[0]?.(...[15]));
output(valid?.[0]?.(...[16]));

// 2. Object missing the method (undefined property)
let empty = {};
try { empty.m(1); } catch (e) { output("empty.m threw TypeError as expected"); }
try { empty?.m(2); } catch (e) { output("empty?.m threw TypeError as expected"); }
output(empty.m?.(3));
output(empty?.m?.(4));
try { empty[0](5); } catch (e) { output("empty[0] threw TypeError as expected"); }
try { empty?.[0](6); } catch (e) { output("empty?.[0] threw TypeError as expected"); }
output(empty[0]?.(7));
output(empty?.[0]?.(8));

try { empty.m(...[9]); } catch (e) { output("empty.m spread threw TypeError as expected"); }
try { empty?.m(...[10]); } catch (e) { output("empty?.m spread threw TypeError as expected"); }
output(empty.m?.(...[11]));
output(empty?.m?.(...[12]));
try { empty[0](...[13]); } catch (e) { output("empty[0] spread threw TypeError as expected"); }
try { empty?.[0](...[14]); } catch (e) { output("empty?.[0] spread threw TypeError as expected"); }
output(empty[0]?.(...[15]));
output(empty?.[0]?.(...[16]));

// 3. Object with non-function property
let nonFnObj = { m: 123, 0: 456 };
try { nonFnObj.m(); } catch (e) { output("nonFnObj.m threw TypeError as expected"); }
try { nonFnObj?.m(); } catch (e) { output("nonFnObj?.m threw TypeError as expected"); }
try { nonFnObj.m?.(); } catch (e) { output("nonFnObj.m?. threw TypeError as expected"); }
try { nonFnObj?.m?.(); } catch (e) { output("nonFnObj?.m?. threw TypeError as expected"); }
try { nonFnObj[0](); } catch (e) { output("nonFnObj[0] threw TypeError as expected"); }
try { nonFnObj?.[0](); } catch (e) { output("nonFnObj?.[0] threw TypeError as expected"); }
try { nonFnObj[0]?.(); } catch (e) { output("nonFnObj[0]?. threw TypeError as expected"); }
try { nonFnObj?.[0]?.(); } catch (e) { output("nonFnObj?.[0]?. threw TypeError as expected"); }

// 4. Undefined / null receivers
let u = undefined;
try { u.m(); } catch (e) { output("u.m threw TypeError as expected"); }
output(u?.m());
try { u.m?.(); } catch (e) { output("u.m?. threw TypeError as expected"); }
output(u?.m?.());
try { u[0](); } catch (e) { output("u[0] threw TypeError as expected"); }
output(u?.[0]());
try { u[0]?.(); } catch (e) { output("u[0]?. threw TypeError as expected"); }
output(u?.[0]?.());

let n = null;
try { n.m(); } catch (e) { output("n.m threw TypeError as expected"); }
output(n?.m());
try { n.m?.(); } catch (e) { output("n.m?. threw TypeError as expected"); }
output(n?.m?.());
try { n[0](); } catch (e) { output("n[0] threw TypeError as expected"); }
output(n?.[0]());
try { n[0]?.(); } catch (e) { output("n[0]?. threw TypeError as expected"); }
output(n?.[0]?.());

// 5. Function calls
let fn = (x) => { output("fn called with:", x); return "fn_ret"; };
output(fn(1));
output(fn?.(2));
output(fn(...[3]));
output(fn?.(...[4]));

let ufn = undefined;
try { ufn(1); } catch (e) { output("ufn() threw TypeError as expected"); }
output(ufn?.(2));
try { ufn(...[3]); } catch (e) { output("ufn spread threw TypeError as expected"); }
output(ufn?.(...[4]));

let nonFnVal = 123;
try { nonFnVal(1); } catch (e) { output("nonFnVal() threw TypeError as expected"); }
try { nonFnVal?.(2); } catch (e) { output("nonFnVal?.() threw TypeError as expected"); }

// 6. Property reads & deletes
output(u?.a);
output(u?.[0]);
output(n?.a);
output(n?.[0]);
output(valid?.a);
output(valid?.[0]);
output(delete u?.a);
output(delete u?.[0]);
output(delete n?.a);
output(delete n?.[0]);
output(delete valid?.a);
output(delete valid?.[0]);

// 7. Classes and Super Method Calls
class SuperClass {
    superMethod(msg) {
        output("SuperClass.superMethod:", msg);
        return "super_ret";
    }
}
class SubClass extends SuperClass {
    test() {
        output(super.superMethod("from sub"));
        output(super.superMethod?.("from sub optional"));
        try {
            output(super.nonExistentMethod?.("nonexistent optional"));
        } catch (e) {
            output("Caught super optional exception:", e.name);
        }
    }
}
new SubClass().test();
