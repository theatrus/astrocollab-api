import "./access.js";

namespace AstroCollab;

/**
 * Credential context and default scopes for an operation.
 * Emits `x-token-context` and `x-required-scopes`.
 */
extern dec access(target: Reflection.Operation, context: valueof string, ...scopes: valueof string[]);
