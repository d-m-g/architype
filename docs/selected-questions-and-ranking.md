Selected-object questions
=========================

Click a detection, choose **Ask about this**, then enter a question. The composer shows a removable scope chip.
The request includes the source id, type and page; history displays the readable name and retains the scope for
correction replays. Reload restores the pending scope. Clearing the chip returns to whole-drawing questions.

The planner and agent select one object with `where.entity_id`; room contents use `where.connected_room_id`.
Measurements still require recorded quantities. Selecting an image object does not manufacture dimensions.
This binding is interpreted by the planner, like the rest of a question; interpretation is not independently verified.

Rankings with incomplete connections
===================================

`room_ranking.rank` gives each room a lower bound (confirmed qualifying connections) and upper bound (lower plus
every unresolved qualifying object that could connect to it). A strict winner must beat every other room's upper
bound for "most", or stay below every other room's lower bound for "fewest". Exact ties are reported together;
overlapping uncertain ranges are not treated as ties. Empty/malformed candidate lists mean unknown on that page.
Excluded legends and below-cutoff detections retain the existing calculation policy.

For example, 6 confirmed doors versus 4 with one unknown proves the leader: the competitor can reach only 5.
Two unknown doors allow 6–8 versus 4–6, so there is no unique verified winner.
The winner's own count can remain a range even when its identity is certain.

The exact whole-drawing English question shape takes a free CPU shortcut. Other wording retains the planner/agent
route; the agent has the same free `rank_rooms` tool and final rankings are recomputed by the server. This tool is
not for apartment-scoped rankings. Missing relationships entirely preserve the existing fallback. Ambiguous raster
rankings attempt the existing cost-bounded, cached room-link review once; proved rankings spend nothing on models.

These are rankings of qualifying *detected* objects, not a guarantee of detector recall or calibrated correctness.
The source support score, 60% provisional cutoff, included ids and unresolved source ids remain inspectable.

Validation: offline interval regressions, selected-id executor tests, mocked agent validation, scoped API request
and reload tests. Cached Swiss PNG replay proves Couloir 4.05 with seven qualifying doors without a new model call.
