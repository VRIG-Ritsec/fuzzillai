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

    func testSuccessUpdateKeepsElementSelectable() {
        // Create a list with one item
        let list = RuntimeWeightedList<String>([("Item", 1)])
        let item = list.weightedElement()

        // RuntimeWeightedList uses rewards > 1.0 as success signals.
        for _ in 0..<10 {
            list.update(item, reward: 1.1)
        }

        XCTAssertEqual(list.weightedElement(), "Item")
    }

    func testFailureUpdateKeepsElementSelectable() {
        // Create a list with one item
        let list = RuntimeWeightedList<String>([("Item", 1)])
        let item = list.weightedElement()

        // Rewards <= 1.0 are treated as failures.
        for _ in 0..<10 {
            list.update(item, reward: 1.0)
        }

        XCTAssertEqual(list.weightedElement(), "Item")

        let desc = list.description
        XCTAssert(desc.contains("Item"))
    }

    func testSelectionDistribution() {
        // A receives success updates and B receives failure updates.
        let list = RuntimeWeightedList<String>([("A", 1), ("B", 1)])

        for _ in 0..<50 {
            list.update("A", reward: 1.1)
            list.update("B", reward: 1.0)
        }

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
