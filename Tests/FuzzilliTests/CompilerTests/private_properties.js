if (typeof output === 'undefined') output = console.log;

// 1. Basic Private Fields and normal methods
class C1 {
  #x = 10;

  getX() { return this.#x; }
  setX(v) { this.#x = v; }
}
let c1 = new C1();
output("C1 init:", c1.getX());
c1.setX(42);
output("C1 update:", c1.getX());


// 2. Private Getters and Setters inside the class
class C2 {
  #a;
  #b;

  constructor(a, b) {
    this.#a = a;
    this.#b = b;
  }

  get #sum() {
    return this.#a + this.#b;
  }

  set #sum(v) {
    this.#a = v;
    this.#b = v;
  }

  #add(a, b) {
    return a + b;
  }

  test() {
    output("C2 sum:", this.#sum);
    this.#sum = 100;
    output("C2 a:", this.#a, "b:", this.#b);
    output("C2 add method:", this.#add(this.#a, this.#b));
  }
}
let c2 = new C2(5, 5);
c2.test();


// 3. Static Private Fields, Getters, Setters, and Methods
class C3 {
  static #p = "static private";

  static get p() { return this.#p; }
  static set p(v) { this.#p = v; }
  static #m() { return "static method"; }

  static test() {
    output("C3 p:", this.#p);
    this.#p = "changed";
    output("C3 p updated:", this.#p);
    output("C3 m():", this.#m());
  }
}
output("C3 external p:", C3.p);
C3.test();
C3.p = "changed again";
output("C3 external p updated:", C3.p);


// 4. Nested Classes with Private Properties
class C4 {
  #x = "C4";

  #run() {
    class C5 {
      #y = "C5";
      #run2() { return this.#y; }
      test() { return this.#run2(); }
    }
    let c5 = new C5();
    return this.#x + " " + c5.test();
  }

  test() {
    output("C4 nested:", this.#run());
  }
}
let c4 = new C4();
c4.test();


// 5. Cross-Instance Private Property Access
class C6 {
  #id;
  constructor(id) {
    this.#id = id;
  }

  compare(other) {
    return this.#id === other.#id;
  }

  transferTo(other) {
    let temp = this.#id;
    this.#id = other.#id;
    other.#id = temp;
  }

  print() {
    output("C6 id:", this.#id);
  }
}
let c6_a = new C6(100);
let c6_b = new C6(200);
output("C6 compare:", c6_a.compare(c6_b));
output("C6 compare self:", c6_a.compare(c6_a));
c6_a.transferTo(c6_b);
c6_a.print();
c6_b.print();

// 6. Looping with Private Properties
class C7 {
  #arr = [1, 2, 3];

  #process(item) {
    return item * 2;
  }

  run() {
    for (let i = 0; i < this.#arr.length; i = i + 1) {
      let val = this.#arr[i];
      output("C7 loop item:", this.#process(val));
    }

    // For of loop
    for (let item of this.#arr) {
      output("C7 for-of:", this.#process(item));
    }
  }
}
let c7 = new C7();
c7.run();

// 7. Updating Private Properties (Compound Assignment)
class C8 {
  #count = 0;

  increment(amount) {
    this.#count += amount;
    return this.#count;
  }

  multiply(factor) {
    this.#count *= factor;
    return this.#count;
  }
}
let c8 = new C8();
output("C8 increment:", c8.increment(5));
output("C8 multiply:", c8.multiply(2));

// 8. Destructuring to Private Properties
class C9 {
  #destructTarget;

  constructor() {
    this.#destructTarget = 0;
  }

  run() {
    ({a: this.#destructTarget} = {a: 42});
    return this.#destructTarget;
  }
}
let c9 = new C9();
output("C9 destructuring:", c9.run());

// 9. Optional Chaining with Private Properties and Methods
class C10 {
  #prop = 99;
  #method() { return "called private method"; }

  run(obj) {
    let resultProp = obj?.#prop;
    let resultMethod = obj?.#method();
    output("C10 optional prop:", resultProp);
    output("C10 optional method:", resultMethod);
  }
}
let c10 = new C10();
c10.run(c10);
c10.run(undefined);

// 10. Spread Syntax with Private Methods
class C11 {
  #method(a, b, c, d) { return a + b + c + d; }

  run(args1, args3) {
    output("C11 spread:", this.#method(...args1, 20, ...args3));
  }
}
let c11 = new C11();
c11.run([10], [30, 40]);

// 11. Property Privacy Verification
class P1 {
  #field = 1;
  get #getter() { return 2; }
  set #setter(v) {}
  #method() { return 4; }
}
let p1 = new P1();
if (p1.field !== undefined) throw "Mistakenly un-privated property!";
if (p1.getter !== undefined) throw "Mistakenly un-privated getter!";
if (p1.setter !== undefined) throw "Mistakenly un-privated setter!";
if (p1.method !== undefined) throw "Mistakenly un-privated method!";

