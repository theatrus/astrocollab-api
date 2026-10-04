// Rigs and asking for work. A rig belongs to the account, so it registers once and
// asks once for work across all the account's projects.
using Http;
using OpenAPI;

namespace AstroCollab;

/** A registered rig. Each paired installation usually registers one. */
model Equipment {
  id: Uuid;
  account_id: Uuid;

  /** Rises when the configuration changes. Plans and submissions cite it. */
  @minValue(1) revision: integer;

  observed_at: Timestamp;
  configuration: EquipmentInput;
}

model EquipmentPage {
  @minItems(0) @maxItems(100) items: Equipment[];
  @maxLength(4096) next_cursor: string | null;
}

/** Frames captured for one panel and not yet submitted. */
model UnsubmittedCaptures {
  panel_id: Uuid;
  @minValue(0) frames: integer;
  @minValue(0) integration_seconds: numeric;

  /** When the most recent of these frames was captured. */
  last_captured_at?: Timestamp;
}

/** Ask what this rig should image. */
model Checkin {
  equipment_id: Uuid;

  /** Limit the choice to these projects. By default the server considers every project you are active in. */
  @extension("uniqueItems", true) @minItems(1) @maxItems(100) project_ids?: Uuid[];

  /** The assignment the rig is working on, if any. */
  assignment_id?: Uuid;

  /**
   * Everything this rig has captured but not yet submitted, as totals per panel. Each
   * report replaces the rig's last one; send `[]` once all is submitted, or omit the
   * field to leave the last report as it is. For a masters project, this is how the
   * server learns of subs waiting to be stacked.
   */
  @minItems(0) @maxItems(100) unsubmitted_captures?: UnsubmittedCaptures[];

  observed_at: Timestamp;
}

/**
 * `image` carries a new assignment. `continue` means keep working on the current one.
 * `wait` means nothing suits this rig now; `reason_codes` say why, such as
 * `rig_incomplete`, `no_matching_filter` or `goals_met`.
 */
@extension("allOf", #[
  #{
    `if`: #{properties: #{action: #{`const`: "image"}}},
    `then`: #{required: #["assignment"]},
    `else`: #{not: #{required: #["assignment"]}}
  }
])
model CheckinResult {
  action: "image" | "continue" | "wait";

  /** Check in again after this many seconds. */
  @minValue(30) @maxValue(86400) next_checkin_seconds: integer;

  @minItems(0) @maxItems(32) reason_codes: Code[];
  assignment?: Assignment;
}

/** Create the rig, or replace its description. An identical body keeps the revision. */
@summary("Register or update a rig")
@tag("rigs")
@put @route("/me/equipment/{equipment_id}")
op registerEquipment(
  @path equipment_id: Uuid,
  @body body: EquipmentInput
): Ok<Equipment> | Errors;

/**
 * Change only the fields sent, using JSON merge patch (RFC 7396): `null` removes a field.
 * Lets a rig report what it knows without erasing what the user entered on the web.
 */
@summary("Update part of a rig")
@tag("rigs")
@patch(#{ implicitOptionality: false })
@route("/me/equipment/{equipment_id}")
op updateEquipment(
  @path equipment_id: Uuid,
  @header contentType: "application/merge-patch+json",
  @body body: MergePatchUpdate<EquipmentInput>
): Ok<Equipment> | Errors;

@summary("Read a rig")
@tag("rigs")
@get @route("/me/equipment/{equipment_id}")
op getEquipment(
  @path equipment_id: Uuid
): Ok<Equipment> | Errors;

@summary("List the account's rigs")
@tag("rigs")
@get @route("/me/equipment")
op listEquipment(...Paging): Ok<EquipmentPage> | Errors;

/**
 * The server picks the project and panel where this rig adds most, using everything the rig
 * advertises, and assigns it. Repeating a check-in is safe and creates nothing new.
 */
@summary("Ask what this rig should image")
@tag("rigs")
@post @route("/me/checkins")
op checkIn(@body body: Checkin): Ok<CheckinResult> | Errors;
