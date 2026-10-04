using Http;
using OpenAPI;

namespace AstroCollab;

model Problem {
  type: url;
  title: Text;
  @minValue(400) @maxValue(599) status: integer;
  code: Code;
  detail: Text;
  request_id: Uuid;
  @minItems(0) @maxItems(100) errors?: {
    pointer: Text;
    code: Text;
  }[];
}
