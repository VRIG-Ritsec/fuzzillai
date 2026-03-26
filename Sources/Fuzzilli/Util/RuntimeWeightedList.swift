
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

/// A list where each element is selected using Thompson Sampling.
/// Each element maintains Beta distribution parameters (alpha, beta) representing
/// successes and failures. Selection samples from each element's Beta distribution
/// and picks the one with the highest sampled value.
public class RuntimeWeightedList<Element: Equatable>: WeightedList<Element> {
    private var elements = [(
        elem: Element,
        weight: Int,
        cumulativeWeight: Int,
        alpha: Double,  // successes + 1 (Beta prior)
        beta: Double    // failures + 1 (Beta prior)
    )]()
    
    // For compatibility with existing code
    private(set) var totalRuntimeWeight: Float = 0.0
    
    // cache of most recently selected mutators
    private var lastElements: [Element] = []

    public override init(_ values: [(Element, Int)]) {
        super.init()
        totalWeight = 0
        for (e, w) in values {
            // Initialize alpha with the configured weight, beta=1
            // So a mutator with weight 30 starts with alpha=30, beta=1
            append(e, withWeight: w, alpha: Double(w), beta: 1.0)
        }
    }

    public convenience init(from weightedList: WeightedList<Element>) {
        // Use iteratorWithWeights to preserve the original weights
        var values: [(Element, Int)] = []
        for (elem, weight) in weightedList.iteratorWithWeights() {
            values.append((elem, weight))
        }
        self.init(values)
    }

    public var description: String {
        // Calculate total alpha across all elements (represents accumulated successes)
        var totalAlpha: Double = 0.0
        for e in elements {
            totalAlpha += e.alpha
        }
        
        var str = "Total α(successes): \(String(format: "%.1f", totalAlpha)) ["
        for (i, e) in elements.enumerated() {
            if i > 0 { str += ", " }
            // Show alpha (successes), beta (failures), and the Beta mean (expected success rate)
            let mean = e.alpha / (e.alpha + e.beta)
            str += "\(e.elem): [α(successes)=\(String(format: "%.1f", e.alpha)), β(failures)=\(String(format: "%.1f", e.beta)), μ(mean)=\(String(format: "%.4f", mean))]"
        }
        str += "]"
        return str
    }

    /// Updates the Beta distribution parameters for an element.
    /// 
    /// - Parameters:
    ///   - elem: The element to update.
    ///   - reward: The reward signal. Positive values increment alpha (success),
    ///             values close to zero increment beta (failure).
    public func update(_ elem: Element, reward: Float) {
        for i in 0..<elements.count {
            if elements[i].elem == elem {
                // Reward > 1.0 means success (found interesting/crash) - add to alpha
                // Reward <= 1.0 means failure - add the reward value to beta
                // This allows tiny penalties (0.000001) to barely affect beta
                if reward > 1.0 {
                    // Big success - add proportionally to alpha
                    elements[i].alpha += Double(reward)
                } else {
                    // Failure - add reward value to beta (smaller = less penalty)
                    // e.g., 0.000001 adds only 0.000001 to beta per execution
                    elements[i].beta += Double(reward)
                }
                break
            }
        }
        
        // Update totalRuntimeWeight for compatibility (use mean of Beta)
        totalRuntimeWeight = 0.0
        for e in elements {
            totalRuntimeWeight += Float(e.alpha / (e.alpha + e.beta))
        }
    }

    /// Updates parameters for a batch of elements.
    public func updateBatch(_ activeElements: [Element], reward: Float) {
        for elem in activeElements {
            update(elem, reward: reward)
        }
    }

    public override func filter(_ isIncluded: (Element) -> Bool) -> RuntimeWeightedList<Element> {
        return self
    }
    
    public func append(_ elem: Element, withWeight weight: Int, alpha: Double, beta: Double) {
        assert(weight > 0)
        totalWeight += weight
        elements.append((elem, weight, totalWeight, alpha, beta))
        totalRuntimeWeight += Float(alpha / (alpha + beta))
    }
    
    // For compatibility - redirect to new append
    public func append(_ elem: Element, withWeight weight: Int, runtimeWeight: Float) {
        append(elem, withWeight: weight, alpha: 1.0, beta: 1.0)
    }

    /// Selects an element using Thompson Sampling.
    /// Samples from each element's Beta distribution and returns the one with highest sample.
    public func weightedElement() -> Element {
        var bestIndex = 0
        var bestSample: Double = -1.0
        
        for i in 0..<elements.count {
            // Sample from Beta(alpha, beta) distribution
            let sample = sampleBeta(alpha: elements[i].alpha, beta: elements[i].beta)
            if sample > bestSample {
                bestSample = sample
                bestIndex = i
            }
        }
        
        lastElements.append(elements[bestIndex].elem)
        return elements[bestIndex].elem
    }
    
    /// Sample from Beta distribution using the gamma distribution method.
    /// Beta(a, b) = Gamma(a, 1) / (Gamma(a, 1) + Gamma(b, 1))
    private func sampleBeta(alpha: Double, beta: Double) -> Double {
        let x = sampleGamma(shape: alpha)
        let y = sampleGamma(shape: beta)
        return x / (x + y)
    }
    
    /// Sample from Gamma distribution using Marsaglia and Tsang's method.
    private func sampleGamma(shape: Double) -> Double {
        if shape < 1.0 {
            // For shape < 1, use: Gamma(shape) = Gamma(shape + 1) * U^(1/shape)
            let u = Double.random(in: 0.0..<1.0)
            return sampleGamma(shape: shape + 1.0) * pow(u, 1.0 / shape)
        }
        
        let d = shape - 1.0 / 3.0
        let c = 1.0 / sqrt(9.0 * d)
        
        while true {
            var x: Double
            var v: Double
            
            repeat {
                x = sampleStandardNormal()
                v = 1.0 + c * x
            } while v <= 0.0
            
            v = v * v * v
            let u = Double.random(in: 0.0..<1.0)
            
            if u < 1.0 - 0.0331 * x * x * x * x {
                return d * v
            }
            
            if log(u) < 0.5 * x * x + d * (1.0 - v + log(v)) {
                return d * v
            }
        }
    }
    
    /// Sample from standard normal distribution using Box-Muller transform.
    private func sampleStandardNormal() -> Double {
        let u1 = Double.random(in: Double.leastNonzeroMagnitude..<1.0)
        let u2 = Double.random(in: 0.0..<1.0)
        return sqrt(-2.0 * log(u1)) * cos(2.0 * .pi * u2)
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
