import Foundation

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
        // In CI environments, PostgreSQL is always available on Linux runners
        if ProcessInfo.processInfo.environment["DATABASE_URL"] != nil {
            return true
        }
        
        // For local testing, assume it's available
        return true
    }
}
