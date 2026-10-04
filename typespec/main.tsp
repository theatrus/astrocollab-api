import "@typespec/http";
import "@typespec/openapi";
import "@typespec/openapi3";
import "./common.tsp";
import "./problem.tsp";
import "./capabilities.tsp";
import "./pairing.tsp";
import "./projects.tsp";
import "./rigs.tsp";
import "./planning.tsp";
import "./submissions.tsp";

using Http;
using OpenAPI;

/**
 * Draft API for collaborative astrophotography. See spec/protocol.md for resource states,
 * authorization and protocol requirements.
 */
@service(#{ title: "AstroCollab API" })
@info(#{ version: "0.1.0-draft.1", license: #{ name: "MIT", identifier: "MIT" } })
@server("https://collab.example/v1", "Reserved example; discover the real API root from the chosen service.")
@useAuth(bearerAuth)
namespace AstroCollab;

/**
 * Send `Authorization: Bearer <api key>`. Each client installation gets its key by pairing.
 * A key acts for its account, optionally limited to some projects. See
 * spec/authentication.md.
 */
model bearerAuth is BearerAuth;
