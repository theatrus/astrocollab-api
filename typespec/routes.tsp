// The routes. Paths are Starfront's, under /api/v1.
using Http;
using OpenAPI;

namespace AstroCollab;

// ---------------------------------------------------------------------------
// Discovery
// ---------------------------------------------------------------------------

/** Whether the server is up, which protocol it speaks, and what it offers. */
model Health {
  ok: boolean;

  /** Protocol version, an integer. This draft is protocol 1. */
  protocol: int32;

  time: UnixTime;

  /** The server's own build version. */
  version: string;

  /** Optional parts this server offers, such as `signin`, `pairing` or `files`. */
  features?: string[];
}

@summary("Check the server and its protocol")
@useAuth(NoAuth)
@tag("discovery")
@get @route("/api/v1/health")
op health(): Ok<Health>;

// ---------------------------------------------------------------------------
// People: signing in and enrolling telescopes
// ---------------------------------------------------------------------------

/** Whether people can sign in here. */
model AuthStatus {
  /** True when sign-in is on. Starfront servers sign people in with Discord. */
  discord: boolean;

  /** The community people must belong to, if any. */
  guild?: string;

  /** Starting projects needs a role. */
  roleRequired?: boolean;

  publicUrl?: string;
}

/** A sign-in in progress: a code for the program to hold, and a page for the person to open. */
model LoginStarted {
  code: string;

  /** Open this in the browser. */
  url: string;

  /** Seconds the code stays good. */
  expiresIn: int32;
}

model SignedInUser {
  id: string;
  name: string;
  avatar?: string;
  admin: boolean;

  /** May start projects on this server. */
  canStart: boolean;
}

/** `done` hands over the person's token, once. */
model LoginPoll {
  state: "pending" | "done" | "claimed" | "expired";
  token?: string;
  user?: SignedInUser;
}

model Me {
  id: string;
  name: string;
  admin: boolean;
  canStart: boolean;
}

model SignedOut {
  signedOut: boolean;
}

model EnrolRequest {
  /** What the owner calls this telescope. */
  @minLength(1) @maxLength(80) name: string;

  @maxLength(80) owner?: string = "";
}

/** A telescope's credential. The token is shown once; keep it on that telescope. */
model TelescopeCreated {
  agent: Telescope;
  token: string;
}

model TelescopeList {
  agents: Telescope[];
}

/** Trade a pairing code from the server's web pages for a telescope token. */
model PairRequest {
  /** The single-use code the person issued. */
  @minLength(1) @maxLength(256) code: string;

  /** What to call this telescope. */
  @minLength(1) @maxLength(80) name: string;
}

@summary("Can people sign in here")
@useAuth(NoAuth)
@tag("signin")
@get @route("/api/v1/auth")
op authStatus(): Ok<AuthStatus>;

/**
 * Starts a device sign-in. The program opens `url` in the browser and polls `authPoll`
 * with `code` until the person has signed in there.
 */
@summary("Start signing a person in")
@useAuth(NoAuth)
@tag("signin")
@post @route("/api/v1/auth/login")
op authLogin(): Ok<LoginStarted> | Unavailable;

@summary("Wait for the person to finish signing in")
@useAuth(NoAuth)
@tag("signin")
@get @route("/api/v1/auth/poll")
op authPoll(@query @maxLength(64) code: string): Ok<LoginPoll>;

@summary("Who is signed in")
@tag("signin")
@get @route("/api/v1/auth/me")
op authMe(): Ok<Me> | Errors;

@summary("Sign out")
@tag("signin")
@post @route("/api/v1/auth/logout")
op authLogout(): Ok<SignedOut> | Errors;

/** Takes a person's token. Each telescope gets its own token, shown once. */
@summary("Enrol a telescope")
@tag("telescopes")
@post @route("/api/v1/agents")
op enrolTelescope(@body body: EnrolRequest): Ok<TelescopeCreated> | Errors | Unavailable;

/** Takes a person's token. Lists the person's own telescopes. */
@summary("List your telescopes")
@tag("telescopes")
@get @route("/api/v1/agents")
op listTelescopes(): Ok<TelescopeList> | Errors | Unavailable;

/**
 * The other way to get a telescope token, for servers that list the `pairing` feature. The
 * person issues a single-use code on the server's web pages and types it into the program.
 * Takes no token. Never retry on your own: a lost reply needs a new code.
 */
@summary("Pair a telescope with a code")
@useAuth(NoAuth)
@tag("telescopes")
@post @route("/api/v1/pair")
op pairTelescope(@body body: PairRequest): Ok<TelescopeCreated> | Unauthorized | Invalid | NotFound | TooManyRequests;

// ---------------------------------------------------------------------------
// The telescope: hello, browse, join, tonight, report
// ---------------------------------------------------------------------------

model HelloRequest {
  /** The protocol the program speaks. */
  protocol?: int32 = 1;

  profile?: RigProfile;

  /** Where the telescope points, for the others' charts. Optional. */
  presence?: Presence | null;
}

model HelloResponse {
  agent: Id;
  name: string;
  protocol: int32;
  serverTime: UnixTime;
}

model PresenceResponse {
  telescopes: TelescopePresence[];

  /** Telescopes online now. */
  online: int32;

  /** People with a telescope online now. */
  people: int32;

  onlineSeconds: int32;
  serverTime: UnixTime;
}

model OpenProjects {
  projects: OpenProject[];
  protocol: int32;
}

model JoinRequest {
  /** Hours per filter to give, capped by the project's goal. 0 takes the goal. */
  @minValue(0) @maxValue(24) hours?: float64 = 0;

  /** Sub length for a filter with no entry in `exposures`. 0 takes the middle of the project's range. */
  @minValue(0) @maxValue(3600) exposure?: float64 = 0;

  /** The rig's own sub length per filter, seconds. */
  exposures?: Record<float64>;

  /** The night the rig is in, so the first deal is tonight's. */
  night?: NightName = "";

  /** How much of the Moon is lit tonight, 0–1. */
  @minValue(0) @maxValue(1) moon?: float64 | null;

  /** The fraction of the dark hours the Moon is up, 0–1. */
  @minValue(0) @maxValue(1) moonUp?: float64 | null;
}

model JoinResponse {
  task: Task | null;
  requirements: Requirements;

  /** The rig already held a share; that one is returned. */
  alreadyJoined?: boolean;
}

/** Every share this telescope holds, each dealt for tonight. */
model TaskResponse {
  /** The first share, for programs that expect one. */
  task: Task | null;

  tasks: Task[];

  /** Each share's project requirements, so the program can judge its own data first. */
  requirementsByProject?: Record<Requirements>;

  /** The first share's version. */
  version: int32;

  protocol: int32;

  /** The first share's project requirements. */
  requirements?: Requirements;
}

model TaskStateRequest {
  state: "accepted" | "declined" | "complete";
}

model TaskEnvelope {
  task: Task;
}

model ReportRequest {
  /** One record per night, filter and panel. */
  @maxItems(200) contributions?: Contribution[];
}

model ReportResponse {
  /** In the order sent. */
  recorded: Recorded[];
}

/**
 * Says what this telescope is and, if it likes, where it points. Also a heartbeat. Send it
 * before asking for work and whenever the rig changes.
 */
@summary("Say hello and describe the rig")
@tag("telescope")
@post @route("/api/v1/agent/hello")
op hello(@body body: HelloRequest): Ok<HelloResponse> | Errors | Conflict;

/** Telescopes that checked in during the last day, where they point and what they do. */
@summary("See who is on the sky")
@tag("telescope")
@get @route("/api/v1/presence")
op presence(): Ok<PresenceResponse> | Errors;

/** Every open project, and whether this telescope can help each one. */
@summary("Browse open projects")
@tag("telescope")
@get @route("/api/v1/agent/projects")
op openProjects(): Ok<OpenProjects> | Errors;

/** Everybody's accepted depth on a fine grid over the region, per filter, and the progress it adds up to. */
@summary("See a project's depth map")
@tag("telescope")
@get @route("/api/v1/agent/projects/{project_id}/depth")
op projectDepth(@path project_id: Id): Ok<DepthMap> | Errors | NotFound | Conflict;

/**
 * Takes a share of a project, checked against the profile the rig last sent. The share is
 * the whole region tiled with this rig's own camera, at its own sub lengths. Joining twice
 * returns the share already held. Joining is consent: the share arrives accepted. Sending the
 * night and the Moon deals tonight's list at once, as `tonight` would. A rig that
 * has not described its optics gets 400; one that cannot meet the rules gets 409.
 */
@summary("Join a project")
@tag("telescope")
@post @route("/api/v1/agent/projects/{project_id}/join")
op joinProject(@path project_id: Id, @body body: JoinRequest): Ok<JoinResponse> | Errors | BadRequest | NotFound | Conflict;

/**
 * Tonight's work: each share with tonight's panels in `share` and tonight's filter and frames
 * in `visit`. A list holds for the whole night the rig names and is dealt afresh the first
 * time it asks in the next one. `moon` and `moonUp` describe the rig's own sky tonight and
 * decide whether it is a narrowband night. A version the rig already has means nothing new.
 */
@summary("Ask what to shoot tonight")
@tag("telescope")
@get @route("/api/v1/agent/task")
op tonight(
  /** The night the rig is in, as it names its nights, such as `2026-10-05`. */
  @query night?: NightName,

  /** How much of the Moon is lit tonight, 0–1. */
  @query @minValue(0) @maxValue(1) moon?: float64,

  /** The fraction of the dark hours the Moon is up, 0–1. */
  @query @minValue(0) @maxValue(1) moonUp?: float64
): Ok<TaskResponse> | Errors;

/** Accept, decline or finish a share. Only needed for shares a coordinator offered. */
@summary("Accept, decline or finish a share")
@tag("telescope")
@post @route("/api/v1/agent/task/{task_id}")
op setTaskState(@path task_id: Id, @body body: TaskStateRequest): Ok<TaskEnvelope> | Errors | NotFound;

/**
 * Hands back what was shot, one record per night, filter and panel, and hears whether each
 * counts. Reporting the same night, filter and panel again keeps the larger figure.
 */
@summary("Report what was shot")
@tag("telescope")
@post @route("/api/v1/agent/report")
op report(@body body: ReportRequest): Ok<ReportResponse> | Errors | Forbidden;
