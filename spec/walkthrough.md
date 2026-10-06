# Payload walkthrough

This page goes through every example payload in order. They were captured from
Starfront's server driving one night, by `tools/capture_starfront_examples.py`,
so they are what a real server sends. For a shorter tour with requests, read
[How a night works](overview.md). The [example index](../examples/manifest.json)
maps each file to its type.

## Discover the server

[Health](../examples/health.response.json) gives the protocol number, 1, the
server's build, and `features`: this server offers sign-in. [Sign-in status](../examples/authStatus.response.json) says
sign-in is on; this server signs people in with Discord.

## Sign in and enrol

The program [asks for a login code](../examples/authLogin.response.json) and opens
its page in the browser. While the person signs in, [polling](../examples/authPoll.pending.response.json)
says `pending`; afterwards it [hands over the person's token](../examples/authPoll.done.response.json)
once. [Who is signed in](../examples/authMe.response.json) confirms it.

With that token the program [enrols the telescope](../examples/enrolTelescope.request.json)
and gets [its own token](../examples/enrolTelescope.response.json), shown once.
[Listing telescopes](../examples/listTelescopes.response.json) shows it, without
the token. [Pairing](../examples/extra/pairTelescope.request.json) is the other
way in, with the [same reply](../examples/extra/pairTelescope.response.json);
Starfront does not offer it yet, so those two files are written by hand, as is
[signing out](../examples/extra/authLogout.response.json).

## Say hello

The [hello](../examples/hello.request.json) describes "Vega 530": a 530 mm
telescope with a 6248×4176 mono camera of 3.76 µm pixels, about 1.46″ per pixel
and a field of 2.54°×1.70°. It carries 7 nm H-alpha, OIII and SII filters and a
luminance filter of unknown bandpass, shoots 300 s narrowband and 120 s
luminance subs to match its darks, usually reaches 2.4″ stars and 0.62″ guiding,
and gives six hours a night between 21:30 and 04:30. It shares where it points.
The [reply](../examples/hello.response.json) gives its ID and the server's time.

## Browse and join

[Open projects](../examples/openProjects.response.json) lists two:

- **M51 in LRGB**, a `single` project wanting 20 h of L and 5 h each of R, G and
  B. Vega 530 cannot help: it is longer than the project's 400 mm limit and has
  no colour filters. The listing says so, rule by rule.
- **M31 halo in narrowband**, a 7°×4.5° `mosaic` turned to 35°, wanting 10 h of
  H and of O at every point, with stars under 3.5″, subs of 120–600 s, 30° from
  the Moon and above 30° altitude. Vega 530 can help.

The [join](../examples/joinProject.request.json) sends the rig's own sub lengths,
and tonight's night and Moon so the first list is dealt at once. The
[reply](../examples/joinProject.response.json) is an `accepted` share: the
whole mosaic tiled with nine cells of Vega 530's field, turned to 35°, at 300 s
in H and O.

## Tonight

The program [asks for tonight](../examples/tonight.query.json) with the night's
name and a thin Moon (12% lit, up 30% of the dark hours). The
[reply](../examples/tonight.response.json) deals six of the nine cells, in order,
all in OIII, 11 subs of 300 s each: a dark night goes to the filter that cannot
be shot under a bright Moon. Asking again tonight returns the same list.

Shares a coordinator pushes arrive `offered`; the program
[accepts](../examples/setTaskState.request.json) one and gets it
[back](../examples/setTaskState.response.json). A share from joining needs no
answer.

## Report

The [report](../examples/report.request.json) gives two panels of OIII, each 11
frames of 300 s, with the solved footprint, 1.46″ per pixel, stars of 2.3″ and
2.5″, 0.58″ guiding and a 7 nm bandpass. Both are
[accepted](../examples/report.response.json).

The next night's [report](../examples/report.rejected.request.json) has stars of
4.98″, and is [rejected](../examples/report.rejected.response.json) against the
project's 3.5″ limit, with the reason in words.

## Presence

[Who is on the sky](../examples/presence.response.json): Vega 530 is online,
pointing at M31.

## Errors

A request with an [unknown token](../examples/errors/unknown-token.response.json)
gets `401`. A 1000 mm rig that tries to join M51 gets
[`409`](../examples/errors/cannot-join.response.json) with the reasons.
