# Using OpsDeck

A tour of what each tab does. Setup is in the [README](../README.md).

## GitHub

A **feed** follows one organization, whole or a list of its repositories, optionally limited to some kinds of events (pushes, pull requests, reviews, comments, issues, branches, releases).

### Timeline

Each day opens with a histogram of its activity per half hour and a strip of who contributed, busiest first. Hovering a person shows what they did (kinds, repositories, first and last event) and highlights their share of the histogram; clicking filters the timeline to them. Events the GitHub events API has not published yet (it can lag by hours) come from a search and carry a ⚡.

Clicking an event shows its details; **Full context →** opens a side panel with the whole text of the comment, review, issue, pull request or release, rendered like on GitHub, and the conversation around it: the whole thread of a review comment (with the code it refers to), the line comments of a review, and the size, branches, labels and reviewers of a pull request. With the panel open, clicking another event or pressing ↑/↓ moves to it, and Esc closes it.

### Contributors

The **contributors** switch shows the feed's daily volume per person: one row per person, one column per day for the last 2, 4 or 8 weeks, under the team's volume per day. It counts commits that reached a default branch, pull requests and issues opened, and reviews of other people's pull requests (a person's reviews of one pull request count once per day), read from the repositories, so it reaches weeks back. Pushes to other branches and comments are not counted.

Kind and repository chips filter the grid, and hovering a day or the bar at the end of a row shows the breakdown. Clicking a day lists what the person did, each item linked to GitHub, and opens the day in the timeline filtered by person and day (or in GitHub search, for days the timeline no longer has). The first read of a whole organization takes several seconds; it is cached for ten minutes.

### Network

The **network** switch draws the same contributions as a map: repositories are the anchors (size = activity in the period) and each person is linked to the repositories they contributed to, the link's width showing how much they did there and its length how large a share of their work it was. People linked to two or more repositories are *bridges*, and a repository where one person made 60 % or more of the work is *concentrated*; the side panel lists both, and shows a person's repositories or a repository's people when a node is hovered or pinned with a click.

A slider hides a person's minor repositories (under 5 % of their work by default), nodes can be dragged into place (double-click releases one) and the drawing zooms with ctrl + scroll or the buttons in its corner.

### Notifications

Hover a feed and click 🔔 to get desktop notifications of its new events. The browser asks for permission once and a test notification confirms they reach the desktop (if it does not show up, check the system's do-not-disturb settings for the browser). Notifications arrive while OpsDeck is open in some tab, even in the background, and clicking one opens the feed. Browsers only allow them on `localhost` or HTTPS.

## Logs

Logs are read from **sources**, listed under the saved filters: a source is a Google Cloud project for now, and each filter runs on one. Clicking a severity, a resource or a field of an entry refines the results, **Save as new filter** keeps the refined query, and clicking an entry's trace shows every entry of the same request, optionally across the other sources.

### Watched filters

The eye button of a filter **watches** it: in the background the app counts the entries that arrived since you last opened the filter and shows them on its card, one chip per severity, with the total on the Logs tab.

- Clicking a chip shows exactly those entries, even when they are older than the filter's window.
- Opening the filter marks the new entries with a dot. When some are older than its window, or more than its limit, a bar above the results says so and leads to them.
- The count starts over once a view has shown every new entry, or when you click **✓ mark seen**.

Each round reads only the minutes since the previous one, one API call per watched filter (Cloud Logging does not bill reads; its quota is 60 per minute per project). A round that reaches 1000 entries shows the number as a lower bound, like `1000+`.

## Background updates

Feed badges, notifications, the auto refresh of the open feed (**◷ auto**) and the count of watched filters run in the background. A thin line under the feed list, the filter list and the open feed fills up until the next update; hover it to see when that is, and click it to choose how often (every 1 to 15 min; the open feed every 30 s to 5 min).

The **● live** switch at the top of the feed list pauses all of it: the lines turn dashed, and feeds and filters only update when you open them or click Refresh. These choices are saved with the theme.

## The app

OpsDeck can be installed as an app in Chrome, Edge or other Chromium browsers: open it on `localhost` or over HTTPS and use the install icon in the address bar. The installed app runs in its own window and shows the count of unseen GitHub events on its icon. It caches nothing, so the server must be running; when it is not, the app shows a notice instead of an error page.

A reload reopens the tab you were on. Cards, filters and feeds can be exported and imported with the ⇅ button of each tab.
