if (typeof output === 'undefined') output = console.log;

// 1. Basic Object Destructuring
function f1({ a, b }) { output(a, b); }
f1({ a: 1, b: 2 });

const a1 = ({ a, b }) => { output(a, b); };
a1({ a: 1, b: 2 });

// 2. Object Destructuring with Aliasing
function f2({ c: charlie }) { output(charlie); }
f2({ c: 3 });

const a2 = ({ c: charlie }) => { output(charlie); };
a2({ c: 3 });

// 3. Rest Properties in Object Destructuring
function f5({ k, ...restProps }) { output(k, restProps.l, restProps.m); }
f5({ k: 11, l: 12, m: 13 });

const a5 = ({ k, ...restProps }) => { output(k, restProps.l, restProps.m); };
a5({ k: 11, l: 12, m: 13 });

// 4. Deep/Nested Object Destructuring
function f6({ user: { profile: { id } } }) { output(id); }
f6({ user: { profile: { id: 14 } } });

const a6 = ({ user: { profile: { id } } }) => { output(id); };
a6({ user: { profile: { id: 14 } } });

// 5. Missing Properties
function f7({ missingProp }) { output(missingProp); }
f7({ present: true });

const a7 = ({ missingProp }) => { output(missingProp); };
a7({ present: true });

// 6. Empty Object Pattern
function f8({}) { output("empty"); }
f8({ x: 15 });

const a8 = ({}) => { output("empty"); };
a8({ x: 15 });

// 7. Basic Array Destructuring
function f9([first, second]) { output(first, second); }
f9([10, 20]);

const a9 = ([first, second]) => { output(first, second); };
a9([10, 20]);

// 8. Array Destructuring with Elision
function f10([a, , b, , , c]) { output(a, b, c); }
f10([1, 2, 3, 4, 5, 6]);

const a10 = ([a, , b, , , c]) => { output(a, b, c); };
a10([1, 2, 3, 4, 5, 6]);

// 9. Array Destructuring with Trailing Elision
function f11([d, e, ,]) { output(d, e); }
f11([7, 8, 9, 10]);

const a11 = ([d, e, ,]) => { output(d, e); };
a11([7, 8, 9, 10]);

// 10. Rest Elements in Array Destructuring
function f13([head, ...tail]) { output(head, tail[0], tail[1], tail[2], tail.length); }
f13([1, 2, 3, 4]);

const a13 = ([head, ...tail]) => { output(head, tail[0], tail[1], tail[2], tail.length); };
a13([1, 2, 3, 4]);

// 11. Nested Array Destructuring
function f14([x, [y, z]]) { output(x, y, z); }
f14([1, [2, 3]]);

const a14 = ([x, [y, z]]) => { output(x, y, z); };
a14([1, [2, 3]]);

// 12. Rest Elements as a Pattern
function f15([start, ...[...restUnpacked]]) { output(start, restUnpacked[0], restUnpacked[1], restUnpacked.length); }
f15([10, 20, 30]);

const a15 = ([start, ...[...restUnpacked]]) => { output(start, restUnpacked[0], restUnpacked[1], restUnpacked.length); };
a15([10, 20, 30]);

// 13. Array Rest resolving into an Object pattern
function f16([n, ...{ length: len, 0: p }]) { output(n, len, p); }
f16([37, 38, 39]);

const a16 = ([n, ...{ length: len, 0: p }]) => { output(n, len, p); };
a16([37, 38, 39]);

// 14. Object inside Array
function f17([{ id: id1 }, { id: id2 }]) { output(id1, id2); }
f17([{ id: 1 }, { id: 2 }]);

const a17 = ([{ id: id1 }, { id: id2 }]) => { output(id1, id2); };
a17([{ id: 1 }, { id: 2 }]);

// 15. Array inside Object
function f18({ coords: [x2, y2] }) { output(x2, y2); }
f18({ coords: [45.5, -122.6] });

const a18 = ({ coords: [x2, y2] }) => { output(x2, y2); };
a18({ coords: [45.5, -122.6] });

// 16. Deeply mixed
function f19([, { tags: [, secondTag, ...otherTags], status: status2 }]) {
    output(secondTag, status2, otherTags[0]);
}
f19([{ tags: ["ignore"] }, { tags: ["js", "web", "node"], status: "active" }]);

const a19 = ([, { tags: [, secondTag, ...otherTags], status: status2 }]) => {
    output(secondTag, status2, otherTags[0]);
};
a19([{ tags: ["ignore"] }, { tags: ["js", "web", "node"], status: "active" }]);

// 17. Parameter Defaults mixed with Parameter Destructuring
function f20({ a } = { a: 100 }, [b, c] = [200, 300], d = 400) { output(a, b, c, d); }
f20();

const a20 = ({ a } = { a: 100 }, [b, c] = [200, 300], d = 400) => { output(a, b, c, d); };
a20();

// 18. Top Level Rest mixed with Parameter Destructuring
function f21({ a }, [b, c], ...restArgs) { output(a, b, c, restArgs[0], restArgs[1]); }
f21({ a: 1 }, [2, 3], 4, 5);

const a21 = ({ a }, [b, c], ...restArgs) => { output(a, b, c, restArgs[0], restArgs[1]); };
a21({ a: 1 }, [2, 3], 4, 5);

// 19. Plain function expressions
const e1 = function({ a, b }) { output(a, b); };
e1({ a: 1, b: 2 });

// 20. Class methods, object methods, constructors (including computed)
class C {
    constructor({ a }) { output(a); }
    m({ b }) { output(b); }
    ["m" + "2"]({ c }) { output(c); }
}
new C({ a: 1 }).m({ b: 2 });
new C({ a: 1 }).m2({ c: 3 });

const obj = {
    m({ d }) { output(d); },
    ["m" + "2"]({ e }) { output(e); }
};
obj.m({ d: 4 });
obj.m2({ e: 5 });

// 21. Inner default values (unsupported by parser.js)
// (({a = 5}) => { output(a); })({});

// 22. Computed property keys (unsupported by parser.js)
// (({["ab"]: a}) => { output(a); })({ab: 5});

// 23. Empty nested destructuring patterns
const a23 = (({b: [{}]}, {a}) => { output(a); });
a23({b: [{1: 2}]}, {a: 2});
