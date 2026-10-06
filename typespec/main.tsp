import "@typespec/http";
import "@typespec/openapi";
import "@typespec/openapi3";
import "./common.tsp";
import "./models.tsp";
import "./routes.tsp";

using Http;
using OpenAPI;

/**
 * Draft API for collaborative astrophotography. A telescope's software signs in, describes
 * the rig, asks what to shoot tonight and reports what it captured. This version adopts the
 * protocol Starfront already speaks. See spec/protocol.md for the rules behind each route.
 */
@service(#{ title: "AstroCollab API" })
@info(#{ version: "0.2.0-draft.1", license: #{ name: "MIT", identifier: "MIT" } })
@server("https://collab.example", "Reserved example; use the address of the server you join.")
@useAuth(bearerAuth)
namespace AstroCollab;

/**
 * Send `Authorization: Bearer <token>`. Telescope routes take a telescope's agent token;
 * account routes take a person's token from sign-in. The two are never interchangeable.
 */
model bearerAuth is BearerAuth;
