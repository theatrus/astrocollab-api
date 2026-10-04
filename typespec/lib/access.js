import { setExtension } from "@typespec/openapi";

// @access(context, ...scopes) records which credential an operation accepts
// and the scopes it needs. See spec/authentication.md.
export const namespace = "AstroCollab";

export function $access(context, target, tokenContext, ...scopes) {
  setExtension(context.program, target, "x-token-context", tokenContext);
  setExtension(context.program, target, "x-required-scopes", scopes);
}
