if (typeof output === 'undefined') output = console.log;

class GeneratorExample {
    *generatorMethod() {
        output("generatorMethod start");
        yield 1;
        output("generatorMethod end");
        return "done";
    }
    *['computedGen']() {
        yield 2;
    }
    *#privateGen() {
        yield 3;
    }
    callPrivate() {
        return this.#privateGen();
    }
    static *staticGen() {
        yield 4;
    }
    static *['staticComputedGen']() {
        yield 5;
    }
    static *#staticPrivateGen() {
        yield 6;
    }
    static callStaticPrivate() {
        return this.#staticPrivateGen();
    }
}

let genObj = {
    *genObjMethod() { yield 7; },
    *['computedGenObj']() { yield 8; }
};

function runGenTests() {
    let ex = new GeneratorExample();
    let it = ex.generatorMethod();
    output(it.next().value);
    output(it.next().value);

    output(ex.computedGen().next().value);
    output(ex.callPrivate().next().value);

    output(GeneratorExample.staticGen().next().value);
    output(GeneratorExample.staticComputedGen().next().value);
    output(GeneratorExample.callStaticPrivate().next().value);

    output(genObj.genObjMethod().next().value);
    output(genObj.computedGenObj().next().value);
}

runGenTests();
