// Copyright 2025 Google LLC
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

import Collections

public class ContextGraph {
    // This is an edge, it holds all Generators that provide the `to` context at some point.
    // Another invariant is that each Generator will keep the original context, i.e. it will return to the `from` context.
    struct EdgeKey: Hashable {
        let from: Context
        let to: Context

        public init(from: Context, to: Context) {
            self.from = from
            self.to = to
        }
    }

    // This struct describes the value of an edge in this ContextGraph.
    // It holds all `CodeGenerator`s that go from one context to another in a direct transition.
    public struct GeneratorEdge {
        var generators: [CodeGenerator] = []

        // How many generator stubs we at *most* need to schedule for this edge.
        public var weight: Int = 0

        // Adds a generator to this Edge.
        public mutating func addGenerator(_ generator: CodeGenerator) {
            generators.append(generator)
            weight = max(weight, generator.parts.count)
        }
    }

    // This is a Path that goes from one Context to another via (usually more than one) `GeneratorEdge`. It is a full path in the Graph.
    // Every Edge in a path may be provided by various CodeGenerators.
    public struct Path {
        let edges: [GeneratorEdge]

        // For each edge, pick a random Generator that provides that edge.
        public func randomConcretePath() -> [CodeGenerator] {
            edges.map { edge in
                chooseUniform(from: edge.generators)
            }
        }
    }

    // This is the Graph, each pair of from and to, maps to a `GeneratorEdge`.
    var edges: [EdgeKey: GeneratorEdge] = [:]

    public init(
        for generators: WeightedList<CodeGenerator>, isBundle: Bool, withLogger logger: Logger
    ) {
        assertBasicConsistency(in: generators)
        warnOfSuspiciousContexts(in: generators, isBundle: isBundle, withLogger: logger)

        // One can still try to build in a context that doesn't have generators, this will be caught in the build function, if we fail to find any suitable generator.
        // Otherwise we could assert here that the sets are equal.
        // One example is a GeneratorStub that opens a context, then calls build manually without it being split into two stubs where we have a yield point to assemble a synthetic generator.
        self.edges = [:]

        for generator in generators {
            for providableContext in generator.providedContexts {
                let edge = EdgeKey(from: generator.requiredContext, to: providableContext)
                self.edges[edge, default: GeneratorEdge()].addGenerator(generator)
            }
        }
    }

    // Check that every part that provides something is used by the next part of the Generator. This is a simple consistency check.
    private func assertBasicConsistency(in generators: WeightedList<CodeGenerator>) {
        for generator in generators where generator.parts.count > 1 {
            var currentContext = Context(generator.parts[0].providedContext)

            for i in 1..<generator.parts.count {
                let stub = generator.parts[i]

                guard
                    stub.requiredContext.matches(currentContext)
                        || (stub.requiredContext.isJavascript && currentContext.isEmpty)
                        // If the requiredContext is more than two, we should never provide a context.
                        // See `CodeGenerator` for details.
                        || (!stub.requiredContext.isSingle && currentContext.isEmpty)
                else {
                    fatalError("Inconsistent requires/provides Contexts for \(generator.name)")
                }

                currentContext =
                    stub.providedContext.isEmpty ? currentContext : Context(stub.providedContext)
            }
        }
    }

    private func warnOfSuspiciousContexts(
        in generators: WeightedList<CodeGenerator>, isBundle: Bool, withLogger logger: Logger
    ) {
        // Technically we don't need any generator to emit the .bundle or .javascript context, as they are provided by the toplevel.
        var providedContexts = Set<Context>([isBundle ? .bundle : .javascript])
        var requiredContexts = Set<Context>()

        for generator in generators {
            generator.providedContexts.forEach { ctx in
                providedContexts.insert(ctx)
            }
            requiredContexts.insert(generator.requiredContext)
        }

        for generator in generators {
            // Now check which generators don't have providers
            if !providedContexts.contains(generator.requiredContext) {
                if !isBundle && generator.requiredContext == .bundle {
                    continue
                }
                logger.warning(
                    "Generator \(generator.name) cannot be run as it doesn't have a Generator that can provide this context."
                )

            }

            // All provided contexts must be required by some generator.
            if !generator.providedContexts.allSatisfy({
                requiredContexts.contains($0)
            }) {
                logger.warning(
                    "Generator \(generator.name) provides a context that is never required by another generator \(generator.providedContexts)"
                )
            }
        }
    }

    /// Get a shortest path (the least amount of generator stubs) from the `from` Context to the `to` Context. Returns an arbitrary one if there are several.
    /// TODO: Do this initially and cache all results?
    func getShortestPath(from src: Context, to dst: Context) -> [EdgeKey]? {
        guard src.rawValue.nonzeroBitCount <= 1 else {
            fatalError("getShortestPath called with multi-bit src context: \(src)")
        }
        guard dst.rawValue.nonzeroBitCount <= 1 else {
            fatalError("getShortestPath called with multi-bit dst context: \(dst)")
        }
        // Do simple BFS to find a shortest path, considering the edge weights.
        var queue: Deque<([Context], Int)> = [([src], 0)]
        var seenNodes: [Context: Int] = [:]

        var maybeFoundPath: [Context]? = nil
        var foundWeight = Int.max
        while !queue.isEmpty {
            let (currentPath, currentWeight) = queue.popFirst()!

            let currentNode = currentPath.last!

            if currentNode == dst {
                if currentWeight < foundWeight {
                    maybeFoundPath = currentPath
                    foundWeight = currentWeight
                }
                continue
            }

            // Get all possible edges from here on and push all of those to the queue.
            for edge in self.edges
            where edge.key.from == currentNode {
                let endpoint = edge.key.to
                let totalWeight = currentWeight + edge.value.weight
                if let oldWeight = seenNodes[endpoint] {
                    if oldWeight <= totalWeight {
                        // We've already found a shorter or equally short path to `endpoint`.
                        continue
                    }
                }
                seenNodes[endpoint] = totalWeight
                queue.append((currentPath + [endpoint], totalWeight))
            }
        }

        guard let foundPath = maybeFoundPath else {
            return nil
        }

        // Reduce foundPath to Edges structs that we can easily look up.
        return zip(foundPath, foundPath[1...]).map(EdgeKey.init)
    }

    func getGenerators(from src: Context, to dst: Context) -> GeneratorEdge? {
        self.edges[EdgeKey(from: src, to: dst)]
    }

    func getReachableContexts(from src: Context) -> Set<Context> {
        // Do simple BFS to find all reachable contexts.
        var queue: Deque<Context> = [src]
        var seenNodes = Set<Context>([src])

        while !queue.isEmpty {
            let currentNode = queue.popFirst()!

            // Get all possible edges from here on and push all unseen target nodes to the queue.
            for edge in self.edges
            where edge.key.from.isSubset(of: currentNode) && !seenNodes.contains(edge.key.to) {
                seenNodes.insert(edge.key.to)
                queue.append(edge.key.to)
            }
        }
        return seenNodes
    }

    // The return value is a list of possible Paths.
    // A Path is a possible way from one context to another.
    public func getShortestCodeGeneratorPath(from src: Context, to dst: Context) -> Path? {

        guard let path = getShortestPath(from: src, to: dst) else {
            return nil
        }

        return Path(
            edges: path.map { edge in
                self.edges[edge]!
            })
    }
}
