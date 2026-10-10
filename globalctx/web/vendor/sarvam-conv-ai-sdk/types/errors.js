/**
 * Custom error types for the Sarvam Conversation AI SDK
 */
/**
 * Base error class for SDK errors
 */
export class SDKError extends Error {
    /** Error code for programmatic handling */
    code;
    /** HTTP status code if applicable */
    statusCode;
    constructor(message, code, statusCode) {
        super(message);
        this.name = "SDKError";
        this.code = code;
        this.statusCode = statusCode;
        // Maintains proper stack trace for where error was thrown (only in V8)
        if (Error.captureStackTrace) {
            Error.captureStackTrace(this, this.constructor);
        }
    }
}
/**
 * Error thrown when the API rate limit is exceeded.
 *
 * @example
 * ```typescript
 * try {
 *   await agent.start();
 * } catch (err) {
 *   if (err instanceof RateLimitError) {
 *     console.log(`Rate limited. Retry after ${err.retryAfter} seconds`);
 *     // Optionally wait and retry
 *     await new Promise(resolve => setTimeout(resolve, err.retryAfter * 1000));
 *     await agent.start();
 *   }
 * }
 * ```
 */
export class RateLimitError extends SDKError {
    /** Number of seconds to wait before retrying (from Retry-After header) */
    retryAfter;
    constructor(message, retryAfter = 60) {
        super(message, "RATE_LIMITED", 429);
        this.name = "RateLimitError";
        this.retryAfter = retryAfter;
    }
}
/**
 * Error thrown when authentication fails (invalid API key, etc.)
 */
export class AuthenticationError extends SDKError {
    constructor(message) {
        super(message, "AUTHENTICATION_FAILED", 401);
        this.name = "AuthenticationError";
    }
}
/**
 * Error thrown when the request is forbidden (insufficient permissions)
 */
export class ForbiddenError extends SDKError {
    constructor(message) {
        super(message, "FORBIDDEN", 403);
        this.name = "ForbiddenError";
    }
}
/**
 * Error thrown when a resource is not found
 */
export class NotFoundError extends SDKError {
    constructor(message) {
        super(message, "NOT_FOUND", 404);
        this.name = "NotFoundError";
    }
}
/**
 * Error thrown when the server encounters an internal error
 */
export class ServerError extends SDKError {
    constructor(message, statusCode = 500) {
        super(message, "SERVER_ERROR", statusCode);
        this.name = "ServerError";
    }
}
//# sourceMappingURL=errors.js.map