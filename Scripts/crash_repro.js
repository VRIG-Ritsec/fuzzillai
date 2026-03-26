 function* f0() {
    function F4() {
    }
    function F8() {
        if (!new.target) { throw ''; }
        try { this(F4); } catch (e) {}
    }
}
