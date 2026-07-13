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

import Foundation
import Testing

@testable import Fuzzilli

struct VariableMapTests {
    @Test func testBasicVariableMapFeatures() {
        var m = VariableMap<Int>()
        #expect(m.isEmpty)

        #expect(!m.contains(v(0)))
        #expect(m[v(0)] == nil)

        m[v(42)] = 42
        #expect(m.contains(v(42)))
        #expect(m[v(42)] == 42)

        m[v(0)] = 0
        #expect(m.contains(v(0)))
        #expect(m[v(0)] == 0)
        #expect(!m.contains(v(1)))
        #expect(m[v(1)] == nil)
        m[v(1)] = 1
        #expect(m.contains(v(1)))
        #expect(m[v(1)] == 1)

        m.removeValue(forKey: v(1))
        #expect(!m.contains(v(1)))
        #expect(m[v(1)] == nil)
        #expect(m.contains(v(0)))
        #expect(m[v(0)] == 0)

        m.removeAll()
        #expect(m == VariableMap<Int>())
        #expect(m.isEmpty)

        m[v(43)] = 100
        #expect(!m.isEmpty)
        m.removeValue(forKey: v(43))
        #expect(m.isEmpty)
    }

    @Test func testVariableMapEquality() {
        var m1 = VariableMap<Bool>()
        #expect(m1 == m1)

        var m2 = VariableMap<Bool>()
        #expect(m1 == m2)

        for i in 0..<128 {
            let val = Bool.random()
            m1[v(i)] = val
            m2[v(i)] = val
        }
        #expect(m1 == m2)

        m1.removeValue(forKey: v(2))
        #expect(m1 != m2)
        m2.removeValue(forKey: v(2))
        #expect(m1 == m2)

        // Add another 128 elements and compare with a new map built up in the opposite order
        for i in 128..<256 {
            let val = Bool.random()
            m2[v(i)] = val
        }

        var m3 = VariableMap<Bool>()
        #expect(m1 != m3)

        for i in (0..<256).reversed() {
            m3[v(i)] = m2[v(i)] ?? false
        }
        #expect(m1 != m3)
        m3.removeValue(forKey: v(2))
        #expect(m3 == m2)

        // Remove last 128 variables from m3, should now be equal to m1
        for i in 128..<256 {
            m3.removeValue(forKey: v(i))
        }
        #expect(m3 == m1)

        // Remove all variables from m2, should now be equal to an empty map
        for i in 0..<256 {
            m2.removeValue(forKey: v(i))
        }
        #expect(m2 == VariableMap<Bool>())
    }

    @Test func testVariableMapEncoding() {
        var map = VariableMap<Int>()

        for i in 0..<1000 {
            withProbability(0.75) {
                map[v(i)] = Int.random(in: 0..<1_000_000)
            }
        }

        let encoder = JSONEncoder()
        let decoder = JSONDecoder()

        let data = try! encoder.encode(map)
        let mapCopy = try! decoder.decode(VariableMap<Int>.self, from: data)

        #expect(map == mapCopy)
    }

    @Test func testVariableMapHashing() {
        var map1 = VariableMap<Int>()
        var map2 = VariableMap<Int>()

        for i in 0..<1000 {
            withProbability(0.75) {
                let value = Int.random(in: 0..<1_000_000)
                map1[v(i)] = value
                map2[v(i)] = value
            }
        }

        #expect(map1 == map2)
        #expect(map1.hashValue == map2.hashValue)
    }

    @Test func testVariableMapIteration() {
        var map = VariableMap<Int>()
        for i in 0..<1000 {
            withProbability(0.5) {
                map[v(i)] = Int.random(in: 0..<1_000_000)
            }
        }

        var copy = VariableMap<Int>()
        for (v, t) in map {
            copy[v] = t
        }
        #expect(map == copy)
    }

    @Test func testEmptyVariableMapForHoles() {
        let m = VariableMap<Int>()

        #expect(m.hasHoles() == false)
    }

    @Test func testDenseVariableMapForHoles() {
        var m = VariableMap<Int>()

        for i in 0..<20 {
            m[v(i)] = Int.random(in: 0..<20)
        }

        #expect(m.hasHoles() == false)
    }

    @Test func testForHolesAfterLastElementRemoval() {
        var m = VariableMap<Int>()

        let mapSize = 15
        for i in 0..<mapSize {
            m[v(i)] = Int.random(in: 0..<20)
        }
        m.removeValue(forKey: v(mapSize - 1))

        #expect(m.hasHoles() == false)
    }

    @Test func testForHolesAfterFirstElementRemoval() {
        var m = VariableMap<Int>()

        let mapSize = 15
        for i in 0..<mapSize {
            m[v(i)] = Int.random(in: 0..<20)
        }
        m.removeValue(forKey: v(0))

        #expect(m.hasHoles() == true)
    }

    @Test func testForHolesAfterArbitraryElementRemoval() {
        var m = VariableMap<Int>()

        let mapSize = 15
        for i in 0..<mapSize {
            m[v(i)] = Int.random(in: 0..<20)
        }
        m.removeValue(forKey: v(Int.random(in: 0..<mapSize - 1)))

        #expect(m.hasHoles() == true)
    }
}
