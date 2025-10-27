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
        // In CI environments, we assume PostgreSQL is available if DATABASE_URL is set
        if ProcessInfo.processInfo.environment["DATABASE_URL"] != nil {
            return true
        }
        
        // For local testing, we could add more sophisticated checks here
        // For now, we'll assume it's available if we're running tests
        return true
    }
}
