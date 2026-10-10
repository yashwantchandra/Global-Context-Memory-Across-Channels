/**
 * Agent state tracking types
 */
/**
 * Possible states of the conversation agent
 */
export var AgentState;
(function (AgentState) {
    /** Agent is not connected */
    AgentState["IDLE"] = "idle";
    /** Agent is connecting to the server */
    AgentState["CONNECTING"] = "connecting";
    /** Agent has connected but not yet ready */
    AgentState["CONNECTED"] = "connected";
    /** Agent is listening for user input */
    AgentState["LISTENING"] = "listening";
    /** Agent is speaking/responding */
    AgentState["SPEAKING"] = "speaking";
    /** Agent encountered an error */
    AgentState["ERROR"] = "error";
})(AgentState || (AgentState = {}));
//# sourceMappingURL=state.js.map