using Http;
using OpenAPI;

namespace AstroCollab;

/**
 * A filter on a rig. Each filter lists the passbands it lets through: one for a
 * narrowband or luminance filter, two for a dual-narrowband filter on a color camera,
 * three for an RGB or multi-band filter. Use a filter with no glass in the light path
 * as `kind: none` with the camera's own response as its passband.
 */
model Filter {
  id: Uuid;

  /** The user's name for it, such as "Ha 3 nm". */
  name?: Text;

  kind?: "luminance" | "rgb" | "narrowband" | "dual_narrowband" | "multi_band" | "broadband" | "light_pollution" | "uv_ir_cut" | "none";
  @minLength(1) @maxLength(80) manufacturer?: string;
  @minLength(1) @maxLength(80) `model`?: string;
  @minItems(1) @maxItems(8) bandpasses: Bandpass[];
}

model Site {
  @minValue(-90) @maxValue(90) latitude_degrees: numeric;
  @minValue(-180) @maxValue(180) longitude_degrees: numeric;
  @minValue(0) precision_km: numeric;
  timezone: Text;
}

/**
 * A rig's description. Only `name` is required: the user may enter some details on the
 * server's web pages and the rig others. To plan for a rig, the server needs the sensor
 * size, pixel size, focal length, color state and at least one filter; until then,
 * check-ins return `review_required` with reason `rig_incomplete`.
 */
model EquipmentInput {
  name: Text;

  /** Unbinned sensor size. Field of view does not depend on binning; sampling does. */
  @minValue(1) @maxValue(100000) sensor_width_pixels?: integer;

  @minValue(1) @maxValue(100000) sensor_height_pixels?: integer;
  @maxValue(1000) @minValueExclusive(0) pixel_size_um?: numeric;
  @maxValue(100000) @minValueExclusive(0) focal_length_mm?: numeric;

  /** Defaults to 1. */
  @minValue(1) @maxValue(16) binning_x?: integer;

  /** Defaults to 1. */
  @minValue(1) @maxValue(16) binning_y?: integer;

  color_state?: "mono" | "cfa" | "debayered_rgb";

  /** Filters the rig can use. A color camera with no filter lists one `kind: none` filter. */
  @minItems(1) @maxItems(100) filters?: Filter[];

  @minLength(1) @maxLength(80) camera?: string;
  @minLength(1) @maxLength(80) telescope?: string;

  /** Defaults to `fixed`. */
  rotation?: "fixed" | "manual" | "motorized";

  @minValue(0) @maxValueExclusive(360) confirmed_position_angle_degrees?: numeric;
  site?: Site;
  @extension("propertyNames", #{pattern: "^[a-z0-9]+(?:[.-][a-z0-9]+)+$"}) @extension("maxProperties", 32) extensions?: Record<unknown>;
}

model EquipmentRef {
  equipment_id: Uuid;
  @minValue(1) revision: integer;
}

/** A panel's place in the grid the server laid over a target. Columns run east to west. */
model PanelLayout {
  @minValue(1) @maxValue(100) columns: integer;
  @minValue(1) @maxValue(100) rows: integer;
  @minValue(1) @maxValue(100) column: integer;
  @minValue(1) @maxValue(100) row: integer;
}

/** One field to image: where to point, which filter, how long and how many frames. */
model Panel {
  id: Uuid;
  target_id: Uuid;

  /** The target's name, for display. */
  target_name?: Text;

  @minItems(1) @maxItems(100) objective_ids: Uuid[];
  /** The camera field to point at: center, size and angle. It fits the rig's field of view. */
  footprint: Footprint;

  /** Where this panel sits when the server splits the target into a grid. */
  layout?: PanelLayout;

  @minValue(0) @maxValue(0.9) overlap_fraction: numeric;
  equipment: EquipmentRef;
  filter_id: Uuid;
  @maxValue(86400) @minValueExclusive(0) exposure_seconds: numeric;
  @minValue(1) suggested_frames: integer;
}

/**
 * What a rig should image: the panels the server hands out, in order. Image the first;
 * move to the next when it has its suggested frames or can no longer be imaged.
 */
model Assignment {
  id: Uuid;
  project_id: Uuid;

  /** The requirements revision the frames will be judged against. */
  @minValue(1) project_revision: integer;

  created_at: Timestamp;

  /** Stop starting new frames for this assignment after this time. */
  expires_at: Timestamp;

  @minItems(1) @maxItems(10) panels: Panel[];
  @minValue(0) estimated_rig_seconds?: numeric;
  @minValue(0) estimated_accepted_integration_seconds?: numeric;

  /** Plain notes for the user, such as why a target was chosen. */
  @minItems(0) @maxItems(16) notes?: Text[];
}
