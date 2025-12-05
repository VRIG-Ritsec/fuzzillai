
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

/// A list where each element also has a weight, which determines how frequently it is selected by randomElement().
/// For example, an element with weight 10 is 2x more likely to be selected by randomElement() than an element with weight 5.
public class RuntimeWeightedList<Element: Equatable>: WeightedList<Element> {
    private var elements = [(
        elem: Element,
        weight: Int,
        cumulativeWeight: Int,
        runtimeWeight: Float,
        cumulativeRuntimeWeight: Float
    )]()
    private(set) var totalRuntimeWeight: Float = 0.0
    
    /// The learning rate (alpha) for EWMA.
    /// Controls how much recent results influence the weight.
    /// Higher values = faster adaptation but more noise.
    /// Lower values = smoother but slower adaptation.
    private let learningRate: Float = 0.1
    // cache of most recently selected mutators
    private var lastElements: [Element] = []

    public override init(_ values: [(Element, Int)]) {
        super.init()
        totalWeight = values.count
        for (e, _) in values {
            append(e, withWeight: 1, runtimeWeight: 1.0)
        }
    }

    public convenience init(from weightedList: WeightedList<Element>) {
        let values = weightedList.map { ($0, 1) }
        self.init(values)
    }

    public var description: String {
        var str = "Total: \(String(format: "%.2f", totalRuntimeWeight)) ["
        for (i, e) in elements.enumerated() {
            if i > 0 { str += ", " }
            str += "\(e.elem): \(String(format: "%.2f", e.runtimeWeight))"
        }
        str += "]"
        return str
    }

    /// Updates the weight of an element using Exponential Weighted Moving Average (EWMA).
    ///
    /// Formula: NewWeight = (1 - alpha) * OldWeight + alpha * Reward
    ///
    /// - Parameters:
    ///   - elem: The element to update.
    ///   - reward: The reward signal (e.g., 1.0 for success, 0.0 for failure).
    public func update(_ elem: Element, reward: Float) {
        for i in 0..<elements.count {
            if elements[i].elem == elem {
                let oldWeight = elements[i].runtimeWeight
                
                // EWMA update
                var newWeight = (1.0 - learningRate) * oldWeight + learningRate * reward
                
                // Clamp weights to keep them reasonable (e.g., never exactly 0)
                // We use a base weight of 0.01 to ensure every mutator has a non-zero chance.
                if newWeight < 0.01 {
                    newWeight = 0.01
                } else if newWeight > 100.0 {
                    newWeight = 100.0
                }
                
                elements[i].runtimeWeight = newWeight
                break
            }
        }
        
        // Recompute cumulative weights
        var currentCumulative: Float = 0.0
        for i in 0..<elements.count {
            currentCumulative += elements[i].runtimeWeight
            elements[i].cumulativeRuntimeWeight = currentCumulative
        }
        totalRuntimeWeight = currentCumulative
    }

    /// Updates weights for a batch of elements.
    ///
    /// - Parameters:
    ///   - activeElements: The elements that were active.
    ///   - reward: The reward to assign to these elements.
    public func updateBatch(_ activeElements: [Element], reward: Float) {
        for elem in activeElements {
            update(elem, reward: reward)
        }
    }

    public override func filter(_ isIncluded: (Element) -> Bool) -> RuntimeWeightedList<Element> {
        //var r: RuntimeWeightedList<Element> = RuntimeWeightedList()
        //for (e, w, cw, rw, crw) in elements where isIncluded(e) {
        //    append(e, withWeight: w)
        //}
        return self
    }
    
    public func append(_ elem: Element, withWeight weight: Int, runtimeWeight: Float) {
        assert(weight > 0)
        let previousCumulativeWeight = totalRuntimeWeight
        totalRuntimeWeight += runtimeWeight
        totalWeight += weight
        elements.append((elem, weight, totalWeight, runtimeWeight, totalRuntimeWeight))
    }

    public func weightedElement() -> Element {
        let k = Float.random(in: 0.0..<totalRuntimeWeight)
        for i in 0..<elements.count {
            if elements[i].cumulativeRuntimeWeight > k {
                lastElements.append(elements[i].elem)
                return elements[i].elem
            }
        }
        return elements.last!.elem 
    }

    public func getLastElements() -> [Element] {
        return lastElements
    }

    public func popLastElement() -> Void {
        let _ = lastElements.popLast()
    }

    public func flushLastElements() -> Void {
        lastElements = []
    }

    // Override to use our own elements array (the parent class's is shadowed and empty)
    public override func makeIterator() -> Array<Element>.Iterator {
        return elements.map({ $0.elem }).makeIterator()
    }
}
