/**
 * Browser-specific entry point for Sarvam Conv AI SDK
 *
 * This module exports only browser-compatible components.
 * Use this when building for web/browser environments to avoid bundling Node.js dependencies.
 *
 * @example
 * ```typescript
 * import { ConversationAgent, BrowserAudioInterface } from 'sarvam-conv-ai-sdk/browser';
 * ```
 */
// Core conversation agent
export { ConversationAgent, createConversation } from "./conversation";
// Browser audio interface
export { BrowserAudioInterface } from "./interfaces/browser";
export { AgentState, InteractionType } from "./types";
// Error classes (exported as values for instanceof checks)
export { SDKError, RateLimitError, AuthenticationError, ForbiddenError, NotFoundError, ServerError, } from "./types";
//# sourceMappingURL=browser.js.map