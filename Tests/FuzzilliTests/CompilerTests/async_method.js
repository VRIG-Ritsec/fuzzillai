if (typeof output === 'undefined') output = console.log;

class AsyncExample {
    async asyncMethod() {
        output("asyncMethod start");
        await 1;
        output("asyncMethod end");
        return "asyncMethod result";
    }
    async ['computedAsync']() {
        await 2;
        return "computedAsync result";
    }
    async #privateAsync() {
        await 3;
        return "privateAsync result";
    }
    callPrivate() {
        return this.#privateAsync();
    }
    static async staticAsync() {
        await 4;
        return "staticAsync result";
    }
    static async ['staticComputedAsync']() {
        await 5;
        return "staticComputedAsync result";
    }
    static async #staticPrivateAsync() {
        await 6;
        return "staticPrivateAsync result";
    }
    static callStaticPrivate() {
        return this.#staticPrivateAsync();
    }
}

let asyncObj = {
    async asyncObjMethod() {
        await 7;
        return "asyncObjMethod result";
    },
    async ['computedAsyncObj']() {
        await 8;
        return "computedAsyncObj result";
    }
};

class AsyncGeneratorExample {
    async *asyncGenMethod() {
        yield 1;
        await 2;
        yield 3;
    }
    async *['computedAsyncGen']() {
        yield 4;
    }
    async *#privateAsyncGen() {
        yield 5;
    }
    callPrivate() {
        return this.#privateAsyncGen();
    }
    static async *staticAsyncGen() {
        yield 6;
    }
    static async *['staticComputedAsyncGen']() {
        yield 7;
    }
    static async *#staticPrivateAsyncGen() {
        yield 8;
    }
    static callStaticPrivate() {
        return this.#staticPrivateAsyncGen();
    }
}

let asyncGenObj = {
    async *asyncGenObjMethod() {
        yield 9;
    },
    async *['computedAsyncGenObj']() {
        yield 10;
    }
};

async function runTests() {
    let ex = new AsyncExample();
    output(await ex.asyncMethod());
    output(await ex.computedAsync());
    output(await ex.callPrivate());
    output(await AsyncExample.staticAsync());
    output(await AsyncExample.staticComputedAsync());
    output(await AsyncExample.callStaticPrivate());

    output(await asyncObj.asyncObjMethod());
    output(await asyncObj.computedAsyncObj());

    let genEx = new AsyncGeneratorExample();
    let it = genEx.asyncGenMethod();
    output((await it.next()).value);
    output((await it.next()).value);
    output((await it.next()).done);

    it = genEx.computedAsyncGen();
    output((await it.next()).value);

    it = genEx.callPrivate();
    output((await it.next()).value);

    it = AsyncGeneratorExample.staticAsyncGen();
    output((await it.next()).value);

    it = AsyncGeneratorExample.staticComputedAsyncGen();
    output((await it.next()).value);

    it = AsyncGeneratorExample.callStaticPrivate();
    output((await it.next()).value);

    it = asyncGenObj.asyncGenObjMethod();
    output((await it.next()).value);

    it = asyncGenObj.computedAsyncGenObj();
    output((await it.next()).value);
}

runTests().then(() => output("Done")).catch(e => output("Error: " + e));
