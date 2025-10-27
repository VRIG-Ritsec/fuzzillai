import Foundation
@testable import Fuzzilli

/// Utility functions for PostgreSQL tests
public class PostgreSQLTestUtils {
    
    /// Get the PostgreSQL connection string for tests
    /// Uses DATABASE_URL environment variable if available, otherwise falls back to local Docker setup
    public static func getConnectionString() -> String {
        return ProcessInfo.processInfo.environment["DATABASE_URL"] ?? 
               "postgresql://fuzzilli:fuzzilli123@localhost:5433/fuzzilli"
    }
    
    /// Check if PostgreSQL is available for testing
    public static func isPostgreSQLAvailable() -> Bool {
        // Try to create a database pool and test the connection
        do {
            let connectionString = getConnectionString()
            let databasePool = DatabasePool(connectionString: connectionString)
            
            // Try to initialize the pool
            let semaphore = DispatchSemaphore(value: 0)
            var isAvailable = false
            
            Task {
                do {
                    try await databasePool.initialize()
                    let connected = try await databasePool.testConnection()
                    isAvailable = connected
                    await databasePool.shutdown()
                } catch {
                    isAvailable = false
                }
                semaphore.signal()
            }
            
            // Wait for the async operation to complete (with timeout)
            let result = semaphore.wait(timeout: .now() + 5.0)
            return result == .success && isAvailable
            
        } catch {
            return false
        }
    }
}
