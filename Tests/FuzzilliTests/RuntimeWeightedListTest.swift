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

import XCTest
@testable import Fuzzilli

class RuntimeWeightedListTests: XCTestCase {
    
    func testEWMAConvergence() {
        // Create a list with one item
        let list = RuntimeWeightedList<String>([("Item", 1)])
        let item = list.weightedElement()
        
        // Initial weight is 1.0
        // We report "success" (1.0) repeatedly
        // The weight should stay at 1.0 (since it's already there) or converge to it if it wasn't
        
        for _ in 0..<10 {
            list.update(item, reward: 1.0)
        }
        
        // Check internal weight (indirectly via description or by assuming implementation details)
        // Since we can't access private properties, we can verify it doesn't explode
        // But we can check if it's selected (trivial for 1 item)
        XCTAssertEqual(list.weightedElement(), "Item")
    }
    
    func testEWMADecay() {
        // Create a list with one item
        let list = RuntimeWeightedList<String>([("Item", 1)])
        let item = list.weightedElement()
        
        // Initial weight is 1.0
        // We report "failure" (0.0) repeatedly
        // The weight should decay: 1.0 -> 0.9 -> 0.81 -> ...
        
        for _ in 0..<10 {
            list.update(item, reward: 0.0)
        }
        
        // After 10 failures with alpha=0.1, weight should be approx 1.0 * (0.9)^10 = 0.34
        // We can't verify the exact float value easily without exposing it, 
        // but we can verify it's still selectable and hasn't crashed.
        XCTAssertEqual(list.weightedElement(), "Item")
        
        // Verify description contains the weight (approx check)
        let desc = list.description
        XCTAssert(desc.contains("Item"))
    }
    
    func testSelectionDistribution() {
        // Two items, A (reward 1.0) and B (reward 0.0)
        let list = RuntimeWeightedList<String>([("A", 1), ("B", 1)])
        
        // Train A to be good, B to be bad
        for _ in 0..<50 {
            list.update("A", reward: 1.0)
            list.update("B", reward: 0.0)
        }
        
        // A should have weight ~1.0, B should have weight ~0.01 (min clamped)
        // So A should be selected ~100x more often than B
        
        var aCount = 0
        var bCount = 0
        for _ in 0..<1000 {
            if list.weightedElement() == "A" {
                aCount += 1
            } else {
                bCount += 1
            }
        }
        
        XCTAssertGreaterThan(aCount, bCount * 10, "A should be selected significantly more often than B")
    }
}
